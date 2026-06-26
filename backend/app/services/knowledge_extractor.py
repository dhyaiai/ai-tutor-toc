"""
知识点提取服务。

从 AI 评分分析文本中提取结构化知识点，可选调用大模型做精细化提取。
"""

import json
import logging
from dataclasses import dataclass

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
        """调用大模型精细化提取"""
        from openai import AsyncOpenAI
        from app.core.config import get_settings

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

        try:
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "user", "content": f"{EXTRACTION_PROMPT}\n\n分析文本：{text}"},
                ],
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=30,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            return data.get("knowledge_points", [])
        except Exception as e:
            logger.error("LLM knowledge extraction failed: %s", e)
            return []

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
