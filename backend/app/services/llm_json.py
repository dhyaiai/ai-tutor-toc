"""
LLM JSON 请求统一封装（带重试 + 容错解析）。

背景：deepseek-v4-flash 等思考型模型会在 max_tokens 预算内先输出大量推理 token，
导致正文 JSON 被截断（json.JSONDecodeError）或完全为空。这里把
「空正文 / JSON 解析失败 / 调用异常」视为一次失败并重试，显著提高结构化输出成功率。

similar_generator._request_json 与 agent/tools.py 的学习计划生成原先各自实现
一份「调用 + 解析 + 重试」，统一收敛到本模块，后续新增 LLM JSON 调用点直接复用。
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.text_clean import sanitize_llm_controls

logger = logging.getLogger(__name__)


@dataclass
class LLMJsonResult:
    """一次 LLM JSON 请求的结果（成功或全部重试失败后返回）。"""

    data: dict | None  # 解析成功的 JSON；全部失败为 None
    raw_text: str      # 最后一次尝试的模型原始输出（可能为空，供调用方兜底展示）
    error: str         # 最后一次失败的原因描述（调用成功时为空字符串）


async def request_llm_json(
    client: Any,
    *,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float = 0.6,
    timeout: int = 120,
    attempts: int = 2,
    retry_delay: float = 0.0,
    response_format: dict | None = None,
    extract_braces: bool = False,
    extra_body: dict | None = None,
) -> LLMJsonResult:
    """
    请求 LLM 并解析 JSON 响应（带重试）。

    :param client: OpenAI 兼容 AsyncClient（openai.AsyncOpenAI）
    :param response_format: 传入 {"type": "json_object"} 要求模型严格输出 JSON（部分部署不支持时省略）
    :param extract_braces: 模型可能附带多余文本时，提取首个 { 到末尾 } 再解析（容错更强）
    :param attempts: 总尝试次数（含首次调用；如 2 = 首次 + 1 次重试）
    :param retry_delay: 每次失败后的等待秒数（用于避开瞬时限流）
    :param extra_body: 透传给网关的非 OpenAI 标准参数（如 {"enable_thinking": False}
        关闭 qwen3 系列思考模式，避免推理 token 抢占 max_tokens 导致正文截断/空壳）
    """
    last_err: Exception | None = None
    raw_text = ""
    for attempt in range(attempts):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
            }
            if response_format:
                kwargs["response_format"] = response_format
            if extra_body:
                kwargs["extra_body"] = extra_body
            resp = await client.chat.completions.create(**kwargs)
            content = (resp.choices[0].message.content or "").strip()
            raw_text = content
            if not content:
                # 思考型模型可能把全部预算消耗在推理上，正文为空
                last_err = ValueError("LLM 返回空内容")
                logger.warning("LLM returned empty content (attempt %d)", attempt + 1)
            elif extract_braces:
                # 容错解析：用 raw_decode 匹配第一个完整 JSON 对象（正确处理嵌套大括号）
                start_idx = content.find("{")
                if start_idx == -1:
                    raise json.JSONDecodeError("no braces in content", content, 0)
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(content, start_idx)
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("expected object", content, start_idx)
                # 清洗模型误转义的控制字符（\b → 退格符 → 还原为 LaTeX 反斜杠）
                data = sanitize_llm_controls(data)
                return LLMJsonResult(data=data, raw_text=content, error="")
            else:
                data = json.loads(content)
                # 清洗模型误转义的控制字符（\b → 退格符 → 还原为 LaTeX 反斜杠）
                data = sanitize_llm_controls(data)
                return LLMJsonResult(data=data, raw_text=content, error="")
        except json.JSONDecodeError as e:
            # 输出被 max_tokens 截断时抛出，属可重试的临时失败
            last_err = e
            logger.warning("LLM JSON 解析失败，重试 (attempt %d): %s", attempt + 1, e)
        except Exception as e:
            last_err = e
            logger.warning("LLM 调用失败，重试 (attempt %d): %s", attempt + 1, e)

        if retry_delay > 0 and attempt < attempts - 1:
            await asyncio.sleep(retry_delay)

    logger.error("LLM JSON 请求最终失败: %s", last_err)
    return LLMJsonResult(data=None, raw_text=raw_text, error=str(last_err or ""))
