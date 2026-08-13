# -*- coding: utf-8 -*-
"""临时验证脚本:latex_convert.to_plain 转换效果(用文件避免 bash 转义)"""
from app.utils.latex_convert import to_plain

cases = [
    ("已知 $$a_4 = 18$$，求 $a_5$", "inline+block"),
    ("$\\frac{1}{2} + \\sqrt{2} = 1.914$", "frac/sqrt"),
    ("$\\ce{NaCl}$ 溶解", "unsupported cmd"),
    ("$x \\times y \\leq z$", "ops"),
    ("$\\sum_{i=1}^{n} i$", "sum"),
    ("纯文本没有公式", "plain"),
]
for s, tag in cases:
    print(tag, "->", repr(to_plain(s)))
