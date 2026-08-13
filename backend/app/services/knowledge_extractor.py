"""
知识点提取服务。

从 AI 评分分析文本中提取结构化知识点，可选调用大模型做精细化提取。
"""

import json
import logging
from dataclasses import dataclass

from app.services.text_clean import sanitize_llm_controls

logger = logging.getLogger(__name__)


@dataclass
class KnowledgePoint:
    name: str
    category: str | None  # e.g., "计算", "应用", "概念"
    mastery: str | None  # "mastered" | "partial" | "weak"


EXTRACTION_PROMPT = """你是一位教育数据分析专家。请从以下题目分析文本中提取知识点。

返回 JSON：
{
  "knowledge_points": [
    {
      "name": "知识点名称",
      "category": "分类（计算/应用/概念/记忆）",
      "mastery": "掌握程度（mastered/partial/weak）"
    }
  ]
}

只提取明确涉及的知识点，不要猜测。"""


TRIM_PROMPT = """你是一位教育内容分析专家。请对以下题目涉及的知识点列表进行精简合并。

要求：
1. 将相似、重复或过于细分的知识点合并为更核心的概念
2. 最多保留 {max_count} 个最能概括本题考查重点的知识点
3. 优先保留题目真正考查的学科核心概念，去掉泛泛而谈或仅作为背景的细枝末节
4. 返回 JSON：{{"knowledge_points": ["知识点1", "知识点2", ...]}}

题目信息：{context}

知识点列表：
{knowledge_points}
"""


class KnowledgeExtractor:
    """
    知识点提取器。

    支持两种模式：
    1. 规则模式：从已有分析文本中提取显式标注的知识点
    2. LLM 模式：调用大模型做精细化提取（仅在必要时使用）
    """

    def __init__(self):
        from app.core.config import get_settings
        settings = get_settings()
        self.llm_enabled = bool(settings.LLM_API_KEY)

    async def extract(self, analysis_text: str) -> list[dict]:
        """
        从分析文本中提取知识点。

        Args:
            analysis_text: AI 评分返回的分析文本

        Returns:
            [{"name": "分数加减法", "category": "计算", "mastery": "weak"}, ...]
        """
        # Rule-based extraction: look for explicit patterns
        knowledge_points = self._rule_based_extract(analysis_text)

        # If insufficient, use LLM
        if not knowledge_points and self.llm_enabled:
            knowledge_points = await self._llm_extract(analysis_text)

        return knowledge_points

    def _rule_based_extract(self, text: str) -> list[dict]:
        """规则匹配提取"""
        import re

        points = []

        # Pattern: "知识点：XXX" or "涉及：XXX" or "考察了XXX"
        patterns = [
            r"知识点[：:]\s*(.+?)(?:[；;，,\n]|$)",
            r"涉及[：:]\s*(.+?)(?:[；;，,\n]|$)",
            r"考察了?\s*(.+?)(?:[；;，,\n]|$)",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text):
                name = match.group(1).strip()
                if name and len(name) < 50:
                    points.append({
                        "name": name,
                        "category": None,
                        "mastery": "weak" if "错" in text or "薄弱" in text else None,
                    })

        return points

    async def _llm_extract(self, text: str) -> list[dict]:
        """调用大模型精细化提取（带重试 + 容错解析）。

        复用 llm_json.request_llm_json：思考型模型可能把 max_tokens 预算
        耗在推理上导致正文为空或 JSON 截断，attempts=2（首次 + 1 次重试）
        可显著提高提取成功率；extract_braces 容忍模型在 JSON 外附加
        代码块/前后缀文本。最终仍失败时返回 []（不阻塞分析流程，
        由调用方 knowledge_points 为空时静默接受）。
        """
        from openai import AsyncOpenAI
        from app.core.config import get_settings
        from app.services.llm_json import request_llm_json

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

        result = await request_llm_json(
            client,
            model=settings.LLM_MODEL,
            messages=[
                {"role": "user", "content": f"{EXTRACTION_PROMPT}\n\n分析文本：{text}"},
            ],
            max_tokens=500,
            temperature=0.1,
            timeout=60,          # 原 30s 偏紧，放宽到 60s 配合重试场景
            attempts=2,          # 空正文 / JSON 截断 / 调用异常均重试一次
            retry_delay=1.0,
            response_format={"type": "json_object"},
            extract_braces=True,  # 容错解析：忽略 JSON 外的多余文本
        )
        if result.data is None:
            logger.error("LLM knowledge extraction failed: %s", result.error)
            return []
        return result.data.get("knowledge_points", []) or []

    def merge(self, existing: list[dict] | None, new_points: list[dict]) -> list[dict]:
        """合并知识点，去重"""
        seen = set()
        merged = []
        for point in (existing or []) + new_points:
            name = point.get("name", "") if isinstance(point, dict) else str(point)
            if name and name not in seen:
                seen.add(name)
                merged.append(point if isinstance(point, dict) else {"name": name})
        return merged

    def _extract_names(self, knowledge_points: list) -> list[str]:
        """统一提取知识点名称列表并去重"""
        seen: list[str] = []
        for point in knowledge_points or []:
            name = point.get("name", "") if isinstance(point, dict) else str(point)
            if name and name not in seen:
                seen.append(name)
        return seen

    async def trim(self, knowledge_points: list, context: str | None = None, max_count: int = 5) -> list[dict]:
        """
        将知识点列表精简到指定数量（默认5个左右）。

        用于大题父题合并多个子题知识点后，或普通题知识点过多时，
        保留最核心的几个知识点，避免展示过长。

        Args:
            knowledge_points: 知识点列表，元素可以是字符串或 {"name": ...} 字典
            context: 题目相关上下文（如分析文本），辅助 LLM 判断重点
            max_count: 最多保留的知识点数量

        Returns:
            精简后的知识点字典列表，每项含 {"name": ...}
        """
        names = self._extract_names(knowledge_points)
        if not names:
            return []
        if len(names) <= max_count:
            return [{"name": n} for n in names]

        # 未配置 LLM 时直接截断，保证不超过上限
        if not self.llm_enabled:
            return [{"name": n} for n in names[:max_count]]

        from app.core.config import get_settings
        settings = get_settings()
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

        prompt = TRIM_PROMPT.format(
            max_count=max_count,
            context=context or "无",
            knowledge_points="\n".join(f"- {n}" for n in names),
        )

        try:
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=30,
            )
            data = sanitize_llm_controls(json.loads(response.choices[0].message.content or "{}"))
            trimmed = data.get("knowledge_points", [])
            if isinstance(trimmed, list) and trimmed:
                result = []
                for item in trimmed:
                    if isinstance(item, dict):
                        name = item.get("name", "")
                    else:
                        name = str(item)
                    if name and len(result) < max_count:
                        result.append({"name": name})
                return result if result else [{"name": n} for n in names[:max_count]]
        except Exception as e:
            logger.error("LLM knowledge trim failed: %s", e)

        return [{"name": n} for n in names[:max_count]]
