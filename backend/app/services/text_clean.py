"""LLM 输出文本清洗工具。

背景：qwen 系列模型在 JSON 模式输出 LaTeX 时，偶发把反斜杠错误转义为 JSON
的合法转义序列（而不是 \\\\），常见两类：

1. \\b（退格符 0x08）：如 "\\eta" 被写错成 "\\b eta" → 退格字符 + "eta"
2. \\t（制表符 0x09）：如 "\\theta" 被写错成 "\\t heta" → 制表符 + "heta"

解析后这些控制字符不可见，KaTeX 把后面的字母当普通文本渲染，页面显示
"eta"、"heta" 之类乱码。

清洗规则（递归处理 dict/list 中的字符串）：
- 退格符 0x08：无条件还原为反斜杠（实证来自 \\b 误转义）
- 制表符 0x09 及其它控制字符（0x01-0x1F，排除 \\n \\r）：后跟字母时还原为
  反斜杠（LaTeX 命令位置），否则直接删除——正常业务文本不含这类控制字符
"""

import re

# 控制字符正则：退格外的其它控制字符（不含 \n=0x0A、\r=0x0D，它们是合法换行）
_CTRL_RE = re.compile(r"([\x01-\x09\x0b\x0c\x0e-\x1f])(.)")


def _fix_control(m: re.Match) -> str:
    """控制字符 + 后一个字符 → 按 JSON 转义源码还原为 LaTeX 命令。

    模型把 LaTeX 反斜杠错误转义为 JSON 合法转义（\b \t \f 等），解析后变成
    控制字符。按实证规律还原：
    - 0x08（\b 转义）：模型把 \eta 写成 \b+eta（\e 非法转义被"修正"为 \b），
      还原为单个反斜杠（\ + eta = \eta）
    - 0x09（\t 转义）：模型把 \theta 写成 \t+heta，须还原为反斜杠+t
      （\t + heta = \theta）
    - 0x0C（\f 转义）：模型把 \frac 写成 \f+rac，还原为反斜杠+f
    - 其它控制字符：后跟字母时还原为反斜杠，否则删除
    """
    ch = m.group(1)
    nxt = m.group(2)
    if ch == "\x08":
        return "\\" + nxt
    if ch == "\x09":
        return "\\t" + nxt
    if ch == "\x0c":
        return "\\f" + nxt
    if nxt.isalpha() or nxt == "\\" or nxt == "{":
        return "\\" + nxt
    return nxt


def _clean_str(s: str) -> str:
    # 1) 退格符 0x08 → 反斜杠（\b 误转义，实证命中：\eta → \b+eta）
    s = s.replace("\x08", "\\")
    # 2) 制表符 0x09 → \t（反斜杠+t，\theta → \t+heta 场景）
    s = s.replace("\x09", "\\t")
    # 3) 换页符 0x0C → \f（\frac → \f+rac 场景）
    s = s.replace("\x0c", "\\f")
    # 4) 其它控制字符：后跟字母才还原为反斜杠，否则删除
    return _CTRL_RE.sub(_fix_control, s)


def sanitize_llm_controls(value):
    """递归清洗 LLM JSON 输出：dict/list 中的字符串做控制字符清洗。"""
    if isinstance(value, dict):
        return {k: sanitize_llm_controls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_llm_controls(v) for v in value]
    if isinstance(value, str):
        return _clean_str(value)
    return value
