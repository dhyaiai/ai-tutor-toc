from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, case
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment, AssignmentStatus, LayoutType
from app.models.question import Question, QuestionStatus
from app.schemas.assignment import (
    AssignmentUpload,
    AssignmentListResponse,
    AssignmentDetailResponse,
    PaginatedResponse,
)
from app.schemas.question import QuestionResponse
from app.services.file_upload import StorageService
from app.core.config import get_settings
from app.utils.pdf_renderer_utils import _rotate_and_cut, _render_pdf_pages_bgr, _merge_images
import asyncio
import json
import logging
import time
from io import BytesIO

logger = logging.getLogger(__name__)

from pydantic import BaseModel

router = APIRouter(prefix="/assignments", tags=["assignments"])

# 僵尸状态自愈节流记录：assignment_id → 上次自愈时间戳
# 防止前端轮询详情页时反复触发 reconcile + recalc（每次都会 commit 写库）
_HEAL_THROTTLE_SECONDS = 60
_last_heal_timestamps: dict[int, float] = {}


class ManualSplitRegion(BaseModel):
    question_number: int
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    draw_order: int = 0  # 绘制顺序，同题多区域时决定合并后的排列先后
    rotation: int = 0     # 图片旋转角度：0/90/180/270，应用到该页面后再裁切
    # 区域类型：question=普通题目区域；answer_sheet=客观题识别区（答题卡），
    # 不创建 Question 记录，切图保存到作业级 answer_sheet_image_url，
    # 评分时作为 [Answer Sheet] 第三图源拼入每道题
    region_type: str = "question"


class ManualSplitRequest(BaseModel):
    regions: list[ManualSplitRegion]


class AnswerSplitRegion(BaseModel):
    """答案切割区域 —— 每个区域对应一道已有题目的答案部分"""
    question_number: int  # 对应已有题目的题号
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    rotation: int = 0  # 0/90/180/270


class AnswerSplitRequest(BaseModel):
    regions: list[AnswerSplitRegion]
    answer_file_url: str  # 从上一步 answer-pages 返回中获取，用于定位答案文件


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

# 上传大小上限统一读取 config.MAX_UPLOAD_SIZE_MB（避免硬编码与配置漂移）
# 注：原硬编码 MAX_FILE_SIZE = 50MB 已移除，见 _validate_and_read_file

# Image magic bytes
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
WEBP_MAGIC = b"RIFF"
PDF_MAGIC = b"%PDF"

_MAGIC_MAP = {
    ".pdf": PDF_MAGIC,
    ".png": PNG_MAGIC,
    ".jpg": JPEG_MAGIC,
    ".jpeg": JPEG_MAGIC,
    ".webp": WEBP_MAGIC,
}


async def _validate_and_read_file(file: UploadFile) -> bytes:
    """Validate file extension, size and magic bytes. Returns file bytes.

    分块读取 + 中途截断：避免超大请求先整体 read() 进内存再判大小
    （恶意并发大文件会把 worker 内存打爆），超限立即抛 413 中止读取。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型，仅允许：{', '.join(ALLOWED_EXTENSIONS)}",
        )

    max_size = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024
    max_size_mb = get_settings().MAX_UPLOAD_SIZE_MB
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 每次最多读 1MB
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail=f"文件过大（最大 {max_size_mb}MB）")
        chunks.append(chunk)
    file_data = b"".join(chunks)

    # Validate magic bytes
    magic = _MAGIC_MAP.get(ext)
    if magic:
        if ext == ".webp":
            if not file_data[:4].startswith(magic):
                raise HTTPException(status_code=400, detail=f"无效的 {ext.upper()} 文件")
        elif not file_data.startswith(magic):
            raise HTTPException(status_code=400, detail=f"无效的 {ext.upper()} 文件")

    return file_data


def _image_bytes_to_pdf(file_data: bytes) -> bytes:
    """将图片字节（png/jpg/jpeg/webp）转换为单页 PDF 字节。"""
    from PIL import Image

    img = Image.open(BytesIO(file_data))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _merge_pdf_bytes(file_datas: list[tuple[str, bytes]]) -> bytes:
    """合并多个文件字节（PDF/图片）为单个 PDF 字节。

    同步 CPU+IO 重活（fitz 逐文件插入 + 重写 PDF），多文件大卷可达秒级，
    必须在 asyncio.to_thread 中调用。文件字节已由调用方下载完毕，
    这里只做纯内存合并，不触碰存储。

    Args:
        file_datas: [(原文件名, 文件字节), ...]，扩展名决定合并方式

    Raises:
        ValueError: 不支持的扩展名 / 合并结果为空
    """
    import fitz

    merged_doc = fitz.open()  # 新建空白 PDF 文档
    try:
        for fp, file_data in file_datas:
            ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else ""
            if ext == "pdf":
                # PDF → 打开并插入所有页面
                src_doc = fitz.open(stream=file_data, filetype="pdf")
                try:
                    merged_doc.insert_pdf(src_doc)
                finally:
                    src_doc.close()
            elif ext in ("png", "jpg", "jpeg", "webp"):
                # 图片 → Pillow 打开 → 转为 PDF → 插入
                src_doc = fitz.open(stream=_image_bytes_to_pdf(file_data), filetype="pdf")
                try:
                    merged_doc.insert_pdf(src_doc)
                finally:
                    src_doc.close()
            else:
                raise ValueError(f"不支持合并的文件类型：.{ext}")

        if len(merged_doc) == 0:
            raise ValueError("合并后PDF为空，请检查上传的文件")
        return merged_doc.tobytes()
    finally:
        merged_doc.close()


def _assert_owned_storage_path(file_path: str, user_id: int, allowed_prefixes: tuple[str, ...] = ("originals", "answers")) -> None:
    """校验存储路径归属：只允许读取当前用户目录下自己上传的文件。

    客户端传入的 file_path / file_paths 直接拼接进 storage 读取（get_file_bytes
    按路径直读本地目录或 MinIO 对象），若不校验可跨用户读取他人文件
    （含 reports/ 下的学情报告）。同时拒绝 `..` 防止路径穿越。

    注意：Windows 下反斜杠段（如 `..\\`）可绕过按 '/' 切分的 `..` 检查，
    必须先统一替换为 '/' 再校验（本地存储模式路径会经 pathlib 规范化后读取）。
    使用 pathlib.Path.resolve() 彻底防御路径穿越（含符号链接攻击）。
    """
    from pathlib import Path
    from app.core.config import get_settings

    settings = get_settings()
    # 统一路径分隔符
    normalized = file_path.replace("\\", "/")
    # 基础检查：拒绝明显的路径穿越尝试
    if ".." in normalized.split("/"):
        raise HTTPException(status_code=400, detail=f"非法的文件路径：{file_path}")
    # 校验前缀归属：必须是当前用户的指定目录
    prefix_valid = any(normalized.startswith(f"{prefix}/{user_id}/") for prefix in allowed_prefixes)
    if not prefix_valid:
        raise HTTPException(
            status_code=403,
            detail="无权访问该文件：只能使用自己上传的文件",
        )
    # 深度防御：本地存储模式下，resolve 后确认仍在存储根目录内
    if settings.DEV_MODE:
        storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
        target_path = (storage_root / normalized).resolve()
        if not target_path.is_relative_to(storage_root):
            raise HTTPException(status_code=400, detail=f"路径穿越攻击被拦截：{file_path}")


async def _ensure_pdf(
    file_path: str,
    storage: StorageService,
    user_id: int,
) -> str:
    """
    确保原卷以 PDF 格式存储：已是 PDF 直接返回原路径；
    图片文件则转换为单页 PDF 存入 storage，返回新的 file_url。
    """
    # 归属校验：file_path 由客户端传入，防止跨用户读取他人文件
    _assert_owned_storage_path(file_path, user_id)
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext == "pdf":
        return file_path

    file_data = await storage.get_file_bytes(file_path)
    if not file_data:
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败：{file_path}，请重新上传",
        )

    # Pillow 转 PDF 是同步 CPU 重活，线程池执行避免阻塞事件循环
    pdf_bytes = await asyncio.to_thread(_image_bytes_to_pdf, file_data)
    base_name = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    pdf_name = (base_name.rsplit(".", 1)[0] if "." in base_name else base_name) + ".pdf"
    file_url = await storage.save_original(pdf_bytes, pdf_name, user_id)
    logger.info(f"Converted {file_path} to PDF: {file_url}")
    return file_url


async def _merge_files_to_pdf(
    file_paths: list[str],
    storage: StorageService,
    user_id: int,
) -> str:
    """
    将多个文件（图片/PDF）按数组顺序合并为一个 PDF，保存到 storage，返回合并后的 file_url。

    合并策略：
    - PDF 文件：逐页插入到目标文档
    - 图片文件（png/jpg/jpeg/webp）：先用 Pillow 转为 PDF 再插入
    - 文件顺序由 file_paths 数组顺序决定（前端保证 = 用户排列的顺序）
    """
    # 归属校验 + 下载（异步 IO 保持在事件循环）
    file_datas: list[tuple[str, bytes]] = []
    for fp in file_paths:
        # 归属校验：file_paths 由客户端传入，防止跨用户读取他人文件
        _assert_owned_storage_path(fp, user_id)
        # 从 storage 读取文件内容（支持 MinIO 和本地存储）
        file_data = await storage.get_file_bytes(fp)
        if not file_data:
            raise HTTPException(
                status_code=400,
                detail=f"文件读取失败：{fp}，请重新上传",
            )
        file_datas.append((fp, file_data))

    # fitz 逐页插入 + 重写 PDF 是同步 CPU 重活，线程池执行避免阻塞事件循环
    try:
        merged_bytes = await asyncio.to_thread(_merge_pdf_bytes, file_datas)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 合并后的文件名：基于第一个文件名，确保以 .pdf 结尾
    base_name = file_paths[0].rsplit("/", 1)[-1] if "/" in file_paths[0] else file_paths[0]
    merged_name = f"merged_{base_name}"
    if not merged_name.lower().endswith(".pdf"):
        merged_name = merged_name.rsplit(".", 1)[0] + ".pdf"

    file_url = await storage.save_original(merged_bytes, merged_name, user_id)
    logger.info(f"Merged {len(file_paths)} files into {file_url}")
    return file_url


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_assignment(
    file: UploadFile | None = File(None),
    file_path: str | None = Form(None),
    file_paths: str | None = Form(
        None,
        description="多文件合并上传，JSON 数组字符串如 '[\"path/1.png\",\"path/2.png\"]'，顺序=合并后页面顺序",
    ),
    name: str = Form(..., max_length=255),
    grade: str = Form(..., max_length=32),
    subject: str = Form(..., max_length=64),
    semester: str = Form(..., max_length=32),
    usage_month: str = Form(..., max_length=16),
    layout_type: LayoutType = Form(LayoutType.A4_SINGLE),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传作业文件。
    支持三种模式：
    - file_path：单个预上传文件（旧版兼容）
    - file_paths：多个预上传文件，将按顺序合并为一个 PDF（多页合并）
    - file：直接上传单个文件
    """
    storage = StorageService()

    # 解析多文件路径
    parsed_paths: list[str] = []
    if file_paths:
        try:
            parsed_paths = json.loads(file_paths)
            if not isinstance(parsed_paths, list) or len(parsed_paths) == 0:
                raise HTTPException(status_code=400, detail="file_paths 需要是非空 JSON 数组")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="file_paths 格式错误，需要 JSON 数组字符串")

    if len(parsed_paths) > 1:
        # 多文件合并模式：按前端排列的顺序合并为单个 PDF
        file_url = await _merge_files_to_pdf(parsed_paths, storage, current_user.id)
    elif len(parsed_paths) == 1:
        # 单文件路径：统一转为 PDF 存储（图片 → 单页 PDF）
        file_url = await _ensure_pdf(parsed_paths[0], storage, current_user.id)
    elif file_path:
        # 旧版单文件预上传模式：统一转为 PDF 存储
        file_url = await _ensure_pdf(file_path, storage, current_user.id)
    elif file:
        # 直接上传单文件模式：图片先转为单页 PDF 再存储
        file_data = await _validate_and_read_file(file)
        filename = file.filename or "upload"
        if not filename.lower().endswith(".pdf"):
            file_data = _image_bytes_to_pdf(file_data)
            filename = (filename.rsplit(".", 1)[0] if "." in filename else filename) + ".pdf"
        file_url = await storage.save_original(file_data, filename, current_user.id)
    else:
        raise HTTPException(status_code=400, detail="No file or file_path provided")

    # Create assignment record
    assignment = Assignment(
        name=name,
        grade=grade,
        subject=subject,
        semester=semester,
        usage_month=usage_month,
        layout_type=layout_type,
        file_url=file_url,
        status=AssignmentStatus.PENDING,
        creator_id=current_user.id,
    )
    db.add(assignment)
    await db.flush()
    await db.refresh(assignment)

    return {
        "assignment_id": assignment.id,
        "status": assignment.status.value,
        "message": "Assignment uploaded. Go to detail page to start analysis.",
    }


STORAGE_TIMEOUT = 15  # seconds for MinIO operations


@router.post("/pre-upload", status_code=status.HTTP_200_OK)
async def pre_upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """预上传文件，返回文件路径供后续创建作业时引用"""
    file_data = await _validate_and_read_file(file)

    try:
        storage = StorageService()
        file_path = await asyncio.wait_for(
            storage.save_original(file_data, file.filename, current_user.id),
            timeout=STORAGE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("MinIO storage timed out after %ds", STORAGE_TIMEOUT)
        raise HTTPException(status_code=500, detail="文件存储超时，请确保 MinIO 服务已启动")
    except Exception as e:
        logger.error("File upload to MinIO failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="文件存储失败，请确保 MinIO 服务已启动")

    return {
        "file_path": file_path,
        "filename": file.filename,
        "size": len(file_data),
    }


@router.get("", response_model=PaginatedResponse)
async def list_assignments(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=500),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Total count
    count_query = select(func.count()).select_from(Assignment).where(Assignment.creator_id == current_user.id)
    if grade:
        count_query = count_query.where(Assignment.grade == grade)
    if subject:
        count_query = count_query.where(Assignment.subject == subject)
    if semester:
        count_query = count_query.where(Assignment.semester == semester)
    total = (await db.execute(count_query)).scalar() or 0

    # Paginated results with aggregated counts (single query to avoid N+1)
    q_stats = (
        select(
            Question.assignment_id,
            func.count(Question.id).label("question_count"),
            func.sum(case((Question.score < Question.full_score, 1), else_=0)).label("error_count"),
            func.coalesce(func.sum(Question.score), 0).label("total_score"),
            func.coalesce(func.sum(Question.full_score), 0).label("full_total"),
        )
        .group_by(Question.assignment_id)
        .subquery()
    )

    query = (
        select(Assignment, func.coalesce(q_stats.c.question_count, 0), func.coalesce(q_stats.c.error_count, 0), func.coalesce(q_stats.c.total_score, 0), func.coalesce(q_stats.c.full_total, 0))
        .outerjoin(q_stats, Assignment.id == q_stats.c.assignment_id)
        .where(Assignment.creator_id == current_user.id)
    )

    if grade:
        query = query.where(Assignment.grade == grade)
    if subject:
        query = query.where(Assignment.subject == subject)
    if semester:
        query = query.where(Assignment.semester == semester)

    query = query.order_by(desc(Assignment.created_at)).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for a, question_count, error_count, total_score, full_total in rows:
        items.append(
            {
                "id": a.id,
                "name": a.name,
                "grade": a.grade,
                "subject": a.subject,
                "semester": a.semester,
                "usage_month": a.usage_month,
                "layout_type": a.layout_type,
                "status": a.status,
                "total_score": float(total_score),
                "full_total": float(full_total),
                "question_count": question_count,
                "error_count": error_count,
                "created_at": a.created_at,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{assignment_id}")
async def get_assignment(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Get questions — 按题号、子题序号排序
    q_result = await db.execute(
        select(Question)
        .where(Question.assignment_id == assignment_id)
        .order_by(Question.question_number, Question.sub_question_index)
    )
    all_questions = q_result.scalars().all()

    # ── 僵尸状态自愈 ──
    # dev 模式下分析任务与 API 同进程运行，服务重启会丢失后台任务，
    # 导致作业永远停留在"正在分析"。若检测到状态为分析中但当前进程
    # 并没有该作业的任务在跑，则自动收敛（规则见 reconcile_stuck_assignment：
    # 全失败 → 作业标记失败；残留非终态题目先标记失败再收敛，避免卡死无法重分析）。
    settings = get_settings()
    _ANALYZING_STATES = (
        AssignmentStatus.SPLITTING,
        AssignmentStatus.GRADING,
        AssignmentStatus.PROCESSING,
    )
    # 僵尸状态自愈的触发条件（A3-5 扩展）：
    # - 作业级状态仍卡在分析中（整卷分析被打断）
    # - 或作业已到终态但存在非终态题目（单题重分析被打断，题目卡在
    #   PROCESSING/PENDING，无法再次重分析）
    # 单题重分析进行中会登记进 is_analysis_running 的内存注册表，
    # 自愈逻辑据此跳过，不会误杀在飞任务。
    # 题目残留触发自愈仅适用于"已进入过分析生命周期"的作业：
    # 单题重分析被打断时作业已是终态（COMPLETED/FAILED），题目卡在
    # PENDING/PROCESSING；而作业仍停在 PENDING/SPLITTING/SPLITTED 时，
    # 题目 PENDING 是合法的（用户还没点"开始分析"），不能当作崩溃残留
    # 误标失败。此防护与启动扫描 reconcile_all_stuck_assignments 第二类一致。
    has_stale_question = (
        assignment.status in (AssignmentStatus.COMPLETED, AssignmentStatus.FAILED)
        and any(
            q.status in (QuestionStatus.PENDING, QuestionStatus.PROCESSING)
            for q in all_questions
        )
    )
    if (
        settings.DEV_MODE
        and all_questions
        and (assignment.status in _ANALYZING_STATES or has_stale_question)
    ):
        from app.tasks.analysis_tasks import (
            is_analysis_running,
            reconcile_stuck_assignment,
            recalc_assignment_total,
        )
        # 节流：60 秒内不重复自愈同一作业（前端轮询详情页时避免反复写库）
        now = time.time()
        last_heal = _last_heal_timestamps.get(assignment_id, 0)
        if not is_analysis_running(assignment_id) and (now - last_heal) > _HEAL_THROTTLE_SECONDS:
            healed = await reconcile_stuck_assignment(db, assignment, all_questions)
            if healed:
                _last_heal_timestamps[assignment_id] = now
                # 重算总分（内部会自行 commit）并刷新内存中的 assignment
                await recalc_assignment_total(assignment_id, db)
                await db.refresh(assignment)

    # Generate presigned URLs (graceful fallback on storage errors)
    storage = StorageService()

    try:
        file_url = await storage.get_presigned_url(assignment.file_url)
    except Exception:
        logger.warning("Failed to get presigned URL for assignment %d file_url", assignment_id)
        file_url = ""

    # 辅助函数：将 Question ORM 对象转为 dict
    # 批量预生成所有题目的预签名 URL（单次批量调用替代 N 次单独调用）
    def _question_to_dict(q: Question, presigned_cache: dict[str, str]) -> dict:
        # 从缓存获取预签名 URL（已在外部批量生成）
        image_url = presigned_cache.get(q.image_url, "")
        answer_image_url = presigned_cache.get(q.answer_image_url, "") if q.answer_image_url else None
        return {
            "id": q.id,
            "assignment_id": q.assignment_id,
            "question_number": q.question_number,
            "image_url": image_url,
            "student_answer": q.student_answer,
            "correct_answer": q.correct_answer,
            "score": q.score,
            "full_score": q.full_score,
            "analysis_detail": q.analysis_detail,
            "question_type": q.question_type,
            "knowledge_points": q.knowledge_points,
            "common_mistakes": q.common_mistakes,
            "confidence_score": q.confidence_score,
            "status": q.status,
            "page_index": q.page_index,
            "bbox_x": q.bbox_x,
            "bbox_y": q.bbox_y,
            "bbox_w": q.bbox_w,
            "bbox_h": q.bbox_h,
            "created_at": q.created_at,
            "parent_id": q.parent_id,
            "sub_question_index": q.sub_question_index,
            "answer_image_url": answer_image_url,
            "manual_review_note": q.manual_review_note,
            "children": [],  # 占位，后续填充
        }

    # 批量预生成所有题目的预签名 URL（避免 N+1 查询）
    # 收集所有需要生成 URL 的路径（去重），然后批量生成
    all_image_urls: set[str] = set()
    for q in all_questions:
        if q.image_url:
            all_image_urls.add(q.image_url)
        if q.answer_image_url:
            all_image_urls.add(q.answer_image_url)
    # 批量生成预签名 URL（storage 内部可并发，但对外是单次调用入口）
    presigned_cache: dict[str, str] = {}
    if all_image_urls:
        try:
            # 批量生成：用 asyncio.gather 并发请求所有 URL
            import asyncio as _asyncio
            url_list = list(all_image_urls)
            results = await _asyncio.gather(
                *[storage.get_presigned_url(url) for url in url_list],
                return_exceptions=True,
            )
            for url, result in zip(url_list, results):
                if isinstance(result, Exception):
                    presigned_cache[url] = ""
                else:
                    presigned_cache[url] = result
        except Exception:
            logger.warning("批量生成预签名 URL 失败")

    # 构建嵌套结构：父题 + 子题（支持多级嵌套）
    children_by_parent: dict[int, list[dict]] = {}
    top_level: list[dict] = []
    all_dicts: list[dict] = []
    for q in all_questions:
        qd = _question_to_dict(q, presigned_cache)
        all_dicts.append(qd)
        if q.parent_id is not None:
            # 子题 → 归入对应父题
            children_by_parent.setdefault(q.parent_id, []).append(qd)
        else:
            # 顶层题（可能是独立题或父题容器）
            top_level.append(qd)

    # 递归挂载子题到父题（支持多级嵌套）
    def _attach_children(qd_list: list[dict]):
        for qd in qd_list:
            qd["children"] = children_by_parent.get(qd["id"], [])
            if qd["children"]:
                _attach_children(qd["children"])

    _attach_children(top_level)

    # 计算总分：只汇总叶子题（子题 + 无子题的独立题）
    parent_ids = set(children_by_parent.keys())
    leaf_questions = [q for q in all_questions if q.id not in parent_ids]

    return {
        "id": assignment.id,
        "name": assignment.name,
        "grade": assignment.grade,
        "subject": assignment.subject,
        "semester": assignment.semester,
        "usage_month": assignment.usage_month,
        "layout_type": assignment.layout_type,
        "file_url": file_url,
        "status": assignment.status,
        "total_score": sum((q.score or 0) for q in leaf_questions),
        "full_total": sum((q.full_score or 0) for q in leaf_questions),
        "ai_summary": assignment.ai_summary,
        "questions": top_level,  # 只返回顶层题目，子题嵌套在 children 中
        "created_at": assignment.created_at,
    }


class UpdateAssignmentRequest(BaseModel):
    name: str | None = None
    grade: str | None = None
    subject: str | None = None
    semester: str | None = None
    usage_month: str | None = None


@router.put("/{assignment_id}")
async def update_assignment(
    assignment_id: int,
    data: UpdateAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    if data.name is not None:
        assignment.name = data.name
    if data.grade is not None:
        assignment.grade = data.grade
    if data.subject is not None:
        assignment.subject = data.subject
    if data.semester is not None:
        assignment.semester = data.semester
    if data.usage_month is not None:
        assignment.usage_month = data.usage_month

    await db.commit()
    await db.refresh(assignment)
    return {"message": "作业信息已更新", "assignment_id": assignment_id}


@router.delete("/{assignment_id}")
async def delete_assignment(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Collect file paths before DB deletion
    from app.services.file_upload import StorageService
    storage = StorageService()
    file_paths_to_delete = [assignment.file_url]

    q_result = await db.execute(select(Question).where(Question.assignment_id == assignment_id))
    questions = q_result.scalars().all()
    for q in questions:
        file_paths_to_delete.append(q.image_url)

    # 删除子题 → 父题 → 作业（必须按此顺序，否则 FK 约束报错）
    for q in questions:
        if q.parent_id is not None:
            await db.delete(q)
    for q in questions:
        if q.parent_id is None:
            await db.delete(q)
    await db.flush()
    await db.delete(assignment)
    await db.commit()

    # Then clean up files (best-effort, errors logged but not re-raised)
    for path in file_paths_to_delete:
        try:
            await storage.delete_object(path)
        except Exception:
            logger.warning("Failed to delete file after DB cleanup: %s", path)

    return {"message": "Assignment and associated data deleted."}


@router.post("/{assignment_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动触发作业题目切割与AI分析"""
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    ACTIVE_STATES = (
        AssignmentStatus.SPLITTING,
        AssignmentStatus.GRADING,
        AssignmentStatus.PROCESSING,  # backward compat
    )
    if assignment.status in ACTIVE_STATES:
        raise HTTPException(status_code=400, detail="分析正在进行中，请等待完成")

    # Allow analysis from splitted (normal), completed/failed (re-analysis)
    ALLOW_STATES = (AssignmentStatus.SPLITTED, AssignmentStatus.COMPLETED, AssignmentStatus.FAILED)
    if assignment.status not in ALLOW_STATES:
        raise HTTPException(status_code=400, detail="请先手动切割题目后再开始分析")

    # Mark as splitting — the background task will advance through splitting→splitted→grading→completed
    assignment.status = AssignmentStatus.SPLITTING
    await db.commit()

    # Trigger analysis (dev mode: background async, production: Celery)
    settings = get_settings()
    if settings.DEV_MODE:
        from app.tasks.dev_runner import analyze_assignment_dev
        analyze_assignment_dev(assignment_id)
    else:
        from app.tasks.analysis_tasks import analyze_assignment
        analyze_assignment.delay(assignment_id)

    return {
        "assignment_id": assignment_id,
        "status": assignment.status.value,
        "message": "Analysis started.",
    }


@router.post("/{assignment_id}/cancel")
async def cancel_analysis(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """终止正在进行的分析任务"""
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    ACTIVE_STATES = (
        AssignmentStatus.SPLITTING,
        AssignmentStatus.SPLITTED,
        AssignmentStatus.GRADING,
        AssignmentStatus.PROCESSING,
    )
    if assignment.status not in ACTIVE_STATES and assignment.status not in (AssignmentStatus.PENDING,):
        raise HTTPException(status_code=400, detail="No analysis in progress to cancel")

    assignment.status = AssignmentStatus.FAILED
    assignment.ai_summary = "用户手动终止"
    # 同步清理"正在分析"的题目，避免终止后前端残留转圈状态
    q_result = await db.execute(
        select(Question).where(
            Question.assignment_id == assignment_id,
            Question.status == QuestionStatus.PROCESSING,
        )
    )
    for q in q_result.scalars().all():
        q.status = QuestionStatus.FAILED
        q.analysis_detail = "分析已终止，请重新分析该题"
    await db.commit()

    return {"assignment_id": assignment_id, "status": "failed", "message": "Analysis cancelled."}


@router.post("/{assignment_id}/re-summarize", status_code=status.HTTP_200_OK)
async def re_summarize(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    重新汇总整卷分数和AI评语。

    不重新逐题评分，仅基于现有题目分数重新计算总分，并重新生成整体分析评语。
    用于用户在确认/修正各题分数后刷新整卷分析结果。
    """
    # 1. 校验所有权
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    if assignment.status != AssignmentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="仅已完成分析的作业可以重新汇总")

    # 2. 重新计算总分
    from app.tasks.analysis_tasks import recalc_assignment_total, refresh_assignment_summary
    await recalc_assignment_total(assignment_id, db, user_id=current_user.id)

    # 3. 刷新 assignment 数据（recalc 内部已 commit，需要重新加载）
    await db.refresh(assignment)

    # 4. 基于最新题目数据重新生成AI评语（内置 LLM 失败回退）
    await refresh_assignment_summary(assignment_id, db)

    await db.refresh(assignment)

    return {
        "assignment_id": assignment_id,
        "total_score": assignment.total_score,
        "message": "整卷分析已更新",
    }


@router.get("/{assignment_id}/source-pages")
async def get_source_pages(
    assignment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回原始文件的所有页面图片，供手动切割时渲染画布"""
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    storage = StorageService()

    # 下载原始文件
    try:
        file_bytes = await storage.get_file_bytes(assignment.file_url)
    except Exception as e:
        logger.error("Failed to download source file: %s", e)
        raise HTTPException(status_code=500, detail="无法下载源文件")

    if not file_bytes:
        raise HTTPException(status_code=404, detail="源文件不存在")

    # 渲染页面图片（fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环）
    import numpy as np
    import cv2
    if file_bytes.startswith(b"%PDF"):
        # PDF → 逐页渲染（to_thread 中独立打开/关闭 Document，线程安全）
        rendered_pages = await asyncio.to_thread(_render_pdf_pages_bgr, file_bytes)
        pages = []
        for page_idx, img in rendered_pages.items():
            _, img_bytes = cv2.imencode(".png", img)
            # 保存临时页面图片
            page_path = await storage.save_question_image(
                img_bytes.tobytes(), current_user.id, assignment_id, suffix=f"_page_{page_idx}"
            )
            try:
                page_url = await storage.get_presigned_url(page_path)
            except Exception:
                logger.warning("Failed to get presigned URL for page %d", page_idx)
                page_url = ""
            h, w = img.shape[:2]
            pages.append({
                "page_index": page_idx,
                "image_url": page_url,
                "width": w,
                "height": h,
            })
    else:
        # 单张图片（解码毫秒级，保持原位执行）
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="无法解码图片文件")
        _, img_bytes = cv2.imencode(".png", img)
        page_path = await storage.save_question_image(
            img_bytes.tobytes(), current_user.id, assignment_id, suffix="_page_0"
        )
        try:
            page_url = await storage.get_presigned_url(page_path)
        except Exception:
            logger.warning("Failed to get presigned URL for page 0")
            page_url = ""
        h, w = img.shape[:2]
        pages = [{
            "page_index": 0,
            "image_url": page_url,
            "width": w,
            "height": h,
        }]

    return {
        "pages": pages,
        "total_pages": len(pages),
    }


@router.post("/{assignment_id}/manual-split", status_code=status.HTTP_200_OK)
async def manual_split(
    assignment_id: int,
    data: ManualSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动切割：根据用户定义的区域替换所有题目"""
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if not data.regions:
        raise HTTPException(status_code=400, detail="至少需要一个题目区域")

    # 拆分普通题目区域与客观题识别区区域
    question_regions = [r for r in data.regions if r.region_type != "answer_sheet"]
    sheet_regions = [r for r in data.regions if r.region_type == "answer_sheet"]
    if not question_regions:
        raise HTTPException(status_code=400, detail="至少需要一个题目区域（识别区不能单独切割）")

    storage = StorageService()

    # 下载原始文件
    try:
        file_bytes = await storage.get_file_bytes(assignment.file_url)
    except Exception as e:
        logger.error("Failed to download source file: %s", e)
        raise HTTPException(status_code=500, detail="无法下载源文件")

    import numpy as np, cv2

    # 渲染所有页面（fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环）
    page_images: dict[int, np.ndarray] = {}
    if file_bytes.startswith(b"%PDF"):
        rendered_pages = await asyncio.to_thread(_render_pdf_pages_bgr, file_bytes)
        page_images = dict(rendered_pages)
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="无法解码图片文件")
        page_images[0] = img

    # 删除现有题目（先删子题再删父题，避免 FK 约束报错）
    q_result = await db.execute(
        select(Question).where(Question.assignment_id == assignment_id)
    )
    old_questions = q_result.scalars().all()
    # 先删子题
    for q in old_questions:
        if q.parent_id is not None:
            try:
                await storage.delete_object(q.image_url)
            except Exception:
                pass
            await db.delete(q)
    # 再删父题/独立题
    for q in old_questions:
        if q.parent_id is None:
            try:
                await storage.delete_object(q.image_url)
            except Exception:
                pass
            await db.delete(q)
    await db.flush()

    # 删除旧的客观题识别区切图（重新切割时替换，未标记则清空）
    if assignment.answer_sheet_image_url:
        try:
            await storage.delete_object(assignment.answer_sheet_image_url)
        except Exception:
            pass
        assignment.answer_sheet_image_url = None

    # 按题号分组（前端已按 question_number→draw_order 排序，这里用 dict 保持首次出现顺序）
    groups: dict[int, list[ManualSplitRegion]] = {}
    group_order: list[int] = []
    for region in question_regions:
        if region.question_number not in groups:
            groups[region.question_number] = []
            group_order.append(region.question_number)
        groups[region.question_number].append(region)

    for qn in group_order:
        group = groups[qn]

        # 切出每个区域的图像（支持旋转后裁切）
        cut_images: list[np.ndarray] = []
        for region in group:
            page_img = page_images.get(region.page_index)
            if page_img is None:
                logger.warning("Page %d not found for question %d, skipping", region.page_index, qn)
                continue

            # 使用 _rotate_and_cut 处理旋转后裁切
            cut = _rotate_and_cut(page_img, region.rotation, region.x, region.y, region.w, region.h)
            if cut is None:
                logger.warning("Invalid region for question %d: x=%.0f y=%.0f w=%.0f h=%.0f rot=%d",
                               qn, region.x, region.y, region.w, region.h, region.rotation)
                continue

            cut_images.append(cut)

        if not cut_images:
            logger.warning("No valid images for question %d, skipping", qn)
            continue

        # 单区域直接使用，多区域合并
        if len(cut_images) == 1:
            merged = cut_images[0]
            # 用第一个（唯一）region 的坐标作为 bbox
            first = group[0]
            bbox_x, bbox_y, bbox_w, bbox_h = float(first.x), float(first.y), float(first.w), float(first.h)
            page_index = first.page_index
        else:
            # 统一垂直拼接
            merged = _merge_images(cut_images)
            first = group[0]
            bbox_x, bbox_y = float(first.x), float(first.y)
            bbox_w, bbox_h = float(first.w), float(merged.shape[0] - bbox_y)
            page_index = first.page_index

        _, img_bytes = cv2.imencode(".png", merged)

        image_url = await storage.save_question_image(
            img_bytes.tobytes(), current_user.id, assignment_id
        )
        question = Question(
            assignment_id=assignment_id,
            question_number=qn,
            image_url=image_url,
            status=QuestionStatus.PENDING,
            page_index=page_index,
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
        )
        db.add(question)

    # 切割客观题识别区（多个区域垂直合并），保存到作业级字段
    if sheet_regions:
        sheet_cuts: list[np.ndarray] = []
        for region in sheet_regions:
            page_img = page_images.get(region.page_index)
            if page_img is None:
                logger.warning("Page %d not found for answer sheet region, skipping", region.page_index)
                continue
            cut = _rotate_and_cut(page_img, region.rotation, region.x, region.y, region.w, region.h)
            if cut is None:
                logger.warning("Invalid answer sheet region: x=%.0f y=%.0f w=%.0f h=%.0f rot=%d",
                               region.x, region.y, region.w, region.h, region.rotation)
                continue
            sheet_cuts.append(cut)
        if sheet_cuts:
            sheet_img = sheet_cuts[0] if len(sheet_cuts) == 1 else _merge_images(sheet_cuts)
            _, sheet_bytes = cv2.imencode(".png", sheet_img)
            assignment.answer_sheet_image_url = await storage.save_question_image(
                sheet_bytes.tobytes(), current_user.id, assignment_id, suffix="_answer_sheet"
            )
            logger.info("[manual-split] 作业 %d 已保存客观题识别区切图（%d 个区域）",
                        assignment_id, len(sheet_cuts))

    # 更新状态为已切割
    assignment.status = AssignmentStatus.SPLITTED
    await db.commit()

    return {
        "assignment_id": assignment_id,
        "status": assignment.status.value,
        "question_count": len(group_order),
        "has_answer_sheet": assignment.answer_sheet_image_url is not None,
        "message": "Manual split completed.",
    }


@router.post("/{assignment_id}/answer-pages", status_code=status.HTTP_200_OK)
async def upload_answer_pages(
    assignment_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传标准答案（答案解析）文件，渲染所有页面为图片供前端 canvas 展示切割区域。"""
    # 1. 校验所有权
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    # 2. 校验文件
    file_bytes = await _validate_and_read_file(file)

    # 3. 将答案文件保存到作业记录（使用 StorageService 统一存储）
    import uuid as _uuid
    storage = StorageService()

    # 安全提取扩展名（使用已校验的文件名，限制为白名单内类型）
    _ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "pdf"}
    ext = "png"  # 默认扩展名
    if file.filename and "." in file.filename:
        candidate = file.filename.rsplit(".", 1)[-1].lower()
        if candidate in _ALLOWED_EXT:
            ext = candidate
    answer_file_url = f"answers/{current_user.id}/{assignment_id}/answer_{_uuid.uuid4().hex}.{ext}"
    await storage.save_file(answer_file_url, file_bytes)

    # 4. 渲染所有页面为PNG（fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环）
    pages: list[dict] = []
    import numpy as np
    import cv2

    if file_bytes.startswith(b"%PDF"):
        rendered_pages = await asyncio.to_thread(_render_pdf_pages_bgr, file_bytes)
        for i, img in rendered_pages.items():
            h, w = img.shape[:2]
            # 保存页面图片
            _, page_bytes = cv2.imencode(".png", img)
            page_url = await storage.save_question_image(
                page_bytes.tobytes(), current_user.id, assignment_id,
                suffix=f"_answer_page_{i}",
            )
            presigned = await storage.get_presigned_url(page_url)
            pages.append({
                "page_index": i,
                "image_url": presigned,
                "width": w,
                "height": h,
            })
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        page_img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if page_img is None:
            raise HTTPException(status_code=400, detail="无法解析答案图片")
        h, w = page_img.shape[:2]
        _, page_bytes = cv2.imencode(".png", page_img)
        page_url = await storage.save_question_image(
            page_bytes.tobytes(), current_user.id, assignment_id,
            suffix="_answer_page_0",
        )
        presigned = await storage.get_presigned_url(page_url)
        pages.append({
            "page_index": 0,
            "image_url": presigned,
            "width": w,
            "height": h,
        })

    # 5. 将 answer_file_url 临时存到 assignment 的 file_url 旁边？
    # 我们直接在这里保存到 assignment 对象的扩展字段... Assignment 模型没有 answer_file_url 字段。
    # 我们把答案文件路径保存到 answer_file_url 在 Question 上? 不，先保存在返回结果中供后续 answer-split 使用。
    return {
        "pages": pages,
        "total_pages": len(pages),
        "answer_file_url": answer_file_url,
    }


@router.post("/{assignment_id}/answer-split", status_code=status.HTTP_200_OK)
async def answer_split(
    assignment_id: int,
    data: AnswerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    按题目切割标准答案图片区域，保存到对应 Question 的 answer_image_url。

    与 manual-split 不同，这里不创建新题目，而是匹配已有题目并更新其 answer_image_url。
    """
    # 1. 校验所有权，加载已有题目
    result = await db.execute(
        select(Assignment).where(
            Assignment.id == assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    q_result = await db.execute(
        select(Question)
        .where(Question.assignment_id == assignment_id)
        .order_by(Question.question_number)
    )
    existing_questions = q_result.scalars().all()
    if not existing_questions:
        raise HTTPException(status_code=400, detail="请先手动切割题目，再进行答案切割")
    # 建立题号到 Question 的映射
    question_by_number: dict[int, Question] = {}
    for q in existing_questions:
        # 仅匹配父题或独立题（parent_id IS NULL）
        if q.parent_id is None:
            question_by_number[q.question_number] = q

    # 2. 校验答案文件归属（防止跨用户读取任意存储文件）
    _assert_owned_storage_path(data.answer_file_url, current_user.id, allowed_prefixes=("answers",))

    # 3. 下载答案文件并渲染页面
    settings = get_settings()
    storage = StorageService()

    try:
        answer_bytes = await storage.get_file_bytes(data.answer_file_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法下载答案文件: {str(e)}")

    import numpy as np
    import cv2

    # 渲染所有页面为 numpy 数组（fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环）
    page_images: dict[int, np.ndarray] = {}
    all_pages_data: dict[int, tuple[int, int]] = {}  # page_index -> (width, height)

    if answer_bytes.startswith(b"%PDF"):
        rendered_pages = await asyncio.to_thread(_render_pdf_pages_bgr, answer_bytes)
        page_images = dict(rendered_pages)
        all_pages_data = {
            i: (img.shape[1], img.shape[0]) for i, img in rendered_pages.items()
        }
    else:
        img_array = np.frombuffer(answer_bytes, dtype=np.uint8)
        page_img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if page_img is None:
            raise HTTPException(status_code=400, detail="无法解析答案图片")
        page_images[0] = page_img
        all_pages_data[0] = (page_img.shape[1], page_img.shape[0])

    # 4. 按 question_number 分组
    groups: dict[int, list[AnswerSplitRegion]] = {}
    group_order: list[int] = []
    for region in data.regions:
        if region.question_number not in groups:
            groups[region.question_number] = []
            group_order.append(region.question_number)
        groups[region.question_number].append(region)

    updated_count = 0

    # 5. 逐题切割答案图片
    for qn in group_order:
        group = groups[qn]
        # 按 draw_order 排序（AnswerSplitRegion 也可以加 draw_order...我们暂时用列表顺序）
        # 找到对应题目
        question = question_by_number.get(qn)
        if not question:
            logger.warning("答案切割: 题号 %d 不存在，跳过", qn)
            continue

        # 删除旧答案图片
        if question.answer_image_url:
            try:
                await storage.delete_object(question.answer_image_url)
            except Exception:
                pass

        # 裁切每个区域（支持旋转）
        cut_images: list[np.ndarray] = []
        for region in group:
            page_img = page_images.get(region.page_index)
            if page_img is None:
                logger.warning("答案切割: 页码 %d 不存在，题号 %d 跳过", region.page_index, qn)
                continue
            cut = _rotate_and_cut(page_img, region.rotation, region.x, region.y, region.w, region.h)
            if cut is not None:
                cut_images.append(cut)

        if not cut_images:
            logger.warning("答案切割: 题号 %d 无有效区域", qn)
            continue

        # 多区域垂直拼接
        if len(cut_images) == 1:
            merged = cut_images[0]
        else:
            merged = _merge_images(cut_images)

        # 保存答案图片
        _, answer_img_bytes = cv2.imencode(".png", merged)
        answer_image_url = await storage.save_question_image(
            answer_img_bytes.tobytes(), current_user.id, assignment_id,
            suffix=f"_answer_{qn}",
        )
        question.answer_image_url = answer_image_url
        updated_count += 1
        logger.info("答案切割: 题号 %d 答案图片已保存", qn)

    await db.commit()

    return {
        "assignment_id": assignment_id,
        "updated_count": updated_count,
        "message": f"答案切割完成，已更新 {updated_count} 道题目的答案图片",
    }



