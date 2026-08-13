"""LangChain LLM 客户端复用层。

统一封装：
1. ChatOpenAI 客户端：复用项目已有的 VISION_* 配置（阿里云百炼 Qwen 兼容模式）。
    与 similar_generator 同理，出题这类长 JSON 输出固定走 Qwen 视觉模型组，
    避免主链路 DeepSeek 思考模型的推理 token 抢占 max_tokens 导致输出被截断。
    使用 lru_cache 缓存实例，避免每次调用新建 httpx 连接池导致 FD 泄漏。
2. 结构化 JSON 请求（带重试 + 双路径解析）：
    - 首选 with_structured_output（LangChain 原生结构化输出）；
    - 兜底 raw invoke + response_format(json_object) + 手动解析，
      兼容部分兼容端点不支持 function-calling 结构化输出的情况。

本模块是 question_pipeline 内唯一直接触达 LLM 的地方，其它节点不得直接 new OpenAI client。
"""

import json
import logging
from functools import lru_cache
from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Pydantic 响应模型类型变量（用于结构化输出泛型）
T = TypeVar("T", bound=BaseModel)


@lru_cache(maxsize=8)
def chat_model(max_tokens: int = 4096, temperature: float = 0.6) -> BaseChatModel:
    """创建 LangChain ChatOpenAI 客户端（按 max_tokens + temperature 缓存），复用 VISION_* 配置。"""
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.VISION_API_KEY,
        base_url=settings.VISION_API_BASE,
        model=settings.VISION_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=120,
        # 显式关闭原生 function-calling 结构化输出，走 json_mode，
        # 兼容阿里云百炼 compatible-mode 端点（实测 with_structured_output(method="json_mode") 更稳）
    )


def _looks_like_schema_echo(data) -> bool:
    """检测模型是否把 JSON Schema 原样回显当成了回答。

    阿里云百炼 compatible-mode 端点在 json_mode 下偶发行为：模型把提示词里
    嵌入的输出 schema（含 $defs / properties / question_content 等键）原样
    回显，而不是输出真实数据。这类响应必须快速失败并重试，避免白白消耗
    重试轮数和超时时间。
    """
    if not isinstance(data, dict):
        return False
    # JSON Schema 的典型特征：顶层出现 $defs / $schema，或"数据里混入 schema 字段名"
    if "$defs" in data or "$schema" in data:
        return True
    return any(key in data for key in ("question_content", "properties", "type")) and "questions" not in data


async def structured_json(
    prompt: str,
    response_model: type[T],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.6,
    attempts: int = 3,
) -> T | None:
    """请求 LLM 并返回结构化 JSON（Pydantic 模型），带重试与双路径解析。

    可靠性设计（对齐 similar_generator._request_json 的经验）：
    - 主路径：raw invoke + response_format(json_object)，这是本项目在多模态端点
      上验证过最稳的方式；解析失败（含 schema 回显）立即重试；
    - 兜底路径：with_structured_output(json_mode)，同一 attempt 内主路径失败后才尝试；
    - 空正文 / JSON 解析失败 / 调用异常 均视为一次失败并重试。
    - 注意：每次 attempt 会依次尝试主路径和兜底路径，任一成功即返回；
      全部 attempts 失败才返回 None。
    """
    settings = get_settings()
    if not settings.VISION_API_KEY:
        logger.warning("VISION_API_KEY not configured")
        return None

    last_err: Exception | None = None

    for attempt in range(attempts):
        # ── 主路径：raw invoke + json_object ──
        try:
            raw = await chat_model(max_tokens=max_tokens, temperature=temperature).ainvoke(
                prompt,
                response_format={"type": "json_object"},
            )
            content = (getattr(raw, "content", "") or "").strip()
            if not content:
                last_err = ValueError("LLM 返回空正文（思考模型 token 预算耗尽）")
                logger.warning("LLM returned empty content (attempt %d)", attempt + 1)
                continue
            # 兼容模型偶尔多包一层 ```json ... ``` 围栏
            if content.startswith("```"):
                parts = content.split("```")
                content = parts[1] if len(parts) >= 3 else parts[-1]
                content = content.strip()
            data = json.loads(content)
            # schema 回显 → 快速失败重试
            if _looks_like_schema_echo(data):
                last_err = ValueError("模型回显了 JSON Schema 而非数据")
                logger.warning("模型回显 JSON Schema，重试 (attempt %d)", attempt + 1)
                continue
            return response_model.model_validate(data)
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            last_err = e
            logger.warning("LLM JSON 解析失败，重试 (attempt %d): %s", attempt + 1, e)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM 调用失败，重试 (attempt %d): %s", attempt + 1, e)

        # ── 兜底路径：with_structured_output(json_mode) ──
        try:
            model = chat_model(max_tokens=max_tokens, temperature=temperature)
            structured = model.with_structured_output(response_model, method="json_mode")
            result = await structured.ainvoke(prompt)
            if result is not None:
                return result
            last_err = ValueError("with_structured_output 返回空结果")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("with_structured_output 兜底失败 (attempt %d): %s", attempt + 1, e)

    logger.error("结构化 JSON 请求最终失败: %s", last_err)
    return None

