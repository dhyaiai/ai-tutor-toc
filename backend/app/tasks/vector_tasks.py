"""
向量化入库任务。

在作业分析完成后，将分析文本 embedding 后批量写入 Qdrant。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Celery is optional (not available in dev mode)
try:
    from app.tasks.celery_app import celery_app
except Exception:
    celery_app = None


if celery_app is not None:
    @celery_app.task(bind=True, name="vectorize_assignment", max_retries=2, default_retry_delay=30)
    def vectorize_assignment(self, assignment_id: int):
        """将作业的所有分析文本向量化并存入 Qdrant（Celery）。"""
        logger.info("[vectorize_assignment] Starting for assignment %d", assignment_id)

        try:
            asyncio.run(_do_vectorize(assignment_id))
        except Exception as exc:
            logger.error("[vectorize_assignment] Failed for assignment %d: %s", assignment_id, exc)
            raise self.retry(exc=exc)

        return {"assignment_id": assignment_id, "status": "completed"}
else:
    vectorize_assignment = None


async def _do_vectorize(assignment_id: int):
    """向量化主流程"""
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment
    from app.models.question import Question
    from app.services.rag_service import RAGService

    async with async_session_factory() as db:
        # Load assignment
        result = await db.execute(select(Assignment).where(Assignment.id == assignment_id))
        assignment = result.scalar_one_or_none()
        if not assignment:
            logger.error("Assignment %d not found", assignment_id)
            return

        # Load questions
        q_result = await db.execute(
            select(Question).where(Question.assignment_id == assignment_id)
        )
        questions = q_result.scalars().all()

        if not questions:
            return

        # Build chunks for each question + overall summary
        chunks = []

        # Overall summary chunk
        if assignment.ai_summary:
            chunks.append({
                "text": assignment.ai_summary,
                "metadata": {
                    "assignment_id": assignment_id,
                    "grade": assignment.grade,
                    "subject": assignment.subject,
                    "semester": assignment.semester,
                    "month": assignment.usage_month,
                    "type": "summary",
                },
            })

        # Individual question chunks
        for q in questions:
            if not q.analysis_detail and not q.knowledge_points:
                continue

            # Build enriched analysis text
            kp_str = ""
            if q.knowledge_points:
                if isinstance(q.knowledge_points, list):
                    kp_str = ", ".join(q.knowledge_points)
                elif isinstance(q.knowledge_points, dict):
                    kp_str = ", ".join(
                        k.get("name", str(k)) if isinstance(k, dict) else str(k)
                        for k in q.knowledge_points.values()
                    )

            text = (
                f"第{q.question_number}题："
                f"得分{q.score}/{q.full_score}。"
                f"知识点：{kp_str}。"
                f"分析：{q.analysis_detail or '无'}"
            )

            chunks.append({
                "text": text,
                "metadata": {
                    "assignment_id": assignment_id,
                    "question_id": q.id,
                    "question_number": q.question_number,
                    "grade": assignment.grade,
                    "subject": assignment.subject,
                    "semester": assignment.semester,
                    "month": assignment.usage_month,
                    "knowledge_points": kp_str,
                    "score_rate": round(q.score / q.full_score, 4) if q.score is not None and q.full_score else 0,
                    "type": "question_analysis",
                },
            })

        # Batch index
        if chunks:
            rag = RAGService()
            await rag.batch_index(chunks)
            logger.info("[vectorize_assignment] Indexed %d chunks for assignment %d",
                       len(chunks), assignment_id)
