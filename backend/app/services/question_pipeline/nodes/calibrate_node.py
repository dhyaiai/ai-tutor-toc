"""智能体二：难度校准师（calibrate node）。

职责：读取用户在 KnowledgeTracker 中对目标知识点的掌握度，
把「原题难度」修正为「该学生当前最适合练习的难度」——
实现千人千题的个性化出题：
    - 知识点未掌握（mastery_score <= 60）→ 目标难度降为 easy（先巩固基础）
    - 基本掌握（60 < score <= 85）→ 维持原题难度 medium
    - 掌握良好（score > 85）→ 可升级为 hard（适当拔高）

当知识状态无记录或查询失败时，回落到 base_difficulty，不阻塞流水线。
"""

import logging

from langgraph.runtime import Runtime

from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState

logger = logging.getLogger(__name__)


def _calibrate_difficulty(
    base: str,
    weak_points: list[str],
    strong_points: list[str],
    knowledge_points: list[str],
) -> tuple[str, str]:
    """按薄弱/强项判定目标难度。

    Args:
        base: 原题难度（easy/medium/hard）
        weak_points: 该用户薄弱知识点列表（mastery_score <= 60）
        strong_points: 该用户掌握良好知识点列表（mastery_score >= 85）
        knowledge_points: 目标知识点列表

    Returns:
        (目标难度, 调整原因说明)
    """
    # 目标知识点命中薄弱点 → 降难度，先打基础
    if any(kp in weak_points for kp in knowledge_points):
        return "easy", "该知识点为薄弱项，建议降低难度先巩固基础"
    # 目标知识点命中强项且原题非 hard → 适当拔高
    if any(kp in strong_points for kp in knowledge_points) and base != "hard":
        return "hard", "该知识点已掌握良好，适当提高难度进行拔高训练"
    # 其余情况维持原难度
    return base, "按原题难度出题"


async def calibrate_node(
    state: QuestionPipelineState,
    runtime: Runtime[PipelineContext],
) -> dict:
    """难度校准节点：产出 calibration 和校准后的 difficulty。"""
    db = runtime.context.get("db")
    knowledge_points = state.get("knowledge_points") or []

    # 没有 db 会话（理论上不会发生，兜底）→ 直接回落
    if db is None:
        return {
            "calibration": None,
            "difficulty": state.get("base_difficulty", "medium"),
        }

    try:
        from app.services.knowledge_tracker import KnowledgeTracker

        tracker = KnowledgeTracker(db)
        result = await tracker.query(
            user_id=state["user_id"],
            subject=state.get("subject") or None,
            query_type="掌握度汇总",
        )
        weak_points = result.get("weak_points") or []
        strong_points = result.get("strong_points") or []
        difficulty, reason = _calibrate_difficulty(
            base=state.get("base_difficulty", "medium"),
            weak_points=weak_points,
            strong_points=strong_points,
            knowledge_points=knowledge_points,
        )
        return {
            "calibration": {
                "difficulty": difficulty,
                "reason": reason,
                "summary": result.get("summary", ""),
            },
            "difficulty": difficulty,
        }
    except Exception as e:  # noqa: BLE001 —— 掌握度查询失败不阻断出题
        logger.warning("难度校准失败，回落到原题难度: %s", e)
        return {
            "calibration": None,
            "difficulty": state.get("base_difficulty", "medium"),
        }
