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


class ManualSplitRequest(BaseModel):
    regions: list[ManualSplitRegion]

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# Image magic bytes
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
WEBP_MAGIC = b"RIFF"
PDF_MAGIC = b"%PDF"


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
        # Validate and upload file inline
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        ext = "." + file.filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        file_data = await file.read()
        if len(file_data) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")

        # Validate magic bytes
        if ext == ".pdf" and not file_data.startswith(PDF_MAGIC):
            raise HTTPException(status_code=400, detail="Invalid PDF file")
        elif ext in {".png"} and not file_data.startswith(PNG_MAGIC):
            raise HTTPException(status_code=400, detail="Invalid PNG file")
        elif ext in {".jpg", ".jpeg"} and not file_data.startswith(JPEG_MAGIC):
            raise HTTPException(status_code=400, detail="Invalid JPEG file")
        elif ext == ".webp" and not file_data[:4].startswith(WEBP_MAGIC):
            raise HTTPException(status_code=400, detail="Invalid WebP file")

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
    if ext == ".pdf" and not file_data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=400, detail="无效的 PDF 文件")
    elif ext == ".png" and not file_data.startswith(PNG_MAGIC):
        raise HTTPException(status_code=400, detail="无效的 PNG 文件")
    elif ext in {".jpg", ".jpeg"} and not file_data.startswith(JPEG_MAGIC):
        raise HTTPException(status_code=400, detail="无效的 JPEG 文件")
    elif ext == ".webp" and not file_data[:4].startswith(WEBP_MAGIC):
        raise HTTPException(status_code=400, detail="无效的 WebP 文件")

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

    # Get questions
    q_result = await db.execute(
        select(Question)
        .where(Question.assignment_id == assignment_id)
        .order_by(Question.question_number)
    )
    questions = q_result.scalars().all()

    # Generate presigned URLs
    storage = StorageService()

    return {
        "id": assignment.id,
        "name": assignment.name,
        "grade": assignment.grade,
        "subject": assignment.subject,
        "semester": assignment.semester,
        "month": assignment.month,
        "layout_type": assignment.layout_type,
        "file_url": await storage.get_presigned_url(assignment.file_url),
        "status": assignment.status,
        "total_score": sum((q.score or 0) for q in questions),
        "full_total": sum((q.full_score or 0) for q in questions),
        "ai_summary": assignment.ai_summary,
        "questions": [
            {
                "id": q.id,
                "assignment_id": q.assignment_id,
                "question_number": q.question_number,
                "image_url": await storage.get_presigned_url(q.image_url),
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
            }
            for q in questions
        ],
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

    # Delete files from MinIO
    storage = StorageService()
    await storage.delete_object(assignment.file_url)

    q_result = await db.execute(select(Question).where(Question.assignment_id == assignment_id))
    questions = q_result.scalars().all()
    for q in questions:
        await storage.delete_object(q.image_url)

    await db.delete(assignment)
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
            pages.append({
                "page_index": page_idx,
                "image_url": await storage.get_presigned_url(page_path),
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
        h, w = img.shape[:2]
        pages = [{
            "page_index": 0,
            "image_url": await storage.get_presigned_url(page_path),
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

    # 删除现有题目
    q_result = await db.execute(
        select(Question).where(Question.assignment_id == assignment_id)
    )
    old_questions = q_result.scalars().all()
    for q in old_questions:
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

        # 切出每个区域的图像
        cut_images: list[np.ndarray] = []
        for region in group:
            page_img = page_images.get(region.page_index)
            if page_img is None:
                logger.warning("Page %d not found for question %d, skipping", region.page_index, qn)
                continue

            ph, pw = page_img.shape[:2]
            x = max(0, int(region.x))
            y = max(0, int(region.y))
            w = min(pw - x, int(region.w))
            h = min(ph - y, int(region.h))

            if w <= 0 or h <= 0:
                logger.warning("Invalid region for question %d: x=%d y=%d w=%d h=%d", qn, x, y, w, h)
                continue

            cut_images.append(page_img[y:y + h, x:x + w])

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
