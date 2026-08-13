"""
批改图片红笔预处理。

老师的红笔批改（补写选项字母、划线、勾叉）容易被多模态模型误认成学生作答。
本模块通过 HSV 颜色空间分离红色像素，为含红笔痕迹的图片生成两个视图
（双图输入策略）：
- [Student Only] 去红版：红笔痕迹已抹除并 inpaint 修复，student_answer 只从这张读取
- [Teacher Marks] 红笔痕迹版：白底，仅保留红笔笔迹及其位置，用于判断对错的旁证

不再送原图：实测表明即使 prompt 声明“以去红版为准”，模型仍会从原图重读
学生字迹，并把恰好落在某选项上的红勾/红斜线误认成“学生选了该选项”
（实例：学生括号内写 B，老师对勾尾巴划过 A.240，模型误识为选 A）；
红笔痕迹版上没有学生笔迹与印刷选项，从根源上消除这类误导。

门控策略：仅当红色像素占比超过阈值时才生成双视图，
无红笔的图片不产生额外的图片与 token 开销。
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 红色像素判定阈值（HSV，OpenCV 中 H 范围 0~180）
# 红色横跨色相环 0 点，需要低区 + 高区两段
# 饱和度下限取 30：出水不足/复印后的淡红笔迹 S 可低至 30~70，
# 阈值过高会漏检，红线灰度化后残留成黑色斜线，遮挡学生数字
# （实例：学生答案 672 被漏检红线划过 6，模型误读为 72）
_RED_LOW_1 = np.array([0, 30, 60])
_RED_HIGH_1 = np.array([10, 255, 255])
_RED_LOW_2 = np.array([160, 30, 60])
_RED_HIGH_2 = np.array([180, 255, 255])

# Lab a* 通道兜底阈值：a* 表征红绿分量（中性色约 128），
# 对低饱和度粉红笔迹比 HSV 更稳定；黑色笔迹 a* 的 P99 约 137，取 140 安全
_LAB_A_THRESHOLD = 140

# 门控：红色像素占比超过 0.02% 且绝对数量超过 200 才认为有红笔痕迹
# （一张 A4 照片上一个红笔字母约占 0.03%，阈值需足够低；
#   绝对数量下限用于过滤高分辨率图片上的零星色噪）
_RED_RATIO_THRESHOLD = 0.0002
_RED_PIXEL_MIN_COUNT = 200

# 红色像素占比上限门控：超过此阈值视为色偏（暖光/白炽灯下整张纸偏红），
# 而非红笔批改，返回 None 回退单图输入。
# 避免 Lab a* 通道在暖光下大面积触发，导致 inpaint 抹掉学生笔迹、批改结果出错。
_RED_RATIO_UPPER_LIMIT = 0.25

# 去红版 JPEG 压缩质量（仅用于识别学生笔迹，无需高保真）
_DERED_JPEG_QUALITY = 60

# 红笔痕迹版 JPEG 压缩质量（白底稀疏笔迹，体积很小）
_MARKS_JPEG_QUALITY = 70

# inpaint 修复半径（像素）：红线划过学生黑笔字迹的交叉处，
# 用周围像素补回被抹掉的笔画，避免断笔
_INPAINT_RADIUS = 3


def _decode(image_bytes: bytes) -> np.ndarray | None:
    """解码图片字节为 BGR 数组，失败返回 None"""
    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        logger.warning("图片解码失败，跳过红笔预处理: %s", e)
        return None


def _red_mask(bgr: np.ndarray) -> np.ndarray:
    """提取红色像素掩码（uint8，红色处为 255）

    HSV 双区间 + Lab a* 通道三路取并集：
    HSV 负责常规红色，Lab a* 兜底低饱和度的淡红/粉红笔迹。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, _RED_LOW_1, _RED_HIGH_1)
    mask2 = cv2.inRange(hsv, _RED_LOW_2, _RED_HIGH_2)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    mask3 = cv2.inRange(lab[:, :, 1], _LAB_A_THRESHOLD, 255)
    return cv2.bitwise_or(cv2.bitwise_or(mask1, mask2), mask3)


def _has_red_marks(mask: np.ndarray) -> bool:
    """根据红色掩码判断是否存在红笔痕迹（门控，含上限保护）"""
    red_count = int(cv2.countNonZero(mask))
    total = mask.shape[0] * mask.shape[1]
    if total == 0:
        return False
    ratio = red_count / total
    if ratio >= _RED_RATIO_UPPER_LIMIT:
        # 红色占比过高，视为色偏而非红笔，回退单图输入
        return False
    return red_count >= _RED_PIXEL_MIN_COUNT and ratio >= _RED_RATIO_THRESHOLD


def build_red_split_views(image_bytes: bytes) -> tuple[bytes, bytes] | None:
    """为含红笔痕迹的图片生成（去红版, 红笔痕迹版）两个视图。

    去红版（[Student Only]）：HSV 红色掩码 → 掩码膨胀（覆盖抗锯齿边缘）
    → inpaint 修复 → 灰度化 → JPEG 压缩。
    红笔痕迹版（[Teacher Marks]）：白底画布上仅保留红色像素，
    保持原图尺寸与位置，便于与去红版对照定位勾/叉/批注落在哪道题上。

    Returns:
        (去红版 JPEG 字节, 红笔痕迹版 JPEG 字节)；
        未检测到红笔痕迹或处理失败时返回 None（调用方回退为单图输入）。
    """
    bgr = _decode(image_bytes)
    if bgr is None:
        return None

    try:
        mask = _red_mask(bgr)
        if not _has_red_marks(mask):
            return None

        # 红笔痕迹版：白底 + 原图红色像素（轻度膨胀保留笔迹边缘，保持可读）
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        marks_mask = cv2.dilate(mask, kernel, iterations=1)
        marks = np.full_like(bgr, 255)
        marks[marks_mask > 0] = bgr[marks_mask > 0]
        ok_marks, marks_buf = cv2.imencode(
            ".jpg", marks, [cv2.IMWRITE_JPEG_QUALITY, _MARKS_JPEG_QUALITY]
        )

        # 记录原始检测到的红色像素数（膨胀前的真实值，用于日志和排查）
        original_red_count = int(cv2.countNonZero(mask))

        # 膨胀掩码：红笔笔画边缘存在半透明/抗锯齿的淡红像素，
        # 不膨胀会在去红后留下红色描边；淡红笔迹边缘渐变更宽，膨胀 2 次
        mask = cv2.dilate(mask, kernel, iterations=2)

        # inpaint：用掩码周围像素修复被抹区域，
        # 红线划过学生黑笔字迹的交叉处笔画基本可重建
        cleaned = cv2.inpaint(bgr, mask, _INPAINT_RADIUS, cv2.INPAINT_TELEA)

        # 灰度化 + 压缩：去红版仅用于认字，减小体积和输入 token
        gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
        ok, buf = cv2.imencode(
            ".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, _DERED_JPEG_QUALITY]
        )
        if not ok or not ok_marks:
            logger.warning("去红版/红笔痕迹版 JPEG 编码失败，回退单图输入")
            return None

        logger.info(
            "检测到红笔痕迹（红色像素 %d 个），已生成去红版 %d KB + 红笔痕迹版 %d KB（原图 %d KB）",
            original_red_count, len(buf) // 1024, len(marks_buf) // 1024,
            len(image_bytes) // 1024,
        )
        return buf.tobytes(), marks_buf.tobytes()
    except Exception as e:
        logger.warning("红笔预处理失败，回退单图输入: %s", e)
        return None


def build_student_only_image(image_bytes: bytes) -> bytes | None:
    """仅生成去红笔版（[Student Only]），兼容旧调用方。

    Returns:
        去红版 JPEG 字节；未检测到红笔痕迹或处理失败时返回 None。
    """
    views = build_red_split_views(image_bytes)
    return views[0] if views is not None else None
