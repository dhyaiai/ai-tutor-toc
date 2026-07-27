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


def _stitch_question_answer(q_image_bytes: bytes, a_image_bytes: bytes | None) -> bytes:
    """
    将题目图片和学生答案图片垂直拼接，添加分隔线和标签。

    拼接后 AI 能同时看到题目内容和学生作答，提高识别准确率。
    如果答案图片为空，直接返回题目图片。
    输出为 JPEG 格式（体积远小于 PNG，大幅减少 API 传输时间）。

    Args:
        q_image_bytes: 题目图片字节
        a_image_bytes: 答案图片字节（可为 None）

    Returns:
        拼接后的图片字节（JPEG格式）
    """
    import cv2
    import numpy as np

    if a_image_bytes is None:
        return _compress_for_api(q_image_bytes)

    q_img = cv2.imdecode(np.frombuffer(q_image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    a_img = cv2.imdecode(np.frombuffer(a_image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)

    if q_img is None:
        return _compress_for_api(a_image_bytes) if isinstance(a_image_bytes, bytes) else q_image_bytes
    if a_img is None:
        return _compress_for_api(q_image_bytes)

    # 缩放到相同宽度，限制最大宽度为 800px（减小图片体积，加快 API 传输）
    max_w = max(q_img.shape[1], a_img.shape[1])
    if max_w > 800:
        scale = 800 / max_w
        q_h = int(q_img.shape[0] * scale)
        a_h = int(a_img.shape[0] * scale)
        q_img = cv2.resize(q_img, (800, q_h))
        a_img = cv2.resize(a_img, (800, a_h))
        target_w = 800
    else:
        q_h = int(q_img.shape[0] * max_w / q_img.shape[1])
        a_h = int(a_img.shape[0] * max_w / a_img.shape[1])
        q_img = cv2.resize(q_img, (max_w, q_h))
        a_img = cv2.resize(a_img, (max_w, a_h))
        target_w = max_w

    # 添加标签
    cv2.putText(q_img, "[Question]", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(a_img, "[Student Answer]", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # 白色分隔线
    sep = np.full((8, target_w, 3), 200, dtype=np.uint8)

    # 垂直拼接
    stitched = np.vstack([q_img, sep, a_img])

    # 使用 JPEG 压缩（quality=85 在识别质量和文件体积间取得平衡）
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85]
    _, result_bytes = cv2.imencode(".jpg", stitched, encode_params)
    return result_bytes.tobytes()


def _compress_for_api(image_bytes: bytes, max_width: int = 800, quality: int = 85) -> bytes:
    """
    将单张图片压缩为 JPEG 格式，减小体积以加快 API 传输速度。

    Args:
        image_bytes: 原始图片字节
        max_width: 最大宽度（像素）
        quality: JPEG 压缩质量（1-100）

    Returns:
        压缩后的 JPEG 图片字节
    """
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return image_bytes

    # 缩放过大的图片
    if img.shape[1] > max_width:
        scale = max_width / img.shape[1]
        new_h = int(img.shape[0] * scale)
        img = cv2.resize(img, (max_width, new_h))

    encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    _, result_bytes = cv2.imencode(".jpg", img, encode_params)
    return result_bytes.tobytes()


# ── 父题题型后处理：修正 AI 误判（如将文言文阅读大题误标为单选题）──
_PARENT_TYPE_KEYWORD_MAP = [
    # (关键词列表, 修正后的题型)
    (["文言文", "文言", "古文", "古文中"], "文言文阅读"),
    (["现代文", "散文", "小说", "议论文", "记叙文"], "现代文阅读"),
    (["完形填空", "完形"], "完形填空"),
    (["阅读", "篇章", "passage"], "阅读理解"),
]


def _infer_parent_question_type(
    ai_parent_type: str | None,
    sub_list: list,
) -> str | None:
    """
    根据子题信息修正父题题型。

    规则：
    - 若 AI 已将父题标为"单选题"/"多选题"，但子题中存在多种不同题型，
      说明这是一个包含混合小题的大题，此时根据子题分析内容推断正确的大题题型。
    - 若所有子题题型一致且与父题相同，不做修正。
    """
    if not sub_list:
        return ai_parent_type

    # 收集子题的题型集合
    child_types = {sq.question_type for sq in sub_list if sq.question_type}

    # 如果子题题型全部一致且父题不是选择题，无需修正
    # 如果父题不是单选题/多选题，信任 AI 的判断
    if ai_parent_type not in ("单选题", "多选题"):
        return ai_parent_type

    # 父题被标为选择题，但子题有多种类型 → 明显是 AI 误判
    # 或者子题中有非选择题类型（如简答题、填空题），说明这不是纯选择题大题
    has_non_choice_child = any(
        t and "选" not in t for t in child_types
    )
    if len(child_types) <= 1 and not has_non_choice_child:
        # 子题全是选择题，父题标为单选题可能是正确的
        return ai_parent_type

    # 扫描子题的 analysis_detail 和 knowledge_points 寻找关键词
    all_text = ""
    for sq in sub_list:
        if sq.analysis_detail:
            all_text += sq.analysis_detail + " "
        if sq.knowledge_points:
            for kp in sq.knowledge_points:
                if isinstance(kp, str):
                    all_text += kp + " "
                elif isinstance(kp, dict):
                    all_text += kp.get("name", "") + " "

    for keywords, corrected_type in _PARENT_TYPE_KEYWORD_MAP:
        if any(kw in all_text for kw in keywords):
            logger.info(
                "Corrected parent question_type from '%s' to '%s' based on sub-question analysis",
                ai_parent_type, corrected_type
            )
            return corrected_type

    # 无法推断具体类型，但确认不是纯选择题大题
    logger.warning(
        "Parent question_type '%s' appears incorrect (mixed sub-types: %s), "
        "but could not infer correct type from analysis text",
        ai_parent_type, child_types
    )
    return ai_parent_type  # 无法确定时保持原样


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

    try:
        await _do_analyze_inner(assignment_id)
    except Exception as exc:
        logger.error("[analyze] Fatal error for assignment %d: %s", assignment_id, exc, exc_info=True)
        try:
            await _mark_failed(assignment_id, str(exc))
        except Exception as mark_exc:
            logger.error("[analyze] Failed to mark assignment %d as FAILED: %s", assignment_id, mark_exc)
        raise


async def _do_analyze_inner(assignment_id: int):
    """分析主流程实现"""
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

        # ── 清理旧的子题记录（重新分析时避免产生孤儿数据）──
        old_children_result = await db.execute(
            select(Question).where(
                Question.assignment_id == assignment_id,
                Question.parent_id.isnot(None),
            )
        )
        old_children = old_children_result.scalars().all()
        if old_children:
            logger.info("[analyze] Deleting %d old sub-questions for assignment %d", len(old_children), assignment_id)
            for child in old_children:
                await db.delete(child)
            await db.flush()

        # ── 题目已存在（手动切割），直接进入评分 ──
        # 只加载顶层题目（parent_id IS NULL），子题已清理
        logger.info("[analyze] Loading top-level questions for assignment %d", assignment_id)
        question_result = await db.execute(
            select(Question)
            .where(
                Question.assignment_id == assignment_id,
                Question.parent_id.is_(None),
            )
            .order_by(Question.question_number)
        )
        existing_questions = question_result.scalars().all()
        # 下载已有题目图片用于评分（如有答案图片则拼接后发给AI）
        question_records = []
        for q in existing_questions:
            q_bytes = await storage.get_file_bytes(q.image_url)
            if q_bytes is None:
                logger.warning("Failed to download question image: %s", q.image_url)
                continue
            # 如果有答案图片，下载并拼接
            if q.answer_image_url:
                try:
                    a_bytes = await storage.get_file_bytes(q.answer_image_url)
                    if a_bytes:
                        q_bytes = _stitch_question_answer(q_bytes, a_bytes)
                        logger.info("[analyze] 题目 #%d 已拼接答案图片", q.question_number)
                    else:
                        logger.warning("[analyze] 题目 #%d 答案图片下载失败", q.question_number)
                        q_bytes = _compress_for_api(q_bytes)  # 无答案图时也压缩
                except Exception as stitch_err:
                    logger.error("[analyze] 题目 #%d 答案拼接异常: %s，回退到仅使用题目图片",
                                 q.question_number, stitch_err)
                    q_bytes = _compress_for_api(q_bytes)
            else:
                # 无答案图片，直接压缩题目图片
                q_bytes = _compress_for_api(q_bytes)
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
        from app.services.personality_service import load_grading_directive
        grader = AIGrader()
        extractor = KnowledgeExtractor()

        # 加载作业创建者的助教个性化配置（性格/说话风格/评分严格度），对所有批改生效
        personality_directive = await load_grading_directive(db, assignment.creator_id)

        total_score = 0
        all_knowledge_points: set[str] = set()
        q_count = len(question_records)
        # Batch grading: group images for concurrent API calls (≤3 per request)
        BATCH_SIZE = grader.MAX_IMAGES_PER_REQUEST

        # Process in batches
        batch_start = 0
        while batch_start < q_count:
            batch = question_records[batch_start: batch_start + BATCH_SIZE]
            batch_images = [qr.image_bytes if hasattr(qr, 'image_bytes') else qr for _, qr in batch]
            logger.info("[analyze] Grading batch %d/%d (%d questions)...",
                        batch_start // BATCH_SIZE + 1, (q_count + BATCH_SIZE - 1) // BATCH_SIZE, len(batch))

            try:
                batch_results = await asyncio.wait_for(
                    grader.grade_batch(batch_images, subject=assignment.subject, personality_directive=personality_directive),
                    timeout=240,  # 每批 4 分钟超时（LLM API 自身超时 120s，需留余量）
                )
            except asyncio.TimeoutError:
                logger.warning("Batch grading timed out for questions starting at idx %d", batch_start)
                for question, _ in batch:
                    question.status = QuestionStatus.FAILED
                    question.analysis_detail = "评分超时，请重新分析该题"
                await db.commit()
                batch_start += BATCH_SIZE
                continue
            except Exception as e:
                logger.error("Batch grading failed for questions starting at idx %d: %s", batch_start, e)
                for question, _ in batch:
                    question.status = QuestionStatus.FAILED
                    question.analysis_detail = f"评分异常: {str(e)}"
                await db.commit()
                batch_start += BATCH_SIZE
                continue

            # Apply results — 支持大题套小题（父题有 sub_questions 时创建子题记录）
            for (question, _), grade_result in zip(batch, batch_results):
                if grade_result.sub_questions and len(grade_result.sub_questions) > 0:
                    # ── 大题套小题：父题为容器，子题存评分 ──
                    sub_list = grade_result.sub_questions

                    # 父题设为容器（不存评分数据）
                    question.score = None
                    question.full_score = None
                    question.student_answer = None
                    question.correct_answer = None
                    question.question_type = _infer_parent_question_type(
                        grade_result.question_type, sub_list
                    )
                    question.analysis_detail = f"本大题共 {len(sub_list)} 小题"
                    question.confidence_score = grade_result.confidence
                    question.status = QuestionStatus.COMPLETED

                    # 收集所有子题的知识点（父题知识点为子题知识点的并集）
                    parent_kps: set[str] = set()

                    for idx, sq in enumerate(sub_list):
                        child = Question(
                            assignment_id=question.assignment_id,
                            question_number=question.question_number,
                            parent_id=question.id,
                            sub_question_index=idx,
                            image_url=question.image_url,  # 共享父题的拼接图
                            student_answer=sq.student_answer,
                            correct_answer=sq.correct_answer,
                            score=sq.score,
                            full_score=sq.full_score,
                            analysis_detail=sq.analysis_detail,
                            question_type=sq.question_type or grade_result.question_type,
                            common_mistakes=sq.common_mistakes,
                            confidence_score=sq.confidence,
                            status=QuestionStatus.COMPLETED if sq.confidence >= 0.3 else QuestionStatus.FAILED,
                            page_index=question.page_index,
                            bbox_x=question.bbox_x,
                            bbox_y=question.bbox_y,
                            bbox_w=question.bbox_w,
                            bbox_h=question.bbox_h,
                        )
                        db.add(child)

                        # 提取子题知识点
                        child_kps = sq.knowledge_points or []
                        if sq.analysis_detail:
                            try:
                                kps = await extractor.extract(sq.analysis_detail)
                                child.knowledge_points = extractor.merge(child_kps, kps)
                            except Exception:
                                child.knowledge_points = child_kps
                        else:
                            child.knowledge_points = child_kps

                        if sq.score is not None:
                            total_score += sq.score
                        for kp in (child.knowledge_points or []):
                            name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                            all_knowledge_points.add(name)
                            parent_kps.add(name)

                    # 父题知识点 = 所有子题知识点的并集，再精简到约5个
                    raw_kps = list(parent_kps) if parent_kps else (grade_result.knowledge_points or [])
                    question.knowledge_points = await extractor.trim(
                        raw_kps,
                        context=question.analysis_detail,
                        max_count=5,
                    )

                else:
                    # ── 普通单题（保持原有逻辑）──
                    question.student_answer = grade_result.student_answer
                    question.correct_answer = grade_result.correct_answer
                    question.score = grade_result.score
                    question.full_score = grade_result.full_score
                    question.analysis_detail = grade_result.analysis_detail
                    question.question_type = grade_result.question_type
                    question.common_mistakes = grade_result.common_mistakes
                    question.confidence_score = grade_result.confidence
                    question.status = QuestionStatus.COMPLETED if grade_result.confidence >= 0.3 else QuestionStatus.FAILED

                    # 提取知识点，并精简到约5个
                    if grade_result.analysis_detail:
                        try:
                            kps = await extractor.extract(grade_result.analysis_detail)
                            merged = extractor.merge(grade_result.knowledge_points, kps)
                            question.knowledge_points = await extractor.trim(
                                merged,
                                context=grade_result.analysis_detail,
                                max_count=5,
                            )
                        except Exception:
                            question.knowledge_points = await extractor.trim(
                                grade_result.knowledge_points or [],
                                context=grade_result.analysis_detail,
                                max_count=5,
                            )
                    else:
                        question.knowledge_points = await extractor.trim(
                            grade_result.knowledge_points or [],
                            context=None,
                            max_count=5,
                        )

                    if question.score is not None:
                        total_score += question.score
                    for kp in (question.knowledge_points or []):
                        name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                        all_knowledge_points.add(name)

            # Commit batch
            await db.commit()
            logger.info("[analyze] Batch done (%d questions), last score=%.1f",
                        len(batch), batch[-1][0].score or 0)
            batch_start += BATCH_SIZE

        # ── 完成 ──
        assignment = await db.get(Assignment, assignment_id)
        assignment.status = AssignmentStatus.COMPLETED
        assignment.total_score = total_score

        # 获取叶子题目（子题 + 无子题的独立题）用于生成总结
        leaf_records = []
        all_qs_result = await db.execute(
            select(Question)
            .where(Question.assignment_id == assignment_id)
            .order_by(Question.question_number, Question.sub_question_index)
        )
        all_qs = all_qs_result.scalars().all()
        # 收集有子题的父题ID
        parent_ids = {q.parent_id for q in all_qs if q.parent_id is not None}
        for q in all_qs:
            # 跳过父题容器（有子题且自身是父题）
            if q.id in parent_ids:
                continue
            leaf_records.append((q, None))

        # Generate AI summary via LLM for a teacher-like overall analysis
        error_count = sum(1 for q, _ in leaf_records
                         if q.score is not None and q.full_score is not None and q.score < q.full_score)
        try:
            ai_summary = await _generate_assignment_summary(
                question_records=leaf_records,
                total_score=total_score,
                q_count=len(leaf_records),
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
    根据所有叶子题目的 score 重新计算作业总分。
    只汇总子题（parent_id IS NOT NULL）和无子题的独立题（parent_id IS NULL 且无 child），
    跳过父题容器（parent_id IS NULL 但有子题），避免重复计算。

    如果传入 db session 则使用传入的，否则创建新的 session。

    user_id: 可选，传入时校验作业所有权，防止越权操作。
    """
    from sqlalchemy import select, func, exists, and_
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment
    from app.models.question import Question

    async def _do(session):
        if user_id is not None:
            assignment = await session.get(Assignment, assignment_id)
            if not assignment or assignment.creator_id != user_id:
                logger.warning(
                    "[recalc] Ownership check failed for assignment %d (user=%s)",
                    assignment_id, user_id,
                )
                return

        # 找出所有是"父题容器"的题目ID（被其他题目通过 parent_id 引用的）
        parent_ids_result = await session.execute(
            select(Question.parent_id).where(
                Question.assignment_id == assignment_id,
                Question.parent_id.isnot(None),
            ).distinct()
        )
        parent_ids = set(parent_ids_result.scalars().all())

        # 汇总所有非父题容器的题目分数
        from sqlalchemy import not_
        query = select(
            func.coalesce(func.sum(Question.score), 0),
        ).where(Question.assignment_id == assignment_id)
        if parent_ids:
            query = query.where(not_(Question.id.in_(parent_ids)))

        total_score = (await session.execute(query)).scalar() or 0.0
        assignment = await session.get(Assignment, assignment_id)
        if assignment:
            assignment.total_score = float(total_score)
            await session.commit()
            logger.info(
                "[recalc] Assignment %d total_score updated to %.1f (leaf questions only, skipped %d parents)",
                assignment_id, float(total_score), len(parent_ids),
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
    """单题重分析（支持大题套小题：重分析父题时会先删除所有子题再重建）"""
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.question import Question
    from app.services.file_upload import StorageService

    try:
        await _do_reanalyze_inner(question_id, remark)
    except Exception as e:
        logger.error("[reanalyze_question] Fatal error for question %d: %s", question_id, e, exc_info=True)
        try:
            async with async_session_factory() as db:
                result = await db.execute(select(Question).where(Question.id == question_id))
                question = result.scalar_one_or_none()
                # 只要不是已完成的正常状态，都标记 FAILED
                # （PENDING 可能因 flush 未 commit 被回滚，所以不判断具体状态）
                if question and question.status != QuestionStatus.COMPLETED:
                    question.status = QuestionStatus.FAILED
                    question.analysis_detail = f"重分析异常: {str(e)}"
                    await db.commit()
        except Exception as mark_err:
            logger.error("[reanalyze_question] Failed to mark question %d as FAILED: %s", question_id, mark_err)


async def _do_reanalyze_inner(question_id: int, remark: str | None = None):
    """单题重分析实现"""
    from sqlalchemy import select
    from app.db.session import async_session_factory
    from app.models.question import Question
    from app.models.assignment import Assignment
    from app.services.file_upload import StorageService

    async with async_session_factory() as db:
        result = await db.execute(select(Question).where(Question.id == question_id))
        question = result.scalar_one_or_none()
        if not question:
            raise ValueError(f"题目 {question_id} 不存在")

        # 获取作业的学科信息
        assign_result = await db.execute(select(Assignment).where(Assignment.id == question.assignment_id))
        assignment = assign_result.scalar_one_or_none()
        subject = assignment.subject if assignment else None

        # 加载作业创建者的助教个性化配置，重分析时同样生效
        from app.services.personality_service import load_grading_directive
        personality_directive = (
            await load_grading_directive(db, assignment.creator_id) if assignment else None
        )

        # 如果是父题，递归删除所有子题（包括孙子题等多级嵌套）
        if question.parent_id is None:
            # 收集所有后代ID（BFS遍历）
            to_delete_ids = [question_id]
            all_descendants = []
            while to_delete_ids:
                parent_ids_batch = to_delete_ids
                to_delete_ids = []
                children_result = await db.execute(
                    select(Question).where(Question.parent_id.in_(parent_ids_batch))
                )
                children = children_result.scalars().all()
                for child in children:
                    all_descendants.append(child)
                    to_delete_ids.append(child.id)
            # 从叶子节点向上删除
            for desc in reversed(all_descendants):
                await db.delete(desc)
            if all_descendants:
                logger.info("[reanalyze_question] Deleted %d descendants of question %d", len(all_descendants), question_id)
                await db.flush()

        question.status = QuestionStatus.PENDING
        await db.commit()  # 立即提交，确保异常时外部 handler 能检测到 PENDING 状态

        # Get image bytes from storage（如有答案图片则拼接后再发给AI）
        storage = StorageService()
        image_bytes = await storage.get_file_bytes(question.image_url)
        if image_bytes is None:
            raise ValueError(f"无法下载题目图片: {question.image_url}")

        # 如果有答案图片，下载并拼接
        if question.answer_image_url:
            try:
                a_bytes = await storage.get_file_bytes(question.answer_image_url)
                if a_bytes:
                    image_bytes = _stitch_question_answer(image_bytes, a_bytes)
                    logger.info("[reanalyze] 题目 #%d 已拼接答案图片", question.question_number)
                else:
                    logger.warning("[reanalyze] 题目 #%d 答案图片下载失败", question.question_number)
                    image_bytes = _compress_for_api(image_bytes)
            except Exception as stitch_err:
                logger.error("[reanalyze] 题目 #%d 答案拼接异常: %s，回退到仅使用题目图片",
                             question.question_number, stitch_err)
                image_bytes = _compress_for_api(image_bytes)
        else:
            # 无答案图片，直接压缩题目图片
            image_bytes = _compress_for_api(image_bytes)

        # Re-grade with remark (with timeout protection)
        import asyncio
        from app.services.ai_grader import AIGrader
        grader = AIGrader()
        try:
            results = await asyncio.wait_for(
                grader.grade_batch([image_bytes], remark=remark, subject=subject, personality_directive=personality_directive),
                # 单题重分析预算需容纳一次完整调用 + 一次重试：
                # _grade_chunk 最坏情况 = 180s(首试) + 1s(退避) + 180s(重试) = 361s，
                # 因此设为 6 分钟，避免复杂大题（如文言文/现代文阅读）在重试尚未完成时被外层取消。
                timeout=360,
            )
        except asyncio.TimeoutError:
            question.status = QuestionStatus.FAILED
            question.analysis_detail = "重分析超时（6分钟），请重试"
            await db.commit()
            await recalc_assignment_total(question.assignment_id, db)
            logger.warning("[reanalyze_question] Timed out for question %d", question_id)
            return
        except Exception as e:
            question.status = QuestionStatus.FAILED
            question.analysis_detail = f"评分异常: {str(e)}"
            await db.commit()
            await recalc_assignment_total(question.assignment_id, db)
            logger.error("[reanalyze_question] Grading failed for question %d: %s", question_id, e)
            return

        if not results:
            question.status = QuestionStatus.FAILED
            question.analysis_detail = "重分析失败：AI 未返回有效结果，请重试"
            await db.commit()
            await recalc_assignment_total(question.assignment_id, db)
            logger.warning("[reanalyze_question] Empty results for question %d", question_id)
            return

        gr = results[0]

        # ── 备注持久化（提前保存，确保异常路径也能看到备注内容）──
        if remark:
            question.manual_review_note = remark

        # ── 教师备注强制覆盖：解析备注中的明确纠正，覆盖AI识别结果 ──
        # 注意：即使 AI 评分返回空壳结果（API 全部重试失败），备注覆盖仍然生效，
        # 因为教师已经人工确认过，备注中的内容就是权威依据。
        from app.services.remark_parser import parse_remark_overrides, apply_remark_overrides
        remark_overrides = parse_remark_overrides(remark) if remark else {}
        if remark_overrides:
            logger.info("[reanalyze_question] 备注覆盖 question %d: %s", question_id, remark_overrides)
            apply_remark_overrides(gr, remark_overrides)
            if gr.sub_questions:
                for sq in gr.sub_questions:
                    apply_remark_overrides(sq, remark_overrides)

        # ── 检测 AI 评分是否返回了有效结果 ──
        # 当 _grade_chunk 的 API 调用全部重试失败时，会返回 _empty_grade_result()
        # （confidence=0.0 且所有字段为 None），此时不能当作正常结果写入数据库。
        grading_failed = (
            gr.confidence is not None and gr.confidence <= 0.01
            and gr.analysis_detail is None
            and gr.student_answer is None
            and not gr.sub_questions
        )

        # ── 评分失败时的兜底策略 ──
        if grading_failed:
            if remark_overrides:
                # 有教师备注覆盖：用覆盖值填充，标记为已完成
                logger.info(
                    "[reanalyze_question] AI 评分返回空结果，但备注覆盖提供 %d 个字段，"
                    "以备注为准完成 question %d",
                    len(remark_overrides), question_id,
                )
                # 用备注覆盖后的 gr 值写入（apply_remark_overrides 已设置 confidence=1.0）
                question.student_answer = gr.student_answer
                question.correct_answer = gr.correct_answer
                question.score = gr.score
                question.full_score = gr.full_score
                question.analysis_detail = gr.analysis_detail or f"（人工备注修正：{remark}）"
                question.question_type = gr.question_type
                question.confidence_score = gr.confidence
                question.common_mistakes = gr.common_mistakes
                question.status = QuestionStatus.COMPLETED
            else:
                # 无有效备注覆盖：标记失败，保留旧数据不被空结果覆盖
                logger.warning(
                    "[reanalyze_question] AI 评分返回空结果且无有效备注覆盖，"
                    "question %d 标记为失败，旧数据不覆盖",
                    question_id,
                )
                question.status = QuestionStatus.FAILED
                question.analysis_detail = (
                    f"重分析失败：AI 未返回有效评分结果。"
                    f"请尝试在备注中写明「学生答案：X；正确答案：Y；得分：Z」等关键信息，"
                    f"系统将自动识别并以备注为准完成分析。"
                )
                await db.commit()
                await recalc_assignment_total(question.assignment_id, db)
                return

        from app.services.knowledge_extractor import KnowledgeExtractor
        extractor = KnowledgeExtractor()

        if gr.sub_questions and len(gr.sub_questions) > 0:
            # ── 大题套小题重分析：父题为容器，重建子题 ──
            question.score = None
            question.full_score = None
            question.student_answer = None
            question.correct_answer = None
            question.question_type = _infer_parent_question_type(
                gr.question_type, gr.sub_questions
            )
            question.analysis_detail = f"本大题共 {len(gr.sub_questions)} 小题"
            question.confidence_score = gr.confidence
            question.status = QuestionStatus.COMPLETED
            question.common_mistakes = gr.common_mistakes

            parent_kps: set[str] = set()
            for idx, sq in enumerate(gr.sub_questions):
                # 有教师备注覆盖时，子题也不再因低置信度标为 FAILED
                # （备注覆盖已把 confidence 设为 1.0，阈值判断自然通过）
                child = Question(
                    assignment_id=question.assignment_id,
                    question_number=question.question_number,
                    parent_id=question.id,
                    sub_question_index=idx,
                    image_url=question.image_url,
                    student_answer=sq.student_answer,
                    correct_answer=sq.correct_answer,
                    score=sq.score,
                    full_score=sq.full_score,
                    analysis_detail=sq.analysis_detail,
                    question_type=sq.question_type or gr.question_type,
                    common_mistakes=sq.common_mistakes,
                    confidence_score=sq.confidence,
                    status=QuestionStatus.COMPLETED if sq.confidence >= 0.3 else QuestionStatus.FAILED,
                    page_index=question.page_index,
                    bbox_x=question.bbox_x,
                    bbox_y=question.bbox_y,
                    bbox_w=question.bbox_w,
                    bbox_h=question.bbox_h,
                )
                db.add(child)

                child_kps = sq.knowledge_points or []
                if sq.analysis_detail:
                    try:
                        kps = await extractor.extract(sq.analysis_detail)
                        child.knowledge_points = extractor.merge(child_kps, kps)
                    except Exception:
                        child.knowledge_points = child_kps
                else:
                    child.knowledge_points = child_kps

                for kp in (child.knowledge_points or []):
                    name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                    parent_kps.add(name)

            # 父题知识点精简到约5个
            raw_kps = list(parent_kps) if parent_kps else (gr.knowledge_points or [])
            question.knowledge_points = await extractor.trim(
                raw_kps,
                context=question.analysis_detail,
                max_count=5,
            )
        else:
            # ── 普通单题重分析 ──
            question.student_answer = gr.student_answer
            question.correct_answer = gr.correct_answer
            question.score = gr.score
            question.full_score = gr.full_score
            question.analysis_detail = gr.analysis_detail
            question.question_type = gr.question_type
            question.confidence_score = gr.confidence
            question.common_mistakes = gr.common_mistakes
            # 备注覆盖后置信度已被设为 1.0，正常评分结果置信度也已被 remark boost
            # 提升到 ≥0.85，此处统一按阈值判断，只有真正低置信度（<0.3）才标失败
            question.status = (
                QuestionStatus.COMPLETED
                if (gr.confidence is None or gr.confidence >= 0.3)
                else QuestionStatus.FAILED
            )

            # Re-extract knowledge points 并精简到约5个
            if gr.analysis_detail:
                kps = await extractor.extract(gr.analysis_detail)
                merged = extractor.merge(gr.knowledge_points, kps)
                question.knowledge_points = await extractor.trim(
                    merged,
                    context=gr.analysis_detail,
                    max_count=5,
                )
            else:
                question.knowledge_points = await extractor.trim(
                    gr.knowledge_points or [],
                    context=None,
                    max_count=5,
                )

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
                    "analysis": sq.analysis,
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
