"""LaTeX → 可读 Unicode 文本 转换工具。

用途：PDF 错题订正本、报告等"非交互出口"渲染前，把含 $...$ / $$...$$ 包裹的
LaTeX 公式降级为可读纯文本（如 $\\frac{1}{2}$ → 1/2），避免 PDF 里出现
$x^2$ 这类源码。基于 pylatexenc.latex2text（纯 Python、无重依赖），
转换失败时安全返回原文，绝不抛异常打断渲染流程。
"""

import logging
import re

from pylatexenc.latex2text import LatexNodes2Text

logger = logging.getLogger(__name__)

# 公式切分正则：优先块级 $$...$$，再行内 $...$（与前端 MathText/latex.ts 一致）
_FORMULA_RE = re.compile(r"(\$\$[^$]+\$\$|\$[^$]+\$)")

# 裸 LaTeX 识别：不含 $ 分隔符但含反斜杠命令（评分 LLM 偶发输出，如 \frac{\sqrt{21}}{7}）
_BARE_LATEX_RE = re.compile(r"\\[a-zA-Z]{2,}")

# pylatexenc 转换器：默认配置即可正确处理 \frac/\sqrt/上下标等常见命令
_converter = LatexNodes2Text()

# 求和符号可读化后处理：pylatexenc 把 \sum_{i=1}^{n} 输出为 "∑_i=1^n"，
# 转成更接近教材的 "Σ(i=1..n)"（与前端 latexToPlain 的格式保持一致）
_SUM_RE = re.compile(r"∑_([^\s^]+)\^([^\s]+)")

# 数字下标可读化：pylatexenc 把 a_4 输出为 "a_4"，转成 Unicode 下标 "a₄"
_SUB_RE = re.compile(r"_(\d+)")
_SUB_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _to_plain_latex(latex: str) -> str:
    """单个公式串（不含 $ 分隔符）→ 可读文本。"""
    try:
        text = _converter.latex_to_text(latex).strip()
        # 求和式后处理，提升可读性
        text = _SUM_RE.sub(lambda m: f"Σ({m.group(1)}..{m.group(2)})", text)
        # 数字下标后处理：a_4 → a₄
        return _SUB_RE.sub(lambda m: m.group(1).translate(_SUB_MAP), text)
    except Exception:
        # 转换失败：去掉 $ 分隔符显示源码，保证内容不丢
        return latex


def to_plain(text: str | None) -> str:
    """把可能含 LaTeX 公式的文本转为可读纯文本；不含 $ 时原样返回。"""
    if not text:
        return text or ""
    if "$" not in text:
        # 无 $ 时若疑似裸 LaTeX（评分 LLM 偶发输出，如 \frac{\sqrt{21}}{7}）
        # 也按公式降级，避免 PDF 里出现 LaTeX 源码
        if _BARE_LATEX_RE.search(text):
            return _to_plain_latex(text)
        return text

    def _replace(match: re.Match) -> str:
        part = match.group(1)
        latex = part[2:-2] if part.startswith("$$") else part[1:-1]
        return _to_plain_latex(latex)

    try:
        return _FORMULA_RE.sub(_replace, text)
    except Exception as e:
        # 极端兜底：整体转换失败时返回原文，保证内容可见
        logger.warning("LaTeX→纯文本转换异常，返回原文: %s", e)
        return text
