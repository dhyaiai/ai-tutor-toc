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


# 合并版提示词：一次调用同时完成「从评语提取 + 与候选列表合并去重 + 精简到上限」。
# 替代原来的 extract（LLM）→ merge（内存）→ trim（LLM）链路（最多两次 LLM 调用），
# 将每题的知识点 LLM 开销减半，是整卷分析耗时的重要组成部分。
EXTRACT_TRIM_PROMPT = """你是一位教育内容分析专家。请完成以下知识点整理任务：

1. 从题目分析文本中提取明确涉及的知识点（只提取明确涉及的，不要猜测）
2. 与已列出的知识点合并去重，将相似、重复或过于细分的知识点合并为更核心的概念
3. 最多保留 {max_count} 个最能概括本题考查重点的知识点，优先保留学科核心概念，去掉泛泛而谈或仅作为背景的细枝末节

返回 JSON：{{"knowledge_points": ["知识点1", "知识点2", ...]}}

已列出的知识点：
{knowledge_points}

题目分析文本：
{analysis_text}
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

    async def extract_and_trim(
        self,
        analysis_text: str | None,
        existing_points: list | None,
        max_count: int = 5,
    ) -> list[dict]:
        """
        一步完成「提取 + 合并 + 精简」：从评语提取知识点、与评分 LLM 返回的
        知识点合并去重、并精简到 max_count 个。

        替代原来的 extract → merge → trim 三步链路（规则提取失败时最多两次
        LLM 调用），普通题目场景将知识点 LLM 开销从最多 2 次降为最多 1 次。

        快路径（不发起任何 LLM 调用，与原逻辑一致）：
        - 无评语：仅对已有知识点做精简（≤ 上限时 trim 内部直接返回）
        - 规则提取成功：与原 extract 一致直接使用规则结果，再按需精简
        - 未配置文本 LLM：降级为纯截断

        Args:
            analysis_text: AI 评分返回的分析文本（评语），可为 None
            existing_points: 评分 LLM 已返回的知识点列表（str 或 dict 混合）
            max_count: 最多保留的知识点数量

        Returns:
            精简后的知识点字典列表，每项含 {"name": ...}
        """
        # 快路径 1：无评语 → 与原逻辑一致，仅精简已有列表（context=None）
        if not analysis_text:
            return await self.trim(existing_points or [], context=None, max_count=max_count)

        # 规则提取（显式标注模式，命中时与原 extract 一致不发起 LLM）
        rule_kps = self._rule_based_extract(analysis_text)

        # 快路径 2：规则已提取到 → 合并后按需精简（≤ 上限时无 LLM 调用）
        if rule_kps:
            merged = self.merge(existing_points, rule_kps)
            return await self.trim(merged, context=analysis_text, max_count=max_count)

        # 快路径 3：未配置文本 LLM → 原逻辑降级为空提取，仅精简已有列表
        if not self.llm_enabled:
            return await self.trim(existing_points or [], context=None, max_count=max_count)

        # 常规路径：规则未命中，一次 LLM 调用完成「提取 + 合并 + 精简」
        # （原逻辑此处为 extract 一次 LLM + trim 可能再一次 LLM）
        from openai import AsyncOpenAI
        from app.core.config import get_settings
        from app.services.llm_json import request_llm_json

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

        names = self._extract_names(existing_points or [])
        prompt = EXTRACT_TRIM_PROMPT.format(
            max_count=max_count,
            knowledge_points="\n".join(f"- {n}" for n in names) if names else "（无）",
            analysis_text=analysis_text,
        )

        result = await request_llm_json(
            client,
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.1,
            timeout=60,
            attempts=2,          # 与 _llm_extract 一致：空正文/截断/异常重试一次
            retry_delay=1.0,
            response_format={"type": "json_object"},
            extract_braces=True,
        )
        if result.data is None:
            logger.error("LLM extract_and_trim failed: %s", result.error)
            # 失败回退：截断已有列表（与 trim 无 LLM 时的降级行为一致）
            return [{"name": n} for n in names[:max_count]]

        trimmed = result.data.get("knowledge_points", []) or []
        final: list[dict] = []
        if isinstance(trimmed, list):
            for item in trimmed:
                name = item.get("name", "") if isinstance(item, dict) else str(item)
                if name and len(final) < max_count:
                    final.append({"name": name})
        if final:
            return final
        # LLM 返回为空（异常输出）→ 回退截断已有列表
        return [{"name": n} for n in names[:max_count]]

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
