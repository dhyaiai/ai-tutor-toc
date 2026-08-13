"""
作文批改后台任务。

上传作文时先创建记录立即返回（status=pending），AI 批改在本任务中异步执行：
- DEV 模式：dev_runner.run_async_in_background 在当前进程后台协程执行
- 生产模式：Celery worker 执行

任务流程：读记录 → 从存储读原文件 → 按格式走多模态/文本 LLM 批改 →
写回结果并置 completed；任何异常置 failed + error_message（不自动重试，
失败由用户在前端手动"重新批改"，避免重复烧 LLM 调用）。
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from app.tasks.celery_app import celery_app
except ImportError:
    celery_app = None


async def _do_correct_composition(correction_id: int) -> None:
    """
    批改单条作文记录（后台任务核心逻辑）。

    与 /compositions/correct 同步版的分工：
    - 请求内只做"存文件"（快），本任务做"读文件 → 转 base64/提文本 → LLM 批改 → 写回"（慢）
    - 所有异常在内部捕获并落库为 failed，绝不向上抛出（dev_runner 回调只负责记日志）
    """
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.composition import CompositionCorrection
    from app.services.composition_service import CompositionService
    from app.services.file_upload import StorageService

    # 标记批改中（先于可能较慢的 storage 读取，让前端尽快看到 correcting 状态）
    async with async_session_factory() as db:
        result = await db.execute(
            select(CompositionCorrection).where(CompositionCorrection.id == correction_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            # 记录已被删除，任务无事可做
            logger.warning("[composition] 记录 %d 不存在，跳过批改", correction_id)
            return
        if record.status == "failed" or record.status == "completed":
            # 幂等保护：已完成/已失败的记录不重复批改（防止重复触发）
            logger.info("[composition] 记录 %d 状态为 %s，跳过", correction_id, record.status)
            return
        record.status = "correcting"
        # 注意：必须 commit 而非 flush——flush 只发 SQL 不提交事务，
        # async with 退出时 session 关闭会回滚，状态变更全部丢失（记录永远卡在 pending）
        await db.commit()
        # 缓存批改所需参数，避免长任务期间 session 被复用/关闭
        params = {
            "user_id": record.user_id,
            "subject": record.subject,
            "grade": record.grade,
            "title": record.title,
            "essay_type": record.essay_type,
        }
        pdf_url = record.pdf_url

    try:
        # 加载用户助教个性化配置（性格/说话风格/评分严格度），与同步版行为一致
        async with async_session_factory() as db:
            from app.services.personality_service import build_grading_directive, load_personality
            personality = await load_personality(db, params["user_id"])
        strict_level = personality["strict_level"]
        personality_directive = build_grading_directive(personality)

        storage = StorageService()
        file_data = await storage.get_file_bytes(pdf_url)
        if not file_data:
            raise ValueError("原始文件数据读取失败，可能已被删除")

        # 从 pdf_url 路径中提取文件扩展名
        filename = pdf_url.rsplit("/", 1)[-1] if "/" in pdf_url else pdf_url
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        from app.api.v1.compositions import VISION_FORMATS, _extract_text_from_bytes, _file_to_base64_images

        service = CompositionService()
        content = ""
        if ext in VISION_FORMATS:
            # PDF/图片 → 多模态视觉 LLM 识别+批改
            images = _file_to_base64_images(file_data, filename)
            result_data = await service.correct_multimodal(
                images=images,
                subject=params["subject"],
                grade=params["grade"],
                title=params["title"],
                essay_type=params["essay_type"],
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
            content = result_data.get("content", "")
        elif ext in ("docx", "doc", "txt"):
            # 文本格式 → 提取文本 → 文本 LLM 批改
            content = _extract_text_from_bytes(file_data, filename)
            if not content.strip():
                raise ValueError("未能从文件中提取到文字内容，请检查文件")
            result_data = await service.correct(
                content=content,
                subject=params["subject"],
                grade=params["grade"],
                title=params["title"],
                essay_type=params["essay_type"],
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
        else:
            # 其他格式 → 用已存储的文本批改
            content = ""
            result_data = await service.correct(
                content=content,
                subject=params["subject"],
                grade=params["grade"],
                title=params["title"],
                essay_type=params["essay_type"],
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
    except Exception as exc:
        logger.error("[composition] 记录 %d 批改失败: %s", correction_id, exc, exc_info=True)
        # 失败落库：记录失败原因，前端展示并可手动重新批改
        async with async_session_factory() as db:
            result = await db.execute(
                select(CompositionCorrection).where(CompositionCorrection.id == correction_id)
            )
            record = result.scalar_one_or_none()
            if record:
                record.status = "failed"
                record.error_message = str(exc)[:500]
                await db.commit()
        return

    # 批改成功：写回结果
    async with async_session_factory() as db:
        result = await db.execute(
            select(CompositionCorrection).where(CompositionCorrection.id == correction_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            return
        record.total_score = result_data["total_score"]
        record.full_score = result_data["full_score"]
        record.word_count = result_data.get("word_count", 0)
        record.content = content
        record.dimension_scores = result_data["dimension_scores"]
        record.deductions = result_data.get("deductions", {})
        record.revision_suggestions = result_data["revision_suggestions"]
        record.overall_comment = result_data["overall_comment"]
        record.polish_advice = result_data["polish_advice"]
        record.sample_essay = result_data["sample_essay"]
        record.status = "completed"
        record.error_message = None
        await db.commit()

        # 同步更新知识状态（与同步版行为一致，失败不阻塞批改结果落库）
        try:
            from app.services.knowledge_tracker import KnowledgeTracker
            tracker = KnowledgeTracker(db)
            await tracker.update(
                user_id=record.user_id,
                knowledge_points=[{
                    "point_name": f"{record.subject}写作能力",
                    "subject": record.subject,
                    "mastery_change": 1 if result_data["total_score"] / result_data["full_score"] > 0.7 else -1,
                    "behavior_type": "作文提升点" if result_data["total_score"] / result_data["full_score"] > 0.7 else "作文扣分点",
                }],
                update_source="作文批改",
            )
        except Exception:
            logger.exception("[composition] 记录 %d 知识状态同步失败", correction_id)

    logger.info("[composition] 记录 %d 批改完成", correction_id)


def correct_composition_dev(correction_id: int):
    """DEV 模式入口：当前进程后台协程执行（替代 Celery delay）。"""
    from app.tasks.dev_runner import run_async_in_background
    logger.info("[composition] Starting correction for record %d", correction_id)
    run_async_in_background(_do_correct_composition(correction_id), correction_id)


if celery_app is not None:
    # 注意：任务级 soft/hard time_limit 必须覆盖多模态批改预算——作文多图视觉批改
    # 是多次 LLM 串行调用（实测可达 600s+），默认全局 300/360s 会被 SoftTimeLimitExceeded
    # 杀死并在内部 except 中落库 failed，导致每次重试必败
    @celery_app.task(bind=True, name="correct_composition",
                     soft_time_limit=1800, time_limit=2400)
    def correct_composition(self, correction_id: int):
        """生产模式 Celery 任务：在 worker 中执行批改。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_do_correct_composition(correction_id))
        finally:
            loop.close()
else:
    correct_composition = None  # type: ignore
