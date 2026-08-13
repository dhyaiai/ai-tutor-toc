"""上传试题转录接口——上传试卷文件（Word/PDF/图片），自动转录拆题 + 知识点标注。

流程（异步任务 + 轮询，复用同类题生成的 TTLCache + run_async_in_background 模式）：
1. POST /api/v1/upload-questions：接收一个或多个文件（共享同一份年级/科目/学期/题型表单），校验后立即 202 返回 task_id
2. 后台任务：逐文件解析文档（docx/python-docx、PDF/fitz 文本或渲染、图片）→ 视觉 LLM 转录结构化 JSON
   → 写入 ai_generated_questions（独立题/大题分组，携带自有 grade/subject/semester 元数据）
   → 自动收藏（UserFavorite, item_type="ai"）；单文件失败跳过不中断整批，结果聚合一次返回
3. GET /api/v1/upload-questions/{task_id}：前端轮询，completed 时返回收藏条目列表
   （envelope 结构与 /favorites 列表完全一致，可直接喂给收藏页编辑弹窗 QuestionEditModal）

设计要点：
- 转录成功即自动收藏：上传题才能出现在收藏页并复用编辑弹窗检查/修改
- 任务函数最外层 try/except 必写终态缓存（失败置 failed），
  否则 dev_runner 只记日志，缓存永久停在 processing，前端会无限轮询
- 表单年级/科目/学期/题型白名单与前端 filterConfig / 错题列表保持一致
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.ai_question import AIGeneratedQuestion
from app.models.favorite import UserFavorite
from app.services.file_upload import StorageService
from app.services.question_transcriber import (
    prepare_document,
    transcribe,
    VALID_QUESTION_TYPES,
)
# 复用收藏列表条目构建：保证上传返回的条目结构与 /favorites 完全一致
# （favorites.py 不反向依赖本模块，无循环导入）
from app.api.v1.favorites import _build_ai_entries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload-questions", tags=["upload-questions"])

# ── 上传文件校验（扩展名白名单含 .docx，本地魔数表，不改动作业上传的全局表）──
_UPLOAD_EXTENSIONS = {".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_DOCX_MAGIC = b"PK\x03\x04"  # docx 是 zip 容器
_MAGIC_MAP = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".webp": b"RIFF",
    ".docx": _DOCX_MAGIC,
}

# ── 表单白名单（与前端 utils/filterConfig.ts 的选项一致）──
_VALID_GRADES = frozenset({
    "高三", "高二", "高一", "初三", "初二", "初一",
    "六年级", "五年级", "四年级", "三年级", "二年级", "一年级",
})
_VALID_SUBJECTS = frozenset({
    "语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理",
})
_VALID_SEMESTERS = frozenset({"上学期", "下学期"})

# ── 任务缓存：task_id(uuid4.hex) → {"status", "entries", "error", "user_id"} ──
# 转录任务状态缓存 TTL：任务最坏 600×N 秒（N ≤ 10，Celery 硬超时 6600s），
# 统一取硬超时上限。注意不能用默认 1800s：长转录任务（多文件）结束前缓存
# 会先过期，前端轮询 not_found 被当失败，用户重复上传会重复落库
_TRANSCRIPTION_CACHE_TTL = 6600
_upload_cache: TTLCache = TTLCache(maxsize=200, ttl=_TRANSCRIPTION_CACHE_TTL)


async def _set_upload_cache(task_id: str, value: dict, ttl: int = _TRANSCRIPTION_CACHE_TTL) -> None:
    """写转录任务状态：DEV 写进程内 TTLCache；生产写 Redis（跨 worker 共享）。

    生产多 worker 部署时任务跑在 worker A、轮询打到 worker B，进程内缓存互相
    不可见，必须持久化到 Redis（见 services/redis_state.py）。
    """
    _upload_cache[task_id] = value  # TTLCache 实例 ttl 已按任务最坏时长配置
    if not get_settings().DEV_MODE:
        from app.services.redis_state import redis_state_set
        await redis_state_set(f"upload:{task_id}", value, ttl=ttl)


async def _get_upload_cache(task_id: str) -> dict | None:
    """读转录任务状态（生产模式从 Redis 读，与 _set_upload_cache 对应）。"""
    if get_settings().DEV_MODE:
        return _upload_cache.get(task_id)
    from app.services.redis_state import redis_state_get
    return await redis_state_get(f"upload:{task_id}")

# 整条转录流水线的总超时（文档解析 + 多模态 LLM 重试最坏情况）
_TRANSCRIPTION_TOTAL_TIMEOUT = 600
_STEP_TIMEOUT = 60  # 单步（文档解析等同步活）超时

# 一次上传的文件数上限（多文件串行转录，文件越多耗时越长，10 个为合理上限）
_MAX_FILES = 10


async def _validate_and_read_upload_file(file: UploadFile) -> bytes:
    """校验扩展名 / 魔数 / 大小并分块读取（中途截断防内存打爆）。

    逻辑与作业上传 assignments._validate_and_read_file 一致，扩展名集额外含 .docx。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    if ext not in _UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型，仅允许：{', '.join(sorted(_UPLOAD_EXTENSIONS))}",
        )

    max_size = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024
    max_size_mb = get_settings().MAX_UPLOAD_SIZE_MB
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail=f"文件过大（最大 {max_size_mb}MB）")
        chunks.append(chunk)
    file_data = b"".join(chunks)

    # 魔数校验：webp 魔数只占前 4 字节（RIFF 后接 WEBP），其余用 startswith
    magic = _MAGIC_MAP.get(ext)
    if magic:
        prefix = file_data[:4] if ext == ".webp" else file_data
        if not prefix.startswith(magic):
            raise HTTPException(status_code=400, detail=f"无效的 {ext.upper()} 文件")
    return file_data


async def _run_transcription_task(
    task_id: str, user_id: int, files: list[tuple[bytes, str, str | None]], meta: dict
):
    """后台转录任务：逐文件解析 → LLM 转录 → 落库（AI 题 + 自动收藏）→ 缓存终态。

    多文件串行处理（避免同时打爆多模态 LLM 限流），超时上限按文件数扩展。
    files 元素为 (文件字节, 文件名, 已落盘路径)：生产模式请求内先落盘，
    路径直接复用为展示图 URL 避免 worker 重复保存；DEV 模式第三项为 None。
    任何异常（含超时）都必须写入 failed 终态缓存，前端轮询才能终止；
    绝不静默退出让缓存停在 processing。
    """
    try:
        # 总超时按文件数扩展：单文件最坏 600s，N 个文件最坏 N 倍
        await asyncio.wait_for(
            _do_transcription(task_id, user_id, files, meta),
            timeout=_TRANSCRIPTION_TOTAL_TIMEOUT * max(1, len(files)),
        )
    except asyncio.TimeoutError:
        logger.error("转录任务 %s 整体超时", task_id)
        await _set_upload_cache(task_id, {"status": "failed", "error": "处理超时，请稍后重试", "user_id": user_id})
    except Exception as exc:
        logger.exception("转录任务 %s 失败: %s", task_id, exc)
        # 用户可读的错误信息：HTTPException 取 detail，其余取 str
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        await _set_upload_cache(task_id, {"status": "failed", "error": detail or "转录失败，请重试", "user_id": user_id})


async def _do_transcription(
    task_id: str, user_id: int, files: list[tuple[bytes, str]], meta: dict
):
    """转录任务主体：逐文件转录并聚合所有文件的收藏条目。

    单个文件转录失败（内容非法/无有效题目/解析异常）只跳过该文件，不中断整批；
    所有文件均未转录出有效题目才整体失败。
    """
    await _set_upload_cache(task_id, {"status": "processing", "user_id": user_id})

    all_entries: list[dict] = []
    saved_total = 0
    for file_data, filename, original_path in files:
        try:
            entries, saved = await _transcribe_one_file(
                user_id, file_data, filename, meta, original_path
            )
        except HTTPException as exc:
            # 单文件失败：跳过并继续下一份（常见于该文件内容非试题）
            logger.warning("文件 %s 转录失败，跳过: %s", filename, exc.detail)
        except Exception:
            logger.warning("文件 %s 转录异常，跳过", filename, exc_info=True)
        else:
            all_entries.extend(entries)
            saved_total += saved

    if not all_entries:
        raise HTTPException(status_code=422, detail="未能识别出有效题目，请确认文件内容为试题")

    await _set_upload_cache(task_id, {
        "status": "completed",
        "entries": all_entries,
        "user_id": user_id,
    })
    logger.info(
        "转录任务 %s 完成：%d 个文件，%d 道题（收藏 %d 条）",
        task_id, len(files), saved_total, len(all_entries),
    )


async def _transcribe_one_file(
    user_id: int, file_data: bytes, filename: str, meta: dict,
    original_path: str | None = None,
) -> tuple[list[dict], int]:
    """转录单个文件：保存原文件 → 解析文档 → LLM 转录 → 落库（AI 题 + 自动收藏）。

    每个文件独立事务：此文件成功即提交，不因后续文件失败而回滚。
    返回 (收藏条目列表, 落库题目数)。

    original_path：生产模式请求内已落盘的对象路径，直接复用为展示图 URL，
    避免 worker 内重复保存第二份（同一文件存两份是存储泄漏）；DEV 传 None，
    走本函数内 save_original。
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        # 1. 保存原文件（存储失败不阻断任务，仅记日志——转录不依赖文件留存）
        original_url: str | None = None
        storage = StorageService()
        if original_path:
            # 生产模式：请求内已落盘，直接复用路径，不再重复存储
            original_url = original_path
        else:
            try:
                original_url = await asyncio.wait_for(
                    storage.save_original(file_data, filename, user_id), timeout=_STEP_TIMEOUT
                )
            except Exception:
                logger.warning("转录任务保存原文件 %s 失败（忽略）", filename, exc_info=True)

        # 2. 解析文档 → 文本或图片形态
        doc = await prepare_document(file_data, filename)

        # 3. 确定编辑弹窗对照用的原图 URL（每题共用同一张图，用户自行对照）：
        #    - 图片文件：原文件即展示图，直接复用已保存的 original_url（避免重复存储）
        #    - 扫描版 PDF：原文件是 PDF（<img> 无法显示），把渲染出的首页另存为展示图
        #    - 文本型 PDF/docx：无展示图，保持 NULL（编辑弹窗回落 SVG 配图/空态）
        display_url: str | None = None
        if doc["kind"] == "images" and doc["images"]:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in ("png", "jpg", "jpeg", "webp"):
                display_url = original_url
            else:
                try:
                    display_url = await asyncio.wait_for(
                        storage.save_file(
                            f"originals/{user_id}/{uuid.uuid4()}.jpg", doc["images"][0]
                        ),
                        timeout=_STEP_TIMEOUT,
                    )
                except Exception:
                    logger.warning("保存扫描 PDF 首页展示图失败（忽略）", filename, exc_info=True)

        # 4. 视觉 LLM 转录（拆题 + 知识点标注）
        questions = await transcribe(doc, meta)
        if not questions:
            raise HTTPException(status_code=422, detail="未能识别出有效题目，请确认文件内容为试题")

        # 5. 写入 AI 题（独立题直插；大题 group_id=uuid4 + sub_question_index 从 0 递增）
        saved_ids: list[int] = []
        anchor_ids: list[int] = []  # 收藏锚点（大题=组内 index 最小行，即首行）
        for q in questions:
            if q.get("is_big_question"):
                group_id = uuid.uuid4().hex
                children = q["children"]
                child_ids: list[int] = []
                for idx, child in enumerate(children):
                    record = AIGeneratedQuestion(
                        user_id=user_id,
                        question_text=child["question_text"],
                        answer=child["answer"],
                        analysis=child.get("analysis"),
                        question_type=child.get("question_type"),
                        knowledge_point=child.get("knowledge_point"),
                        difficulty="medium",
                        options=child.get("options"),
                        group_id=group_id,
                        sub_question_index=idx,
                        question_context=q.get("question_context"),
                        image_url=display_url,  # 原图 URL（编辑弹窗对照用）
                        # 自有试题标记（收藏页"题目来源"筛选用）
                        source="upload",
                        # 上传题自有元数据（收藏页/列表筛选用）
                        grade=meta.get("grade"),
                        subject=meta.get("subject"),
                        semester=meta.get("semester"),
                    )
                    db.add(record)
                    await db.flush()
                    child_ids.append(record.id)
                    saved_ids.append(record.id)
                anchor_ids.append(child_ids[0])  # 大题锚点=首行（与 _normalize_anchor 一致）
            else:
                record = AIGeneratedQuestion(
                    user_id=user_id,
                    question_text=q["question_text"],
                    answer=q["answer"],
                    analysis=q.get("analysis"),
                    question_type=q.get("question_type"),
                    knowledge_point=q.get("knowledge_point"),
                    difficulty="medium",
                    options=q.get("options"),
                    image_url=display_url,  # 原图 URL（编辑弹窗对照用）
                    source="upload",
                    grade=meta.get("grade"),
                    subject=meta.get("subject"),
                    semester=meta.get("semester"),
                )
                db.add(record)
                await db.flush()
                saved_ids.append(record.id)
                anchor_ids.append(record.id)

        # 6. 自动收藏（幂等：查重后插入，与 favorites.add_favorite 语义一致）
        fav_rows: list[UserFavorite] = []
        for anchor_id in anchor_ids:
            existing = (
                await db.execute(
                    select(UserFavorite).where(
                        UserFavorite.user_id == user_id,
                        UserFavorite.item_type == "ai",
                        UserFavorite.question_id == anchor_id,
                    ),
                    execution_options={"autoflush": False},
                )
            ).scalar_one_or_none()
            if existing is not None:
                fav_rows.append(existing)
                continue
            fav = UserFavorite(user_id=user_id, item_type="ai", question_id=anchor_id)
            db.add(fav)
            await db.flush()
            await db.refresh(fav)
            fav_rows.append(fav)

        await db.commit()

        # 7. 复用收藏列表条目构建，返回与 /favorites 一致的 envelope 结构
        user = await db.get(User, user_id)
        entries = await _build_ai_entries(db, user, fav_rows, None, None, None, None)

        logger.info("文件 %s 转录完成：%d 道题（收藏 %d 条）", filename, len(saved_ids), len(anchor_ids))
        return entries, len(saved_ids)


@router.post("", status_code=202)
async def upload_questions(
    files: list[UploadFile] = File(...),
    grade: str = Form(...),
    subject: str = Form(...),
    semester: str = Form(...),
    question_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传试卷文件并创建转录任务（202 立即返回，结果需轮询）。

    支持一次上传多个文件（最多 {_MAX_FILES} 个），所有文件共享同一份
    年级/科目/学期/题型元数据，转录结果聚合后一次返回。
    """
    # 表单值白名单校验（防止脏数据写入筛选列）
    if grade not in _VALID_GRADES:
        raise HTTPException(status_code=400, detail=f"无效的年级: {grade}")
    if subject not in _VALID_SUBJECTS:
        raise HTTPException(status_code=400, detail=f"无效的科目: {subject}")
    if semester not in _VALID_SEMESTERS:
        raise HTTPException(status_code=400, detail=f"无效的学期: {semester}")
    if question_type not in VALID_QUESTION_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的题型: {question_type}")

    # 文件数量校验：空列表或超过上限直接拒绝
    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的试卷文件")
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {_MAX_FILES} 个文件")

    # 逐个校验（扩展名/魔数/大小）并读取内容
    prepared: list[tuple[bytes, str]] = []
    for f in files:
        data = await _validate_and_read_upload_file(f)
        prepared.append((data, f.filename or "upload"))

    task_id = uuid.uuid4().hex
    await _set_upload_cache(task_id, {"status": "pending", "user_id": current_user.id})
    meta = {
        "grade": grade,
        "subject": subject,
        "semester": semester,
        "question_type": question_type,
    }

    if get_settings().DEV_MODE:
        # run_async_in_background 持有任务引用，防止 create_task 裸任务被 GC 回收
        #（回收后缓存永久停在 pending，前端轮询永不返回）
        from app.tasks.dev_runner import run_async_in_background
        run_async_in_background(
            _run_transcription_task(
                task_id, current_user.id, [(d, f, None) for d, f in prepared], meta
            )
        )
    else:
        # 生产模式：文件内容（bytes）体积大，不宜进 Celery 消息（Redis 内存受限），
        # 先在请求内落盘，worker 任务从存储读回 bytes 后走同一份转录逻辑；
        # 状态写 Redis（见 _set_upload_cache），多 worker 下轮询接口才能跨进程读到。
        from app.services.file_upload import StorageService
        from app.tasks.analysis_tasks import transcribe_upload_files
        storage = StorageService()
        stored_paths: list[str] = []
        for file_data, filename in prepared:
            try:
                stored_paths.append(await storage.save_original(file_data, filename, current_user.id))
            except Exception:
                # 落盘失败的文件在 worker 里以空路径跳过（记日志，不中断整批）
                logger.exception("转录文件 %s 落盘失败（跳过）", filename)
                stored_paths.append("")
        transcribe_upload_files.delay(task_id, current_user.id, stored_paths, meta)

    return {"task_id": task_id, "status": "pending", "message": "转录任务已创建"}


@router.get("/{task_id}")
async def get_upload_result(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询转录任务结果：processing（进行中）/ completed（含收藏条目）/ failed / not_found。"""
    cached = await _get_upload_cache(task_id)
    # 任务不存在 / TTL 过期 / 跨用户访问（防止枚举他人任务）一律返回 not_found
    if not cached or cached.get("user_id") != current_user.id:
        return {"status": "not_found"}

    if cached["status"] == "completed":
        return {"status": "completed", "entries": cached["entries"]}
    if cached["status"] == "failed":
        return {"status": "failed", "error": cached.get("error", "转录失败")}
    return {"status": cached["status"]}
