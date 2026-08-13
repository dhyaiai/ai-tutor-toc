"""智能体一：联网搜索师（search node）。

职责：根据原题的知识点 / 题型，联网检索同类型题目、最新考纲与考情，
为后续的题目改造提供参考素材（题干风格、命题角度、常见设问方式）。

设计要点：
- 搜索服务是可插拔的：默认读环境变量 SEARCH_* 配置（SEARCH_API_KEY /
  SEARCH_BASE_URL / SEARCH_ENGINE），项目没有内置搜索 key，未配置时优雅跳过
  （status="skipped"），不影响整条流水线——transform 仍可基于原题生成。
- 任何搜索异常都降级为 skipped/failed，绝不让联网失败阻断出题流程。
"""

import logging

from langgraph.runtime import Runtime

from app.core.config import get_settings
from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState

logger = logging.getLogger(__name__)


def _build_search_query(state: QuestionPipelineState) -> str:
    """构造搜索引擎查询词：知识点 + 题型 + 年级/学科。"""
    kp = "、".join(state.get("knowledge_points") or []) or "未知知识点"
    qtype = state.get("question_type") or "练习题"
    grade = state.get("grade") or ""
    subject = state.get("subject") or ""
    parts = [f"初中/高中 {grade} {subject}" if grade else "", kp, qtype]
    return " ".join(p for p in parts if p) or kp


async def _fetch_via_http(state: QuestionPipelineState) -> list[dict]:
    """通过配置的搜索 API 发起 HTTP 检索（httpx，项目已有依赖）。

    不同搜索服务（Bing/Tavily/博查/阿里百炼等）返回结构不同，这里只做通用
    抽取：尽量取 title/snippet/link 字段。接入具体服务时在此函数内适配即可。
    """
    import httpx

    settings = get_settings()
    query = _build_search_query(state)
    params = {
        "q": query,
        "count": 5,
    }
    # API Key 通过 Authorization Header 传递（避免出现在 query string 中泄露到日志/Referer）
    headers = {"Authorization": f"Bearer {settings.SEARCH_API_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(settings.SEARCH_BASE_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    # 兼容多种常见返回结构（results/web_results/data.list 等）
    raw_list = (
        data.get("results")
        or data.get("web_results")
        or data.get("data", {}).get("list")
        or []
    )
    refs = []
    for item in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(item, dict):
            continue
        refs.append({
            "title": item.get("title", ""),
            "source": item.get("url") or item.get("link") or item.get("source", ""),
            "content": item.get("snippet") or item.get("content") or item.get("abstract", ""),
        })
    return refs


async def search_node(
    state: QuestionPipelineState,
    runtime: Runtime[PipelineContext],
) -> dict:
    """联网搜题节点：产出 search_status / search_summary / references。"""
    settings = get_settings()

    # 未配置搜索服务 → 跳过（最常见情况，保证流水线可用）
    if not settings.SEARCH_API_KEY or not settings.SEARCH_BASE_URL:
        logger.info("联网搜索未配置(SEARCH_API_KEY/SEARCH_BASE_URL)，跳过搜索直接进入难度校准")
        return {
            "search_status": "skipped",
            "search_summary": "未配置联网搜索，直接基于原题生成",
            "references": [],
        }

    try:
        refs = await _fetch_via_http(state)
        if not refs:
            return {
                "search_status": "done",
                "search_summary": "搜索完成但未找到有效参考资料，基于原题生成",
                "references": [],
            }
        summary = f"检索到 {len(refs)} 条参考资料，已作为改造参考注入"
        return {
            "search_status": "done",
            "search_summary": summary,
            "references": refs[:5],  # 控制上下文体积，最多注入 5 条
        }
    except Exception as e:  # noqa: BLE001 —— 搜索失败不阻断流程
        logger.warning("联网搜索失败，降级为跳过: %s", e)
        return {
            "search_status": "failed",
            "search_summary": f"联网搜索失败({e})，基于原题生成",
            "references": [],
        }
