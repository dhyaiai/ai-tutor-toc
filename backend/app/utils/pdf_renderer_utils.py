"""
PDF 渲染工具模块。

提供 PDF/图片到页面图片的通用渲染功能，供 assignments.py 等模块复用。
"""

import asyncio
import logging
from typing import List, Tuple

import cv2
import fitz
import numpy as np

from app.services.file_upload import StorageService

logger = logging.getLogger(__name__)

# PDF 最大渲染页数限制：防止用户上传超大 PDF 导致 OOM
# zoom=2.0 下 A4 约 12MB/页，50 页 ≈ 600MB 峰值
MAX_PDF_PAGES = 50


async def render_file_to_page_images(
    file_bytes: bytes,
    storage: StorageService,
    user_id: int,
    assignment_id: int,
    suffix_prefix: str = "page",
) -> List[dict]:
    """
    将文件（PDF 或图片）渲染为页面图片列表。

    Args:
        file_bytes: 文件字节内容
        storage: 存储服务实例
        user_id: 用户 ID
        assignment_id: 作业 ID
        suffix_prefix: 保存文件名的前缀

    Returns:
        页面信息列表，每项包含 page_index, image_url, width, height
    """
    pages = []

    if file_bytes.startswith(b"%PDF"):
        # PDF → 逐页渲染（to_thread 中独立打开/关闭 Document，线程安全）
        rendered_pages = await asyncio.to_thread(_render_pdf_pages_bgr, file_bytes)
        for page_idx, img in rendered_pages.items():
            ok, img_bytes = cv2.imencode(".png", img)
            if not ok:
                logger.error("PDF 第 %d 页 PNG 编码失败", page_idx)
                continue
            page_path = await storage.save_question_image(
                img_bytes.tobytes(), user_id, assignment_id, suffix=f"_{suffix_prefix}_{page_idx}"
            )
            try:
                page_url = await storage.get_presigned_url(page_path)
            except Exception:
                logger.warning("Failed to get presigned URL for %s %d", suffix_prefix, page_idx)
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
            raise ValueError("无法解码图片文件")
        ok, img_bytes = cv2.imencode(".png", img)
        if not ok:
            raise ValueError("图片 PNG 编码失败")
        page_path = await storage.save_question_image(
            img_bytes.tobytes(), user_id, assignment_id, suffix=f"_{suffix_prefix}_0"
        )
        try:
            page_url = await storage.get_presigned_url(page_path)
        except Exception:
            logger.warning("Failed to get presigned URL for %s 0", suffix_prefix)
            page_url = ""
        h, w = img.shape[:2]
        pages.append({
            "page_index": 0,
            "image_url": page_url,
            "width": w,
            "height": h,
        })

    return pages


def _render_pdf_pages_bgr(
    file_bytes: bytes, zoom: float = 2.0, page_indices: set[int] | None = None
) -> dict[int, np.ndarray]:
    """PDF → 指定页（默认全部）的 OpenCV BGR 图像，返回 {page_index: img}。

    同步 CPU 重活（fitz 栅格化 + cv2 转换），多页扫描件可达秒级，
    必须在 asyncio.to_thread 中调用，避免阻塞事件循环。
    每个线程内独立打开/关闭 Document，线程安全。
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        total_pages = len(doc)
        if total_pages > MAX_PDF_PAGES:
            raise ValueError(f"PDF 页数 ({total_pages}) 超过最大限制 ({MAX_PDF_PAGES})")
        out: dict[int, np.ndarray] = {}
        for page_idx, page in enumerate(doc):
            if page_indices is not None and page_idx not in page_indices:
                continue
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            out[page_idx] = img
        return out
    finally:
        doc.close()


def _rotate_and_cut(page_img: np.ndarray, rotation: int,
                    x: float, y: float, w: float, h: float) -> np.ndarray | None:
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


def _merge_images(images: list[np.ndarray]) -> np.ndarray:
    """垂直拼接多张图片"""
    if not images:
        raise ValueError("No images to merge")
    if len(images) == 1:
        return images[0]
    # 统一宽度为最大宽度
    max_w = max(img.shape[1] for img in images)
    resized = []
    for img in images:
        if img.shape[1] != max_w:
            scale = max_w / img.shape[1]
            new_h = int(img.shape[0] * scale)
            img = cv2.resize(img, (max_w, new_h))
        resized.append(img)
    return np.vstack(resized)