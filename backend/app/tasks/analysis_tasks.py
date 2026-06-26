"""
Celery 异步分析任务。

编排作业分析的完整流程：
OCR 切割 → 多模态评分 → 知识点提取 → 向量化入库
"""

import asyncio
import logging
from app.models.assignment import AssignmentStatus
from app.models.question import QuestionStatus, AnalysisTaskType, AnalysisTaskStatus

logger = logging.getLogger(__name__)

# Celery is optional (not available in dev mode)
try:
    from app.tasks.celery_app import celery_app
except Exception:
    celery_app = None


def _run_async(coro):
    """在 Celery 同步任务中运行异步协程"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        # Running inside an event loop (e.g. tests), create new loop in thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


if celery_app is not None:
    @celery_app.task(bind=True, name="analyze_assignment", max_retries=3, default_retry_delay=60)
    def analyze_assignment(self, assignment_id: int):
        """作业整体分析任务（Celery）。"""
        logger.info("[analyze_assignment] Starting for assignment %d (attempt %d)",
                    assignment_id, self.request.retries)

        try:
            _run_async(_do_analyze(assignment_id))
        except Exception as exc:
            logger.error("[analyze_assignment] Failed for assignment %d: %s", assignment_id, exc)
            _run_async(_mark_failed(assignment_id, str(exc)))
            raise self.retry(exc=exc)

        return {"assignment_id": assignment_id, "status": "completed"}
else:
    analyze_assignment = None  # type: ignore


async def _do_analyze(assignment_id: int):
    """分析主流程 —— 状态机：splitting → splitted → grading → completed"""
    from sqlalchemy import select, func
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment
    from app.models.question import Question

    async with async_session_factory() as db:
        # Load assignment
        result = await db.execute(select(Assignment).where(Assignment.id == assignment_id))
        assignment = result.scalar_one_or_none()
        if not assignment:
            raise ValueError(f"Assignment {assignment_id} not found")

        from app.services.file_upload import StorageService
        storage = StorageService()

        # Check if questions already exist (e.g. from manual split)
        q_count_result = await db.execute(
            select(func.count()).select_from(Question).where(Question.assignment_id == assignment_id)
        )
        existing_count = q_count_result.scalar() or 0

        if existing_count == 0:
            raise ValueError("请先手动切割题目后再开始分析")

        # ── 题目已存在（手动切割），直接进入评分 ──
        logger.info("[analyze] %d existing questions found, starting grading", existing_count)
        question_result = await db.execute(
            select(Question)
            .where(Question.assignment_id == assignment_id)
            .order_by(Question.question_number)
        )
        existing_questions = question_result.scalars().all()
        # 下载已有题目图片用于评分
        question_records = []
        for q in existing_questions:
            q_bytes = await storage.get_file_bytes(q.image_url)
            if q_bytes is None:
                logger.warning("Failed to download question image: %s", q.image_url)
                continue
            question_records.append((q, q_bytes))
        if not question_records:
            raise ValueError("无法加载任何题目图片进行评分")
        assignment.status = AssignmentStatus.GRADING
        await db.commit()

        # ── 阶段 2: AI 逐题评分（实时更新，每题完成后立即提交）──
        assignment.status = AssignmentStatus.GRADING
        await db.commit()
        logger.info("[analyze] Stage: GRADING (assignment %d) — %d questions", assignment_id, len(question_records))

        from app.services.ai_grader import AIGrader
        from app.services.knowledge_extractor import KnowledgeExtractor
        grader = AIGrader()
        extractor = KnowledgeExtractor()

        total_score = 0
        all_knowledge_points: set[str] = set()
        q_count = len(question_records)

        for idx, (question, qr) in enumerate(question_records):
            image_bytes = qr.image_bytes if hasattr(qr, 'image_bytes') else qr
            logger.info("[analyze] Grading question %d/%d (id=%d)...", idx + 1, q_count, question.id)

            try:
                grade_result = await asyncio.wait_for(
                    grader.grade_single(image_bytes),
                    timeout=120,  # 每题 2 分钟超时
                )
            except asyncio.TimeoutError:
                logger.warning("Question %d grading timed out, marking as failed", question.id)
                question.status = QuestionStatus.FAILED
                question.analysis_detail = "评分超时"
                await db.commit()
                continue
            except Exception as e:
                logger.error("Question %d grading failed: %s", question.id, e)
                question.status = QuestionStatus.FAILED
                question.analysis_detail = f"评分异常: {str(e)}"
                await db.commit()
                continue

            # 更新题目
            question.student_answer = grade_result.student_answer
            question.correct_answer = grade_result.correct_answer
            question.score = grade_result.score
            question.full_score = grade_result.full_score
            question.analysis_detail = grade_result.analysis_detail
            question.question_type = grade_result.question_type
            question.common_mistakes = grade_result.common_mistakes
            question.confidence_score = grade_result.confidence
            question.status = QuestionStatus.COMPLETED if grade_result.confidence >= 0.3 else QuestionStatus.FAILED

            # 提取知识点
            if grade_result.analysis_detail:
                try:
                    kps = await extractor.extract(grade_result.analysis_detail)
                    question.knowledge_points = extractor.merge(
                        grade_result.knowledge_points, kps
                    )
                except Exception:
                    question.knowledge_points = grade_result.knowledge_points or []
            else:
                question.knowledge_points = grade_result.knowledge_points or []

            if question.score is not None:
                total_score += question.score
            for kp in (question.knowledge_points or []):
                name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                all_knowledge_points.add(name)

            # 立即提交，让前端轮询实时看到
            await db.commit()
            logger.info("[analyze] Question %d/%d done (score=%.1f)", idx + 1, q_count, question.score or 0)

        # ── 完成 ──
        assignment = await db.get(Assignment, assignment_id)
        assignment.status = AssignmentStatus.COMPLETED
        assignment.total_score = total_score

        # Generate AI summary via LLM for a teacher-like overall analysis
        error_count = sum(1 for q, _ in question_records
                         if q.score is not None and q.full_score is not None and q.score < q.full_score)
        try:
            ai_summary = await _generate_assignment_summary(
                question_records=[(q, None) for q, _ in question_records],
                total_score=total_score,
                q_count=q_count,
                error_count=error_count,
            )
            assignment.ai_summary = ai_summary
        except Exception as e:
            logger.warning("Failed to generate LLM summary, using fallback: %s", e)
            kp_list = ", ".join(all_knowledge_points) if all_knowledge_points else "暂无"
            assignment.ai_summary = (
                f"本作业共 {q_count} 题，总分 {total_score} 分。"
                f"错题 {error_count} 题。"
                f"涉及知识点：{kp_list}。"
            )

        await db.commit()

        # Trigger vectorization (skip in dev mode if no Celery/Redis)
        try:
            from app.tasks.vector_tasks import vectorize_assignment
            if vectorize_assignment is not None:
                vectorize_assignment.delay(assignment_id)
            else:
                logger.info("[dev] Skipping vectorization (Celery not available)")
        except Exception:
            logger.info("[dev] Skipping vectorization (Celery/Qdrant not available)")

        logger.info("[analyze] Stage: COMPLETED (assignment %d)", assignment_id)


async def recalc_assignment_total(assignment_id: int, db=None, *, user_id: int | None = None) -> None:
    """
    根据所有题目的 score 和 full_score 重新计算作业总分。
    题目分值变动（重分析、确认修改）后调用此函数保持数据一致。

    如果传入 db session 则使用传入的，否则创建新的 session。

    user_id: 可选，传入时校验作业所有权，防止越权操作。
    """
    from sqlalchemy import select, func
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment

    async def _do(session):
        if user_id is not None:
            assignment = await session.get(Assignment, assignment_id)
            if not assignment or assignment.creator_id != user_id:
                logger.warning(
                    "[recalc] Ownership check failed for assignment %d (user=%s)",
                    assignment_id, user_id,
                )
                return
        result = await session.execute(
            select(
                func.coalesce(func.sum(Question.score), 0),
                func.coalesce(func.sum(Question.full_score), 0),
            ).where(Question.assignment_id == assignment_id)
        )
        total_score, full_total = result.one()
        assignment = await session.get(Assignment, assignment_id)
        if assignment:
            assignment.total_score = float(total_score)
            await session.commit()
            logger.info(
                "[recalc] Assignment %d total_score updated to %.1f",
                assignment_id, float(total_score),
            )

    if db is not None:
        await _do(db)
    else:
        async with async_session_factory() as session:
            await _do(session)


async def _mark_failed(assignment_id: int, error_msg: str):
    """标记作业分析失败"""
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment

    async with async_session_factory() as db:
        result = await db.execute(select(Assignment).where(Assignment.id == assignment_id))
        assignment = result.scalar_one_or_none()
        if assignment:
            assignment.status = AssignmentStatus.FAILED
            assignment.ai_summary = f"Analysis failed: {error_msg}"
            await db.commit()


if celery_app is not None:
    @celery_app.task(bind=True, name="reanalyze_question", max_retries=2, default_retry_delay=30)
    def reanalyze_question(self, question_id: int, remark: str | None = None):
        """
        单题重分析任务。
        """
        logger.info("[reanalyze_question] Starting for question %d (remark=%s)", question_id, remark)

        try:
            _run_async(_do_reanalyze(question_id, remark))
        except Exception as exc:
            logger.error("[reanalyze_question] Failed for question %d: %s", question_id, exc)
            raise self.retry(exc=exc)

        return {"question_id": question_id, "status": "completed"}
else:
    reanalyze_question = None


async def _do_reanalyze(question_id: int, remark: str | None = None):
    """单题重分析"""
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.question import Question
    from app.services.file_upload import StorageService

    async with async_session_factory() as db:
        result = await db.execute(select(Question).where(Question.id == question_id))
        question = result.scalar_one_or_none()
        if not question:
            raise ValueError(f"题目 {question_id} 不存在")

        question.status = QuestionStatus.PENDING
        await db.flush()

        # Get image bytes from storage
        storage = StorageService()
        image_bytes = await storage.get_file_bytes(question.image_url)
        if image_bytes is None:
            raise ValueError(f"无法下载题目图片: {question.image_url}")

        # Re-grade with remark
        from app.services.ai_grader import AIGrader
        grader = AIGrader()
        results = await grader.grade_batch([image_bytes], remark=remark)

        if results:
            gr = results[0]
            question.student_answer = gr.student_answer
            question.correct_answer = gr.correct_answer
            question.score = gr.score
            question.full_score = gr.full_score
            question.analysis_detail = gr.analysis_detail
            question.question_type = gr.question_type
            question.confidence_score = gr.confidence
            question.common_mistakes = gr.common_mistakes
            question.status = QuestionStatus.COMPLETED

            # Re-extract knowledge points
            if gr.analysis_detail:
                from app.services.knowledge_extractor import KnowledgeExtractor
                extractor = KnowledgeExtractor()
                kps = await extractor.extract(gr.analysis_detail)
                question.knowledge_points = extractor.merge(
                    gr.knowledge_points, kps
                )
            else:
                question.knowledge_points = gr.knowledge_points or []

        await db.commit()

        # 同步更新作业总分
        await recalc_assignment_total(question.assignment_id, db)

        logger.info("[reanalyze_question] Completed for question %d", question_id)


if celery_app is not None:
    @celery_app.task(bind=True, name="generate_similar_questions")
    def generate_similar_questions(self, question_id: int):
        """同类题生成任务"""
        logger.info("[generate_similar] Starting for question %d", question_id)
        try:
            result = _run_async(_do_generate_similar(question_id))
            return result
        except Exception as exc:
            logger.error("[generate_similar] Failed: %s", exc)
            return {"error": str(exc)}
else:
    generate_similar_questions = None


async def _do_generate_similar(question_id: int) -> dict:
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.question import Question, AnalysisTask, AnalysisTaskType, AnalysisTaskStatus
    from app.services.similar_generator import SimilarGenerator

    async with async_session_factory() as db:
        # 查找或创建任务记录
        task_result = await db.execute(
            select(AnalysisTask).where(
                AnalysisTask.question_id == question_id,
                AnalysisTask.type == AnalysisTaskType.SIMILAR_GENERATION,
                AnalysisTask.status == AnalysisTaskStatus.PENDING,
            ).order_by(AnalysisTask.created_at.desc()).limit(1)
        )
        task = task_result.scalar_one_or_none()

        if task:
            task.status = AnalysisTaskStatus.PROCESSING
            await db.flush()

        result = await db.execute(select(Question).where(Question.id == question_id))
        question = result.scalar_one_or_none()
        if not question:
            if task:
                task.status = AnalysisTaskStatus.FAILED
                task.error_message = "Question not found"
                await db.commit()
            return {"error": "Question not found"}

        kps = question.knowledge_points
        raw_list = kps if isinstance(kps, list) else list(kps.values()) if isinstance(kps, dict) else None
        kp_list = [
            k["name"] if isinstance(k, dict) else str(k)
            for k in raw_list
        ] if raw_list else None

        generator = SimilarGenerator()
        similar = await generator.generate(
            knowledge_points=kp_list,
            student_answer=question.student_answer,
            correct_answer=question.correct_answer,
            analysis_detail=question.analysis_detail,
            question_type=question.question_type,
        )

        result_data = {
            "similar_questions": [
                {
                    "id": i,
                    "question_text": sq.question_text,
                    "answer": sq.answer,
                    "knowledge_point": sq.knowledge_point,
                    "difficulty": sq.difficulty,
                    "question_type": sq.question_type,
                }
                for i, sq in enumerate(similar)
            ]
        }

        if task:
            task.status = AnalysisTaskStatus.COMPLETED
            task.result_json = result_data
            await db.commit()
        else:
            # 无任务记录时直接创建完成记录
            new_task = AnalysisTask(
                assignment_id=question.assignment_id,
                question_id=question_id,
                type=AnalysisTaskType.SIMILAR_GENERATION,
                status=AnalysisTaskStatus.COMPLETED,
                result_json=result_data,
            )
            db.add(new_task)
            await db.commit()

        return result_data


async def _generate_assignment_summary(
    question_records: list,
    total_score: float,
    q_count: int,
    error_count: int,
) -> str:
    """使用 LLM 生成教师式的整体作业分析评语"""
    from app.models.question import Question
    from app.core.config import get_settings
    from openai import AsyncOpenAI

    settings = get_settings()
    if not settings.LLM_API_KEY:
        raise ValueError("LLM not configured")

    # Build summary of each question for the LLM
    question_summaries = []
    for q, _ in question_records:
        score_text = f"{q.score}/{q.full_score}" if q.score is not None else "未评"
        kp_text = ", ".join(
            kp if isinstance(kp, str) else kp.get("name", str(kp))
            for kp in (q.knowledge_points or [])
        ) if q.knowledge_points else "无"
        mistakes_text = ", ".join(q.common_mistakes) if q.common_mistakes else "无"
        question_summaries.append(
            f"第{q.question_number}题（{q.question_type or '未知题型'}）："
            f"得分{score_text}，知识点：{kp_text}，"
            f"常见错误：{mistakes_text}"
        )

    questions_text = "\n".join(question_summaries)

    prompt = f"""你是一位经验丰富的教师，请根据以下学生作业的各题详情，写一份整体分析评语（200-400字）。

作业概况：共{q_count}题，总分{total_score}分，错题{error_count}题。

各题情况：
{questions_text}

请写出一份像优秀教师那样的综合性评语，要求：
1. 【整体表现】总体评价这份作业的完成质量，肯定学生的努力和亮点
2. 【具体优点】指出学生掌握得好的知识点或题型（具体说明哪里好）
3. 【存在不足】指出学生薄弱的知识点或常犯的错误类型（具体说明问题所在）
4. 【改进建议】给出2-3条具体、可操作的改进建议，帮助学生在下次作业中提高
5. 语气要鼓励性、建设性，像老师在和学生谈心，不要冷冰冰地罗列数据
6. 不要使用 Markdown 格式，用纯文本段落表达"""

    client = AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_API_BASE,
    )

    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.3,
        timeout=60,
    )

    summary = response.choices[0].message.content or ""
    return summary.strip()
