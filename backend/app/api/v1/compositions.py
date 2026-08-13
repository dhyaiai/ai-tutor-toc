"""
作文批改 API

提供作文批改的核心操作：
- POST /compositions/correct  — 上传作文文件并批改
- POST /compositions/upload   — 预上传作文文件
- GET  /compositions          — 历史批改列表（支持按年级/科目筛选）
- GET  /compositions/{id}     — 批改详情
- DELETE /compositions/{id}   — 删除记录

文件处理策略：
- PDF / 图片(png/jpg/webp)：转为图片 base64 → 多模态视觉 LLM 识别+批改
- txt：直接读取文本 → 文本 LLM 批改
- docx/doc：python-docx 提取文本 → 文本 LLM 批改
"""

import base64
import os
from io import BytesIO
from typing import List as ListType
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import get_settings
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.composition import CompositionCorrection
from app.schemas.composition import (
    CompositionCorrectRequest,
    CompositionResponse,
    CompositionListItem,
)
from app.services.file_upload import StorageService

router = APIRouter(prefix="/compositions", tags=["compositions"])

# 允许上传的文件类型
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "png", "jpg", "jpeg", "webp"}
# 需要走多模态视觉 LLM 的文件格式
VISION_FORMATS = {"pdf", "png", "jpg", "jpeg", "webp"}


def _extract_text_from_bytes(file_data: bytes, filename: str) -> str:
    """
    从 txt/docx 文件中提取文本内容。
    仅处理文本格式，PDF/图片由多模态路径处理。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "txt":
        return file_data.decode("utf-8", errors="replace")

    if ext in ("docx", "doc"):
        try:
            from io import BytesIO
            from docx import Document
            doc = Document(BytesIO(file_data))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            content = "\n".join(paragraphs)
            if content.strip():
                return content
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="服务器缺少 python-docx 库，无法解析 Word 文件"
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=400,
            detail="Word文件无法提取文字，请确认文件未损坏，或改用txt格式上传。"
        )

    raise HTTPException(status_code=400, detail=f"不支持的文件格式：.{ext}")


# PDF 渲染参数：在清晰度和文件大小之间平衡
# 1.5x 缩放（108 DPI）对文字识别足够，2x 会导致 base64 过大超时
PDF_RENDER_SCALE = 1.5
PDF_MAX_PAGES = 10  # 单次批改最多处理 10 页


def _file_to_base64_images(file_data: bytes, filename: str) -> list[str]:
    """
    将上传文件转为 base64 data URL 列表，供多模态 LLM 识别。

    - PDF：使用 fitz (PyMuPDF) 逐页渲染为 JPEG → base64（比 PNG 小 5-10 倍）
    - 图片：直接编码为 base64
    返回格式：["data:image/jpeg;base64,...", ...]
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        import fitz
        doc = fitz.open(stream=file_data, filetype="pdf")
        page_count = len(doc)
        if page_count > PDF_MAX_PAGES:
            doc.close()
            raise HTTPException(
                status_code=400,
                detail=f"PDF页数过多（{page_count}页），单次批改最多支持{PDF_MAX_PAGES}页，请拆分为多个文件上传。"
            )
        images = []
        for page in doc:
            mat = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
            pix = page.get_pixmap(matrix=mat)
            # JPEG 格式，比 PNG 小 5-10 倍，文字识别足够
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("ascii")
            images.append(f"data:image/jpeg;base64,{b64}")
        doc.close()
        if not images:
            raise HTTPException(status_code=400, detail="PDF文件没有可渲染的页面")
        return images

    if ext in ("png", "jpg", "jpeg", "webp"):
        mime = f"image/{'jpg' if ext == 'jpg' else ext}"
        b64 = base64.b64encode(file_data).decode("ascii")
        return [f"data:{mime};base64,{b64}"]

    raise ValueError(f"不支持转为图片的格式：{ext}")


@router.post("/upload")
async def upload_composition_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """预上传作文文件，返回 file_path 和文件名（含完整文件校验：扩展名/大小/魔数）。"""
    # 复用 assignments 的统一文件校验（扩展名白名单 + 50MB 限制 + 魔数校验）
    from app.api.v1.assignments import _validate_and_read_file
    file_data = await _validate_and_read_file(file)

    storage = StorageService()
    file_path = await storage.save_original(file_data, file.filename, current_user.id)
    return {
        "file_path": file_path,
        "filename": file.filename,
        "size": len(file_data),
    }


async def _validate_and_read_composition_file(file: UploadFile) -> bytes:
    """
    校验作文上传文件并读取内容。

    作文支持 txt/docx（assignments 的统一校验只允许 PDF/图片，会误拒文本格式），
    因此文本格式单独处理：校验大小 + 直接读取；PDF/图片仍走 assignments 的
    统一校验（扩展名白名单 + 魔数校验，防止伪装扩展名的恶意文件）。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    ext = "." + file.filename.rsplit(".", 1)[-1].lower()

    if ext in (".txt", ".doc", ".docx"):
        max_size = get_settings().MAX_UPLOAD_SIZE_MB * 1024 * 1024
        data = await file.read()
        if len(data) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（最大 {get_settings().MAX_UPLOAD_SIZE_MB}MB）",
            )
        return data

    from app.api.v1.assignments import _validate_and_read_file
    return await _validate_and_read_file(file)


async def _merge_upload_files_to_pdf(
    files: list[UploadFile],
    storage: StorageService,
    user_id: int,
) -> tuple[str, bytes]:
    """
    将多个上传文件按顺序合并为一个 PDF。

    合并策略：
    - PDF 文件：逐页插入到目标文档
    - 图片文件（png/jpg/jpeg/webp）：先用 Pillow 转为 PDF 再插入
    - 文件顺序由 files 列表顺序决定（前端保证 = 用户排列的顺序）

    Returns:
        (合并后的 file_url, 合并后的文件内容 bytes)
    """
    import fitz
    from PIL import Image

    merged_doc = fitz.open()

    for f in files:
        from app.api.v1.assignments import _validate_and_read_file
        file_data = await _validate_and_read_file(f)
        ext = f.filename.rsplit(".", 1)[-1].lower() if f.filename and "." in f.filename else ""

        if ext == "pdf":
            src_doc = fitz.open(stream=file_data, filetype="pdf")
            merged_doc.insert_pdf(src_doc)
            src_doc.close()

        elif ext in ("png", "jpg", "jpeg", "webp"):
            img = Image.open(BytesIO(file_data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img_pdf_bytes = BytesIO()
            img.save(img_pdf_bytes, format="PDF")
            img_pdf_bytes.seek(0)
            src_doc = fitz.open(stream=img_pdf_bytes.read(), filetype="pdf")
            merged_doc.insert_pdf(src_doc)
            src_doc.close()

        else:
            raise HTTPException(
                status_code=400,
                detail=f"多文件合并不支持 .{ext} 格式，请使用 PDF 或图片格式",
            )

    if len(merged_doc) == 0:
        raise HTTPException(status_code=400, detail="合并后PDF为空，请检查上传的文件")

    merged_bytes = merged_doc.tobytes()
    merged_doc.close()

    # 合并后的文件名
    base = files[0].filename or "composition"
    merged_name = f"merged_{base}"
    if not merged_name.lower().endswith(".pdf"):
        merged_name = merged_name.rsplit(".", 1)[0] + ".pdf"

    file_url = await storage.save_original(merged_bytes, merged_name, user_id)
    return file_url, merged_bytes


@router.post("/correct", response_model=CompositionResponse)
async def correct_composition(
    file: UploadFile | None = File(None),
    files: ListType[UploadFile] = File(default=[], description="多文件合并批改，按顺序合并后统一批改"),
    subject: str = Form(...),
    grade: str = Form(...),
    title: str = Form(...),
    essay_type: str | None = Form(None, description="英语作文类型：读后续写/应用文/议论文等"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    上传作文文件并触发AI批改（异步：立即返回 pending 记录，批改在后台任务执行）。

    请求内只做"快操作"（校验/合并/存文件 + 建记录），LLM 批改放后台任务
    （composition_tasks._do_correct_composition），上传通道不阻塞，可连续上传。

    支持两种模式：
    - 单文件模式（file）：上传一个文件直接批改，兼容旧版
    - 多文件合并模式（files）：上传多个文件，按顺序合并为 PDF 后统一批改
      适用于题目和作答分散在多张图片的场景

    支持格式：txt / docx / pdf / 图片(png/jpg/webp)
    - PDF/图片：多模态视觉 LLM 直接识别文字并批改
    - txt/docx：提取文本后由文本 LLM 批改
    - 多文件合并仅支持 PDF/图片格式（合并为 PDF 后走多模态路径）
    subject: 语文或英语
    grade: 年级（如"小学三年级"）
    title: 作文题目
    essay_type: 英语作文类型，用于确定默认满分（读后续写=25分，其他=15分）
    """
    from app.services.composition_service import _get_default_full_score

    storage = StorageService()

    # 校验 subject
    if subject not in ("语文", "英语"):
        raise HTTPException(status_code=400, detail="学科只支持语文或英语")

    # 判断是否多文件合并模式
    is_multi_file = files and len(files) > 0

    # ---- 请求内只存文件（快操作），PDF→base64 渲染与 LLM 调用放后台任务 ----
    if is_multi_file:
        file_path, _ = await _merge_upload_files_to_pdf(files, storage, current_user.id)
    elif file:
        file_data = await _validate_and_read_composition_file(file)
        file_path = await storage.save_original(file_data, file.filename, current_user.id)
    else:
        raise HTTPException(status_code=400, detail="请上传作文文件")

    # 立即创建记录（status=pending），满分为默认值，批改结果由后台任务写回
    correction = CompositionCorrection(
        user_id=current_user.id,
        subject=subject,
        title=title,
        total_score=0,
        full_score=_get_default_full_score(subject, essay_type),
        content="",
        grade=grade,
        essay_type=essay_type,
        pdf_url=file_path,
        status="pending",
    )
    db.add(correction)
    await db.flush()
    await db.refresh(correction)

    # 触发后台批改任务（dev: 进程内后台协程；生产: Celery worker）
    settings = get_settings()
    if settings.DEV_MODE:
        from app.tasks.composition_tasks import correct_composition_dev
        correct_composition_dev(correction.id)
    else:
        from app.tasks.composition_tasks import correct_composition
        correct_composition.delay(correction.id)

    return CompositionResponse(
        id=correction.id,
        subject=correction.subject,
        title=correction.title,
        total_score=correction.total_score,
        full_score=correction.full_score,
        word_count=correction.word_count,
        content=correction.content,
        grade=correction.grade,
        essay_type=correction.essay_type,
        dimension_scores=correction.dimension_scores,
        deductions=correction.deductions,
        revision_suggestions=correction.revision_suggestions,
        overall_comment=correction.overall_comment,
        polish_advice=correction.polish_advice,
        sample_essay=correction.sample_essay,
        strict_level=correction.strict_level,
        pdf_url=correction.pdf_url,
        status=correction.status,
        error_message=correction.error_message,
        create_time=correction.create_time.isoformat() if correction.create_time else None,
    )


@router.get("")
async def list_compositions(
    subject: str | None = None,
    grade: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取历史批改列表，支持按年级和科目筛选"""
    conditions = [CompositionCorrection.user_id == current_user.id]
    if subject:
        conditions.append(CompositionCorrection.subject == subject)
    if grade:
        conditions.append(CompositionCorrection.grade == grade)

    result = await db.execute(
        select(CompositionCorrection)
        .where(*conditions)
        .order_by(CompositionCorrection.create_time.desc())
        .limit(50)
    )
    records = result.scalars().all()

    return {
        "items": [
            {
                "id": r.id,
                "subject": r.subject,
                "title": r.title,
                "total_score": r.total_score,
                "full_score": r.full_score,
                "word_count": r.word_count,
                "strict_level": r.strict_level,
                "grade": r.grade,
                "essay_type": r.essay_type,
                "pdf_url": r.pdf_url,
                "status": r.status,
                "error_message": r.error_message,
                "create_time": r.create_time.isoformat() if r.create_time else None,
            }
            for r in records
        ],
        "total": len(records),
    }


@router.get("/{composition_id}", response_model=CompositionResponse)
async def get_composition(
    composition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取批改详情"""
    result = await db.execute(
        select(CompositionCorrection).where(
            CompositionCorrection.id == composition_id,
            CompositionCorrection.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="批改记录不存在")
    return record


@router.get("/{composition_id}/file-url")
async def get_composition_file_url(
    composition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取原始上传文件的访问URL（图片/PDF可直接预览，其他类型用于下载）"""
    result = await db.execute(
        select(CompositionCorrection).where(
            CompositionCorrection.id == composition_id,
            CompositionCorrection.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="批改记录不存在")
    if not record.pdf_url:
        raise HTTPException(status_code=404, detail="原始文件不存在")

    storage = StorageService()
    url = await storage.get_presigned_url(record.pdf_url)
    return {"url": url, "filename": record.pdf_url.rsplit("/", 1)[-1] if "/" in record.pdf_url else record.pdf_url}


def _render_text_to_images(text: str, title: str = "") -> list[str]:
    """
    使用 Pillow 将文本内容渲染为 JPEG 图片，返回 base64 data URL 列表。
    用于 docx/txt 等无法直接预览的文件格式，使其也能以图片形式查看原文。
    支持中文字符渲染（自动查找系统 CJK 字体）。
    """
    from PIL import Image, ImageDraw, ImageFont

    # 查找中文字体
    font_paths = [
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    text_font = None
    title_font = None
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                text_font = ImageFont.truetype(fp, 22)
                title_font = ImageFont.truetype(fp, 30)
                break
            except Exception:
                continue
    if text_font is None:
        text_font = ImageFont.load_default()
        title_font = text_font

    # 布局参数
    img_width = 800
    pad_x = 40
    pad_top = 40
    line_height = 34
    chars_per_line = 38  # 22px 字号下每行约 38 个中文字符

    # 分行处理
    lines: list[str] = []
    for paragraph in text.split("\n"):
        para = paragraph.rstrip()
        if not para:
            lines.append("")
            continue
        # 长段落按 chars_per_line 拆分
        for i in range(0, len(para), chars_per_line):
            lines.append(para[i:i + chars_per_line])

    # 标题区高度
    title_height = 50 if title else 0
    content_height = len(lines) * line_height
    img_height = pad_top * 2 + title_height + content_height + 20

    # 创建画布
    img = Image.new("RGB", (img_width, max(img_height, 400)), "white")
    draw = ImageDraw.Draw(img)

    # 绘制标题
    y = pad_top
    if title:
        draw.text((pad_x, y), f"《{title}》", fill="#333333", font=title_font)
        y += title_height + 10

    # 绘制正文
    for line in lines:
        draw.text((pad_x, y), line, fill="#222222", font=text_font)
        y += line_height

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return [f"data:image/jpeg;base64,{b64}"]


@router.get("/{composition_id}/page-images")
async def get_composition_page_images(
    composition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取作文原文的页面图片列表（所有文件格式统一转为图片）。

    - PDF：逐页渲染为 JPEG
    - 图片(png/jpg/webp)：直接转 base64
    - docx/doc/txt：Pillow 渲染文本为图片
    返回格式：{"pages": ["data:image/jpeg;base64,...", ...], "total": N}
    """
    result = await db.execute(
        select(CompositionCorrection).where(
            CompositionCorrection.id == composition_id,
            CompositionCorrection.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="批改记录不存在")
    if not record.pdf_url:
        raise HTTPException(status_code=404, detail="原始文件不存在")

    storage = StorageService()
    file_data = await storage.get_file_bytes(record.pdf_url)
    if not file_data:
        raise HTTPException(status_code=404, detail="文件数据读取失败")

    # 从 pdf_url 路径中提取文件扩展名
    filename = record.pdf_url.rsplit("/", 1)[-1] if "/" in record.pdf_url else record.pdf_url
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    pages: list[str] = []

    if ext == "pdf":
        # PDF → 逐页渲染为 JPEG base64
        pages = _file_to_base64_images(file_data, filename)
    elif ext in ("png", "jpg", "jpeg", "webp"):
        # 图片 → 直接 base64
        mime = f"image/{'jpg' if ext == 'jpg' else ext}"
        b64 = base64.b64encode(file_data).decode("ascii")
        pages = [f"data:{mime};base64,{b64}"]
    elif ext in ("docx", "doc", "txt"):
        # docx/doc → 提取文本后渲染为图片
        try:
            text = _extract_text_from_bytes(file_data, filename)
        except HTTPException:
            text = record.content or ""
        if not text.strip():
            text = record.content or "（无文字内容）"
        pages = _render_text_to_images(text, record.title)
    else:
        # 其他格式 → 尝试用提取的文本渲染
        text = record.content or "（无文字内容）"
        pages = _render_text_to_images(text, record.title)

    return {"pages": pages, "total": len(pages)}


@router.delete("/{composition_id}")
async def delete_composition(
    composition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除批改记录"""
    result = await db.execute(
        select(CompositionCorrection).where(
            CompositionCorrection.id == composition_id,
            CompositionCorrection.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="批改记录不存在")
    await db.delete(record)
    await db.flush()
    return {"detail": "已删除"}


@router.post("/{composition_id}/re-correct", response_model=CompositionResponse)
async def re_correct_composition(
    composition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    重新批改已存在的作文记录（异步：立即返回 pending，批改在后台任务执行）。

    使用原记录的原始文件和参数重新调用 AI 批改，覆写批改结果。
    - PDF/图片：重新走多模态视觉 LLM 识别+批改
    - txt/docx：用已存储的文本内容重新走文本 LLM 批改
    """
    result = await db.execute(
        select(CompositionCorrection).where(
            CompositionCorrection.id == composition_id,
            CompositionCorrection.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="批改记录不存在")
    if not record.pdf_url:
        raise HTTPException(status_code=400, detail="原始文件不存在，无法重新批改")
    # 仅 correcting（任务真在跑）拒绝重复触发；
    # pending 可能是崩溃残留（任务未启动），允许重触发以自愈
    if record.status == "correcting":
        raise HTTPException(status_code=400, detail="该记录正在批改中，请稍后再试")

    # 重置结果字段并置 pending，批改结果由后台任务写回
    record.status = "pending"
    record.error_message = None
    record.total_score = 0
    record.word_count = 0
    record.content = ""
    record.dimension_scores = None
    record.deductions = None
    record.revision_suggestions = None
    record.overall_comment = None
    record.polish_advice = None
    record.sample_essay = None
    await db.flush()
    await db.refresh(record)

    # 触发后台批改任务（dev: 进程内后台协程；生产: Celery worker）
    settings = get_settings()
    if settings.DEV_MODE:
        from app.tasks.composition_tasks import correct_composition_dev
        correct_composition_dev(record.id)
    else:
        from app.tasks.composition_tasks import correct_composition
        correct_composition.delay(record.id)

    return CompositionResponse(
        id=record.id,
        subject=record.subject,
        title=record.title,
        total_score=record.total_score,
        full_score=record.full_score,
        word_count=record.word_count,
        content=record.content,
        grade=record.grade,
        essay_type=record.essay_type,
        dimension_scores=record.dimension_scores,
        deductions=record.deductions,
        revision_suggestions=record.revision_suggestions,
        overall_comment=record.overall_comment,
        polish_advice=record.polish_advice,
        sample_essay=record.sample_essay,
        strict_level=record.strict_level,
        pdf_url=record.pdf_url,
        status=record.status,
        error_message=record.error_message,
        create_time=record.create_time.isoformat() if record.create_time else None,
    )
