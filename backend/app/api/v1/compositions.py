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

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.composition import CompositionCorrection
from app.schemas.composition import (
    CompositionCorrectRequest,
    CompositionResponse,
    CompositionListItem,
)
from app.services.composition_service import CompositionService
from app.services.file_upload import StorageService
from app.services.knowledge_tracker import KnowledgeTracker

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
    """预上传作文文件，返回 file_path 和文件名"""
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{ext}，支持：{', '.join(ALLOWED_EXTENSIONS)}")

    storage = StorageService()
    file_data = await file.read()
    file_path = await storage.save_original(file_data, file.filename, current_user.id)
    return {
        "file_path": file_path,
        "filename": file.filename,
        "size": len(file_data),
    }


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
        file_data = await f.read()
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
    上传作文文件并获取AI批改结果。

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
    storage = StorageService()

    # 校验 subject
    if subject not in ("语文", "英语"):
        raise HTTPException(status_code=400, detail="学科只支持语文或英语")

    # 判断是否多文件合并模式
    is_multi_file = files and len(files) > 0

    if is_multi_file:
        # ---- 多文件合并模式：合并为 PDF → 多模态批改 ----
        file_url, merged_data = await _merge_upload_files_to_pdf(files, storage, current_user.id)
        file_path = file_url
        images = _file_to_base64_images(merged_data, "merged.pdf")
    elif file:
        # ---- 单文件模式（兼容旧版）----
        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式：{ext}")

        file_data = await file.read()
        file_path = await storage.save_original(file_data, file.filename, current_user.id)

        # 仅多模态路径需要提前准备 images
        images = None
        if ext in VISION_FORMATS:
            images = _file_to_base64_images(file_data, file.filename)
    else:
        raise HTTPException(status_code=400, detail="请上传作文文件")

    # 根据文件格式选择处理路径
    service = CompositionService()
    content = ""  # 作文文本，多模态模式下后续从批改结果中补充

    # 加载用户的助教个性化配置（性格/说话风格/评分严格度），对所有批改生效
    from app.services.personality_service import load_personality, build_grading_directive
    personality = await load_personality(db, current_user.id)
    strict_level = personality["strict_level"]
    personality_directive = build_grading_directive(personality)

    try:
        if is_multi_file:
            # 多文件合并后统一走多模态视觉 LLM 批改
            result = await service.correct_multimodal(
                images=images,
                subject=subject,
                grade=grade,
                title=title,
                essay_type=essay_type,
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
            content = result.get("content", "")
        elif ext in VISION_FORMATS:
            # PDF/图片 → 多模态视觉 LLM 识别+批改
            result = await service.correct_multimodal(
                images=images,
                subject=subject,
                grade=grade,
                title=title,
                essay_type=essay_type,
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
            # 多模态模式下作文文本由模型识别返回，存入 MySQL
            content = result.get("content", "")
        else:
            # txt/docx → 提取文本 → 文本 LLM 批改
            content = _extract_text_from_bytes(file_data, file.filename)
            if not content.strip():
                raise HTTPException(status_code=400, detail="未能从文件中提取到文字内容，请检查文件")
            result = await service.correct(
                content=content,
                subject=subject,
                grade=grade,
                title=title,
                essay_type=essay_type,
                strict_level=strict_level,
                personality_directive=personality_directive,
            )
    except ValueError as e:
        # JSON 解析失败等——LLM 返回格式异常，属于服务端可恢复错误
        raise HTTPException(
            status_code=502,
            detail=f"AI批改服务返回数据异常，请稍后重试。错误详情：{str(e)[:200]}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"AI批改请求失败，请稍后重试"
        )

    # 保存记录
    correction = CompositionCorrection(
        user_id=current_user.id,
        subject=subject,
        title=title,
        total_score=result["total_score"],
        full_score=result["full_score"],
        content=content,
        grade=grade,
        essay_type=essay_type,
        dimension_scores=result["dimension_scores"],
        revision_suggestions=result["revision_suggestions"],
        overall_comment=result["overall_comment"],
        polish_advice=result["polish_advice"],
        sample_essay=result["sample_essay"],
        strict_level=strict_level,
        pdf_url=file_path,
    )
    db.add(correction)
    await db.flush()
    await db.refresh(correction)

    # 同步更新知识状态
    try:
        tracker = KnowledgeTracker(db)
        await tracker.update(
            user_id=current_user.id,
            knowledge_points=[{
                "point_name": f"{subject}写作能力",
                "subject": subject,
                "mastery_change": 1 if result["total_score"] / result["full_score"] > 0.7 else -1,
                "behavior_type": "作文提升点" if result["total_score"] / result["full_score"] > 0.7 else "作文扣分点",
            }],
            update_source="作文批改",
        )
    except Exception:
        pass

    return CompositionResponse(
        id=correction.id,
        subject=correction.subject,
        title=correction.title,
        total_score=correction.total_score,
        full_score=correction.full_score,
        content=correction.content,
        grade=correction.grade,
        essay_type=correction.essay_type,
        dimension_scores=correction.dimension_scores,
        revision_suggestions=correction.revision_suggestions,
        overall_comment=correction.overall_comment,
        polish_advice=correction.polish_advice,
        sample_essay=correction.sample_essay,
        strict_level=correction.strict_level,
        pdf_url=correction.pdf_url,
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
                "strict_level": r.strict_level,
                "grade": r.grade,
                "essay_type": r.essay_type,
                "pdf_url": r.pdf_url,
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
    重新批改已存在的作文记录。

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

    storage = StorageService()
    file_data = await storage.get_file_bytes(record.pdf_url)
    if not file_data:
        raise HTTPException(status_code=400, detail="原始文件数据读取失败，可能已被删除")

    # 从 pdf_url 路径中提取文件扩展名
    filename = record.pdf_url.rsplit("/", 1)[-1] if "/" in record.pdf_url else record.pdf_url
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    service = CompositionService()

    if ext in VISION_FORMATS:
        # PDF/图片 → 多模态视觉 LLM 重新识别+批改
        images = _file_to_base64_images(file_data, filename)
        result_data = await service.correct_multimodal(
            images=images,
            subject=record.subject,
            grade=record.grade,
            title=record.title,
            essay_type=record.essay_type,
        )
        content = result_data.get("content", record.content)
    elif ext in ("docx", "doc", "txt"):
        # 文本格式 → 提取文本 → 文本 LLM 批改
        try:
            content = _extract_text_from_bytes(file_data, filename)
        except HTTPException:
            content = record.content
        if not content.strip():
            content = record.content
        if not content.strip():
            raise HTTPException(status_code=400, detail="未能从文件中提取到文字内容")
        result_data = await service.correct(
            content=content,
            subject=record.subject,
            grade=record.grade,
            title=record.title,
            essay_type=record.essay_type,
        )
    else:
        # 其他格式 → 用已存储的文本重新批改
        content = record.content
        if not content.strip():
            raise HTTPException(status_code=400, detail="作文文本为空，无法重新批改")
        result_data = await service.correct(
            content=content,
            subject=record.subject,
            grade=record.grade,
            title=record.title,
            essay_type=record.essay_type,
        )

    # 更新数据库记录
    record.total_score = result_data["total_score"]
    record.full_score = result_data["full_score"]
    record.content = content
    record.dimension_scores = result_data["dimension_scores"]
    record.revision_suggestions = result_data["revision_suggestions"]
    record.overall_comment = result_data["overall_comment"]
    record.polish_advice = result_data["polish_advice"]
    record.sample_essay = result_data["sample_essay"]
    await db.flush()
    await db.refresh(record)

    return CompositionResponse(
        id=record.id,
        subject=record.subject,
        title=record.title,
        total_score=record.total_score,
        full_score=record.full_score,
        content=record.content,
        grade=record.grade,
        essay_type=record.essay_type,
        dimension_scores=record.dimension_scores,
        revision_suggestions=record.revision_suggestions,
        overall_comment=record.overall_comment,
        polish_advice=record.polish_advice,
        sample_essay=record.sample_essay,
        strict_level=record.strict_level,
        pdf_url=record.pdf_url,
        create_time=record.create_time.isoformat() if record.create_time else None,
    )
