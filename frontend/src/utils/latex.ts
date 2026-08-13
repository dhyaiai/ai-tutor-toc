/**
 * LaTeX → 可读纯文本 转换工具。
 *
 * 用于 Excel 导出等非交互场景：题目/答案文本含 $...$ 包裹的 LaTeX 公式，
 * 导出时需降级为可读的 Unicode 文本（如 $\\frac{1}{2}$ → (1)/(2)），
 * 而不是把 $x^2$ 这种源码直接写进 Excel。
 *
 * 不做完美还原，转换目标是"可读"：分数/根号/上下标/常见符号按教材惯例
 * 转成近似写法，未知命令安全降级为纯文本。
 */

/** 切分/定位公式段的统一正则（与 MathText 组件保持一致） */
const FORMULA_RE = /(\$\$[^$]+\$\$|\$[^$]+\$)/g;

/** 裸 LaTeX 识别：不含 $ 分隔符但含反斜杠命令（评分/生成 LLM 偶发输出，如 \frac{\sqrt{21}}{7}） */
const BARE_LATEX_RE = /\\[a-zA-Z]{2,}/;

/** 常见数学符号命令 → Unicode 字符映射（长命令在前，避免前缀误伤） */
const SYMBOL_MAP: [string, string][] = [
  ["\\Rightarrow", "⇒"],
  ["\\rightarrow", "→"],
  ["\\leftrightarrow", "↔"],
  ["\\frac", "/"], // 理论不会走到（先被带参数替换处理），兜底而已
  ["\\sqrt", "√"],
  ["\\sum", "Σ"],
  ["\\int", "∫"],
  ["\\times", "×"],
  ["\\cdot", "·"],
  ["\\div", "÷"],
  ["\\pm", "±"],
  ["\\mp", "∓"],
  ["\\leq", "≤"],
  ["\\geq", "≥"],
  ["\\ge", "≥"],
  ["\\neq", "≠"],
  ["\\ne", "≠"],
  ["\\approx", "≈"],
  ["\\infty", "∞"],
  ["\\pi", "π"],
  ["\\alpha", "α"],
  ["\\beta", "β"],
  ["\\gamma", "γ"],
  ["\\delta", "δ"],
  ["\\theta", "θ"],
  ["\\lambda", "λ"],
  ["\\mu", "μ"],
  ["\\sigma", "σ"],
  ["\\phi", "φ"],
  ["\\omega", "ω"],
  ["\\Delta", "Δ"],
  ["\\Sigma", "Σ"],
  ["\\degree", "°"],
  ["\\circ", "°"],
  ["\\angle", "∠"],
  ["\\parallel", "∥"],
  ["\\perp", "⊥"],
  ["\\ast", "*"],
  ["\\to", "→"],
  ["\\ldots", "…"],
  ["\\cdots", "…"],
  ["\\ ", " "],
];

/** 单个公式串（不含 $ 分隔符）→ 可读文本 */
function latexToPlainInternal(latex: string): string {
  let s = latex;
  // 去掉 \left / \right 配对括号（显示上无意义）
  s = s.replace(/\\left\s*/g, "").replace(/\\right\s*/g, "");
  // 根号 \sqrt{x} → √(x)（先于 \frac 处理，避免 \frac{\sqrt{21}}{7} 的参数里残留命令）
  s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)");
  // 分数 \frac{a}{b} → (a)/(b)（参数支持一层嵌套花括号，如 \frac{\sqrt{21}}{7}）
  s = s.replace(/\\frac\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g, "($1)/($2)");
  // 求和 \sum_{i=1}^{n} → Σ(i=1..n)（先处理带上下限的形式）
  s = s.replace(/\\sum\s*_\{\s*([^{}]*?)\s*\}\s*\^\{\s*([^{}]*?)\s*\}/g, "Σ($1..$2)");
  s = s.replace(/\\sum/g, "Σ");
  // 上下标：^{...} → ^...，_{...} → _...（\pi^2 这类无花括号的保留原样）
  s = s.replace(/\^\{([^{}]*)\}/g, "^$1");
  s = s.replace(/_\{([^{}]*)\}/g, "_$1").replace(/\{\^([^{}]*)\}/g, "^$1").replace(/\{_([^{}]*)\}/g, "_$1");
  // 数学符号命令 → Unicode
  for (const [cmd, unicode] of SYMBOL_MAP) {
    if (s.includes(cmd)) {
      s = s.split(cmd).join(unicode);
    }
  }
  // 残留的未知 LaTeX 命令（\xxx）安全降级：删除反斜杠保留命令名（如 \text{解} → 解 由下一步去花括号）
  s = s.replace(/\\[a-zA-Z]+\b/g, "");
  // 去掉残留花括号
  s = s.replace(/[{}]/g, "");
  // 空白规整
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

/**
 * 把可能含 LaTeX 公式的文本转为可读纯文本。
 * 不含 $ 时原样返回（零开销）；公式段逐一转换，转换失败保留公式原文。
 */
export function latexToPlain(text: string | null | undefined): string {
  const raw = text ?? "";
  if (!raw.includes("$")) {
    // 无 $ 时若疑似裸 LaTeX（如 \frac{\sqrt{21}}{7}）也按公式降级，
    // 避免 Excel 导出出现 LaTeX 源码
    if (BARE_LATEX_RE.test(raw)) {
      try {
        return latexToPlainInternal(raw);
      } catch {
        return raw;
      }
    }
    return raw;
  }
  return raw.replace(FORMULA_RE, (match) => {
    const latex = match.startsWith("$$") ? match.slice(2, -2) : match.slice(1, -1);
    try {
      return latexToPlainInternal(latex);
    } catch {
      // 转换失败：去掉 $ 分隔符显示源码，保证内容不丢
      return latex;
    }
  });
}
