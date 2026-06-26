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
import asyncio
import logging

logger = logging.getLogger(__name__)

from pydantic import BaseModel

router = APIRouter(prefix="/assignments", tags=["assignments"])


class ManualSplitRegion(BaseModel):
    question_number: int
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    draw_order: int = 0  # 绘制顺序，同题多区域时决定合并后的排列先后
    rotation: int = 0     # 图片旋转角度：0/90/180/270，应用到该页面后再裁切


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


def _rotate_and_cut(page_img: "np.ndarray", rotation: int,
                    x: float, y: float, w: float, h: float) -> "np.ndarray | None":
    """
    旋转图片后按坐标裁切区域。

    Args:
        page_img: 原始页面图片 (OpenCV BGR numpy array)
        rotation: 旋转角度（0/90/180/270）
        x, y: 裁切区域左上角坐标（基于旋转后的图片）
        w, h: 裁切区域宽高

    Returns:
        裁切后的图片 numpy array，无效区域返回 None
    """
    import cv2
    import numpy as np

    img = page_img
    if rotation:
        if rotation == 90:
            img = cv2.rotate(page_img, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            img = cv2.rotate(page_img, cv2.ROTATE_180)
        elif rotation == 270:
            img = cv2.rotate(page_img, cv2.ROTATE_90_COUNTERCLOCKWISE)

    ph, pw = img.shape[:2]
    cx = max(0, int(x))
    cy = max(0, int(y))
    cw = min(pw - cx, int(w))
    ch = min(ph - cy, int(h))

    if cw <= 0 or ch <= 0:
        return None

    return img[cy:cy + ch, cx:cx + cw]


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

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
    """Validate file extension, size and magic bytes. Returns file bytes."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型，仅允许：{', '.join(ALLOWED_EXTENSIONS)}",
        )

    file_data = await file.read()
    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"文件过大（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")

    # Validate magic bytes
    magic = _MAGIC_MAP.get(ext)
    if magic:
        if ext == ".webp":
            if not file_data[:4].startswith(magic):
                raise HTTPException(status_code=400, detail=f"无效的 {ext.upper()} 文件")
        elif not file_data.startswith(magic):
            raise HTTPException(status_code=400, detail=f"无效的 {ext.upper()} 文件")

    return file_data


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_assignment(
    file: UploadFile | None = File(None),
    file_path: str | None = Form(None),
    name: str = Form(..., max_length=255),
    grade: str = Form(..., max_length=32),
    subject: str = Form(..., max_length=64),
    semester: str = Form(..., max_length=32),
    month: str = Form(..., max_length=16),
    layout_type: LayoutType = Form(LayoutType.A4_SINGLE),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    storage = StorageService()

    if file_path:
        # Use pre-uploaded file path
        file_url = file_path
    elif file:
        file_data = await _validate_and_read_file(file)
        file_url = await storage.save_original(file_data, file.filename, current_user.id)
    else:
        raise HTTPException(status_code=400, detail="No file or file_path provided")

    # Create assignment record
    assignment = Assignment(
        name=name,
        grade=grade,
        subject=subject,
        semester=semester,
        month=month,
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
    page_size: int = Query(10, ge=1, le=100),
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
        )
        .group_by(Question.assignment_id)
        .subquery()
    )

    query = (
        select(Assignment, func.coalesce(q_stats.c.question_count, 0), func.coalesce(q_stats.c.error_count, 0), func.coalesce(q_stats.c.total_score, 0))
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
    for a, question_count, error_count, total_score in rows:
        items.append(
            {
                "id": a.id,
                "name": a.name,
                "grade": a.grade,
                "subject": a.subject,
                "semester": a.semester,
                "month": a.month,
                "layout_type": a.layout_type,
                "status": a.status,
                "total_score": float(total_score),
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

    # Generate presigned URLs (graceful fallback on storage errors)
    storage = StorageService()

    try:
        file_url = await storage.get_presigned_url(assignment.file_url)
    except Exception:
        logger.warning("Failed to get presigned URL for assignment %d file_url", assignment_id)
        file_url = ""

    # 辅助函数：将 Question ORM 对象转为 dict
    async def _question_to_dict(q: Question) -> dict:
        try:
            image_url = await storage.get_presigned_url(q.image_url)
        except Exception:
            logger.warning("Failed to get presigned URL for question %d image_url", q.id)
            image_url = ""
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
            "answer_image_url": (await storage.get_presigned_url(q.answer_image_url)) if q.answer_image_url else None,
            "manual_review_note": q.manual_review_note,
            "children": [],  # 占位，后续填充
        }

    # 构建嵌套结构：父题 + 子题
    children_by_parent: dict[int, list[dict]] = {}
    top_level: list[dict] = []
    for q in all_questions:
        qd = await _question_to_dict(q)
        if q.parent_id is not None:
            # 子题 → 归入对应父题
            children_by_parent.setdefault(q.parent_id, []).append(qd)
        else:
            # 顶层题（可能是独立题或父题容器）
            top_level.append(qd)

    # 挂载子题到父题
    for pqd in top_level:
        pqd["children"] = children_by_parent.get(pqd["id"], [])

    # 计算总分：只汇总叶子题（子题 + 无子题的独立题）
    parent_ids = set(children_by_parent.keys())
    leaf_questions = [q for q in all_questions if q.id not in parent_ids]

    return {
        "id": assignment.id,
        "name": assignment.name,
        "grade": assignment.grade,
        "subject": assignment.subject,
        "semester": assignment.semester,
        "month": assignment.month,
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
    month: str | None = None


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
    if data.month is not None:
        assignment.month = data.month

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
    await db.commit()

    return {"assignment_id": assignment_id, "status": "failed", "message": "Analysis cancelled."}


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

    # 渲染页面图片
    import numpy as np
    if file_bytes.startswith(b"%PDF"):
        # PDF → 逐页渲染
        import fitz, cv2
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = []
        for page_idx, page in enumerate(doc):
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
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
            pages.append({
                "page_index": page_idx,
                "image_url": page_url,
                "width": pix.width,
                "height": pix.height,
            })
        doc.close()
    else:
        # 单张图片
        import cv2
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

    storage = StorageService()

    # 下载原始文件
    try:
        file_bytes = await storage.get_file_bytes(assignment.file_url)
    except Exception as e:
        logger.error("Failed to download source file: %s", e)
        raise HTTPException(status_code=500, detail="无法下载源文件")

    import numpy as np, cv2

    # 渲染所有页面
    page_images: dict[int, np.ndarray] = {}
    if file_bytes.startswith(b"%PDF"):
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page_idx, page in enumerate(doc):
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            page_images[page_idx] = img
        doc.close()
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

    # 按题号分组（前端已按 question_number→draw_order 排序，这里用 dict 保持首次出现顺序）
    groups: dict[int, list[ManualSplitRegion]] = {}
    group_order: list[int] = []
    for region in data.regions:
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

    # 更新状态为已切割
    assignment.status = AssignmentStatus.SPLITTED
    await db.commit()

    return {
        "assignment_id": assignment_id,
        "status": assignment.status.value,
        "question_count": len(group_order),
        "message": "Manual split completed.",
    }


@router.post("/{assignment_id}/answer-pages", status_code=status.HTTP_200_OK)
async def upload_answer_pages(
    assignment_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传学生答案文件，渲染所有页面为图片供前端 canvas 展示切割区域。"""
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

    # 3. 将答案文件保存到作业记录（后续切割时使用）
    import uuid as _uuid
    settings = get_settings()
    storage = StorageService()

    # 保存原始答案文件
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else "png"
    answer_file_url = f"answers/{current_user.id}/{assignment_id}/answer_{_uuid.uuid4().hex}.{ext}"
    if settings.DEV_MODE:
        from pathlib import Path
        full_path = Path(settings.LOCAL_STORAGE_DIR) / answer_file_url
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(file_bytes)
    else:
        import io
        from minio import Minio
        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        bucket = settings.MINIO_BUCKET
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        client.put_object(
            bucket, answer_file_url,
            io.BytesIO(file_bytes), len(file_bytes),
            content_type="application/octet-stream",
        )

    # 4. 渲染所有页面为PNG
    pages: list[dict] = []
    import numpy as np
    import cv2

    if file_bytes.startswith(b"%PDF"):
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        mat = fitz.Matrix(2.0, 2.0)
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
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
        doc.close()
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
    按题目切割答案图片区域，保存到对应 Question 的 answer_image_url。

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

    # 2. 下载答案文件并渲染页面
    settings = get_settings()
    storage = StorageService()

    try:
        answer_bytes = await storage.get_file_bytes(data.answer_file_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法下载答案文件: {str(e)}")

    import numpy as np
    import cv2

    # 渲染所有页面为 numpy 数组
    page_images: dict[int, np.ndarray] = {}
    all_pages_data: dict[int, tuple[int, int]] = {}  # page_index -> (width, height)

    if answer_bytes.startswith(b"%PDF"):
        import fitz
        doc = fitz.open(stream=answer_bytes, filetype="pdf")
        mat = fitz.Matrix(2.0, 2.0)
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            page_images[i] = img
            all_pages_data[i] = (img.shape[1], img.shape[0])
        doc.close()
    else:
        img_array = np.frombuffer(answer_bytes, dtype=np.uint8)
        page_img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if page_img is None:
            raise HTTPException(status_code=400, detail="无法解析答案图片")
        page_images[0] = page_img
        all_pages_data[0] = (page_img.shape[1], page_img.shape[0])

    # 3. 按 question_number 分组
    groups: dict[int, list[AnswerSplitRegion]] = {}
    group_order: list[int] = []
    for region in data.regions:
        if region.question_number not in groups:
            groups[region.question_number] = []
            group_order.append(region.question_number)
        groups[region.question_number].append(region)

    updated_count = 0

    # 4. 逐题切割答案图片
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


def _merge_images(images: list):
    """将多张图像垂直拼接为一张，宽度统一为最大值。"""
    import numpy as np
    import cv2

    max_w = max(img.shape[1] for img in images)
    resized = []
    for img in images:
        h, w = img.shape[:2]
        if w != max_w:
            new_h = int(h * max_w / w)
            img = cv2.resize(img, (max_w, new_h), interpolation=cv2.INTER_CUBIC)
        resized.append(img)

    SEP = 4
    sep_line = np.full((SEP, max_w, 3), 255, dtype=np.uint8)
    parts = []
    for i, img in enumerate(resized):
        if i > 0:
            parts.append(sep_line)
        parts.append(img)
    return np.vstack(parts)
