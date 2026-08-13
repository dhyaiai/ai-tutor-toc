"""本地文件服务（仅 DEV 模式）。

从 main.py 拆分出来，职责：
- 提供 /api/v1/files/{file_path} 路由
- 路径穿越防护
- 私有目录鉴权（reports/、oral_audio/）
- 容错查找（LLM 生成错误路径时模糊匹配）
- PDF/HTML 报告处理
"""

import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db
from app.core.security import decode_token, get_current_user
from app.models.user import User

settings = get_settings()

# 文件上传/访问路由（GET /{file_path:path} 由 main.py 的函数级路由注册，见 main.py）
router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload")
async def upload_editor_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """编辑器粘贴图片上传——返回可直接用于 <img>/markdown 的访问 URL。

    存到 editor/{user_id}/ 私有目录：读取侧由 _check_private_dir_access
    对所有 {prefix}/{user_id}/ 三段路径统一做归属鉴权（登录 + 只能访问自己的目录），
    无需额外鉴权代码；生产 MinIO 下该前缀同样天然私有。
    文件校验复用 assignments.py 的统一校验（扩展名/魔数/大小上限）。
    """
    # 延迟 import：避免在模块顶部引入 assignments（仅此函数需要其校验工具）
    from app.api.v1.assignments import _validate_and_read_file
    from app.services.file_upload import StorageService

    content = await _validate_and_read_file(file)
    ext = file.filename.rsplit(".", 1)[-1].lower()
    object_name = f"editor/{current_user.id}/{uuid.uuid4().hex}.{ext}"
    # save_file 自带路径穿越防护（resolve 后确认仍在存储根目录内）
    storage = StorageService()
    await storage.save_file(object_name, content)
    return {
        "file_path": object_name,
        "url": await storage.get_presigned_url(object_name),
    }


def _sanitize_filename(filename: str) -> str:
    """清理文件名，防止 Content-Disposition 头注入。

    移除控制字符和引号，避免头部污染。
    """
    # 移除控制字符、引号、分号（这些字符在 Content-Disposition 头中有特殊含义）
    cleaned = re.sub(r'[\r\n";\x00-\x1f]', "", filename)
    # 如果清理后为空，返回默认文件名
    return cleaned or "download"


async def _resolve_user_id_from_token(request: Request, db: AsyncSession) -> int | None:
    """解析登录凭证，校验 token 版本号（单设备登录）。

    凭证来源（按优先级）：
    1. Authorization: Bearer <token> 头 —— axios/fetch 请求（AuthedAudio 等）
    2. access_token cookie —— 浏览器 <img>/<audio> 标签自动携带，
       登录/刷新时由 auth.py 种下（仅 DEV 模式），
       用于 /api/v1/files/ 私有目录（作业切图/原图/音频）的加载鉴权
    """
    token: str | None = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = decode_token(token, settings)
        if payload.get("type") != "access" or not payload.get("sub"):
            return None
        user_id = int(payload["sub"])
        if settings.SINGLE_DEVICE_LOGIN:
            from app.models.user import User
            user = await db.get(User, user_id)
            if user is None or int(payload.get("version", 0)) != user.token_version:
                return None
        return user_id
    except (JWTError, ValueError, KeyError):
        return None


def _check_path_safety(full_path: Path, storage_root: Path) -> None:
    """路径穿越防护：确保 full_path 在 storage_root 内。"""
    full_path = full_path.resolve()
    if not full_path.is_relative_to(storage_root):
        raise HTTPException(status_code=403, detail="Access denied")


def _check_private_dir_access(full_path: Path, storage_root: Path, user_id: int | None) -> None:
    """私有目录鉴权：所有 {prefix}/{user_id}/ 形式的目录必须登录且只能访问自己的目录。

    覆盖范围：reports/、oral_audio/、originals/、questions/、answers/ 等全部前缀，
    凡是路径第二段为数字 user_id 的，一律校验归属，防止越权读取他人作业/答案文件。
    """
    rel_parts = full_path.relative_to(storage_root).parts
    # 路径格式为 {prefix}/{user_id}/... 时进行归属校验
    if len(rel_parts) >= 3 and rel_parts[1].isdigit():
        owner_id = int(rel_parts[1])
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if owner_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")


async def serve_local_file(file_path: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Serve uploaded files from local storage (dev mode only)."""
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not available in production mode")

    storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
    full_path = (Path(settings.LOCAL_STORAGE_DIR) / file_path).resolve()
    _check_path_safety(full_path, storage_root)

    # 解析登录凭证
    token_user_id = await _resolve_user_id_from_token(request, db)

    # 私有目录鉴权（覆盖所有 {prefix}/{user_id}/ 形式的目录）
    _check_private_dir_access(full_path, storage_root, token_user_id)

    # 文件不存在时直接返回 404（不再模糊查找，避免越权探测）
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # PDF 文件响应策略：
    # - reports/oral_audio 目录（含学生个人成绩/测评报告）：强制 attachment 下载，
    #   避免浏览器缓存敏感内容到本地
    # - 其他目录（作业原图等）：inline 内联预览
    filename = _sanitize_filename(full_path.name)
    if full_path.suffix == ".pdf":
        rel_parts = full_path.relative_to(storage_root).parts
        is_private = len(rel_parts) > 0 and rel_parts[0] in ("reports", "oral_audio")
        disposition = "attachment" if is_private else "inline"
        return FileResponse(
            str(full_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )

    # 报告类 HTML：下载时即时转成 PDF 并缓存
    if "reports" in full_path.parts and full_path.suffix == ".html":
        pdf_path = full_path.with_suffix(".pdf")
        if not pdf_path.exists():
            from app.services.pdf_renderer import render_html_to_pdf
            pdf_bytes = await render_html_to_pdf(full_path.read_text(encoding="utf-8"))
            if pdf_bytes:
                pdf_path.write_bytes(pdf_bytes)
        if pdf_path.exists():
            pdf_filename = _sanitize_filename(pdf_path.name)
            return FileResponse(
                str(pdf_path),
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{pdf_filename}"'},
            )

    if "reports" in full_path.parts or full_path.suffix == ".html":
        # 对 HTML 响应加 CSP，防止 LLM 输出的学情文本中含恶意脚本在本站源下执行
        return FileResponse(
            str(full_path),
            media_type="text/html",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; script-src 'none';",
            },
        )

    return FileResponse(str(full_path))
