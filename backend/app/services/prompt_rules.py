"""公共 Prompt 规则常量。

各 LLM 调用的 prompt 共享的书写规则。注意:这些常量是普通字符串
(不是 f-string),因为内容包含大量 { } 花括号;由使用方通过模板
占位符(如 {no_latex_rule})或字符串拼接注入。

背景:原各 prompt 统一"禁止 LaTeX、公式用 Unicode 纯文本",现改为
"公式用 LaTeX 书写 + $ 包裹",前端 KaTeX 渲染成教材排版效果。
例外:common_mistakes / knowledge_points 两个列表字段保持纯文本,
豁免公式书写要求(见下方 FORMULA_RULE 第 19 行后的豁免条款)。
"""

FORMULA_RULE = (
    "【公式书写要求(重要)】\n"
    "- 数学公式必须用 LaTeX 书写,并用 $...$ 包裹(行内公式)或 $$...$$ 包裹(独立成行的块级公式)。前端将用 KaTeX 渲染成教材排版效果。\n"
    "- 正确示例:$\\frac{1}{2}$、$\\sqrt{2}$、$x^2+2x-1=0$、$\\angle A=30^\\circ$、$$\\frac{4}{3}\\pi r^3$$\n"
    "- 分数必须用 \\frac{a}{b};根号用 \\sqrt{...};上下标用 ^{...} 与 _{...};乘号用 \\times 或 \\cdot;不等式用 \\leq、\\geq、\\neq;希腊字母用 \\alpha 等。\n"
    "- 公式与相邻中文之间留一个空格,如\"已知 $x^2=4$,求 $x$ 的值\"。\n"
    "- 严禁裸写 LaTeX 命令而不加 $ 包裹(如严禁直接写 \\frac{1}{2} 而不带 $...$)。\n"
    "- 仅使用 KaTeX 支持的标准 LaTeX 命令(\\frac \\sqrt \\sum \\int \\begin{aligned} \\begin{cases} \\text{...} 等),不得使用 \\ce、\\cancel 等 KaTeX 不支持的命令;简单化学式(如 H₂O、CO₂、2H₂O)和化学方程式直接用普通文本加 Unicode 下标数字。\n"
    "- 题干、选项、答案、解析等展示给学生的文本字段中出现的所有公式一律按此规则。\n"
    "- 例外(重要):common_mistakes(常见错误)与 knowledge_points(知识点)两个列表字段一律用纯文本书写,严禁出现 $...$、$$...$$ 或任何 LaTeX 命令(\\frac、\\sqrt、\\times 等)。如需提及公式,用中文与 Unicode 字符描述,例如不得写\"误用 $a^2+b^2=c^2$\",应写\"误用勾股定理的平方关系\"。\n"
    "- 输出前自查:$ 必须成对出现;\\frac、\\sqrt 等命令必须带完整花括号参数。"
)
