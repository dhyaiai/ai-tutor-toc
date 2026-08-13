// 临时验证：latexToPlain 裸 LaTeX 处理（与 src/utils/latex.ts 逻辑一致）
const FORMULA_RE = /(\$\$[^$]+\$\$|\$[^$]+\$)/g;
const BARE_LATEX_RE = /\\[a-zA-Z]{2,}/;

function latexToPlainInternal(latex) {
  let s = latex;
  s = s.replace(/\\left\s*/g, "").replace(/\\right\s*/g, "");
  s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)");
  s = s.replace(/\\frac\s*\{((?:[^{}]|\{[^{}]*\})*)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g, "($1)/($2)");
  s = s.replace(/\\sum\s*_\{\s*([^{}]*?)\s*\}\s*\^\{\s*([^{}]*?)\s*\}/g, "Σ($1..$2)");
  s = s.replace(/\\sum/g, "Σ");
  s = s.replace(/\^\{([^{}]*)\}/g, "^$1");
  s = s.replace(/_\{([^{}]*)\}/g, "_$1").replace(/\{\^([^{}]*)\}/g, "^$1").replace(/\{_([^{}]*)\}/g, "_$1");
  const SYMBOL_MAP = [
    ["\\Rightarrow", "⇒"], ["\\frac", "/"], ["\\sqrt", "√"], ["\\sum", "Σ"],
    ["\\times", "×"], ["\\cdot", "·"], ["\\div", "÷"], ["\\pm", "±"],
    ["\\leq", "≤"], ["\\geq", "≥"], ["\\neq", "≠"], ["\\approx", "≈"],
    ["\\infty", "∞"], ["\\pi", "π"], ["\\perp", "⊥"], ["\\circ", "°"],
    ["\\angle", "∠"], ["\\parallel", "∥"], ["\\alpha", "α"], ["\\theta", "θ"],
  ];
  for (const [cmd, unicode] of SYMBOL_MAP) {
    if (s.includes(cmd)) s = s.split(cmd).join(unicode);
  }
  s = s.replace(/\\[a-zA-Z]+\b/g, "");
  s = s.replace(/[{}]/g, "");
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

function latexToPlain(raw) {
  const text = raw ?? "";
  if (!text.includes("$")) {
    if (BARE_LATEX_RE.test(text)) {
      try { return latexToPlainInternal(text); } catch { return text; }
    }
    return text;
  }
  return text.replace(FORMULA_RE, (match) => {
    const latex = match.startsWith("$$") ? match.slice(2, -2) : match.slice(1, -1);
    try { return latexToPlainInternal(latex); } catch { return latex; }
  });
}

const cases = [
  "\\frac{\\sqrt{21}}{7}",       // 用户报告的问题数据
  "48\\pi",                      // 同作业第10题
  "AF⊥SC",                       // Unicode 纯文本，不应误伤
  "证明见解析",                   // 纯中文，不应误伤
  "C:\\Users\\Admin",            // Windows 路径（理论场景），接受可读输出即可
  "已知 $x^2=4$，解得 $x=2$",    // 正常 $ 包裹，行为不变
  "2019年12月",
];
for (const c of cases) {
  console.log(JSON.stringify(c), "=>", JSON.stringify(latexToPlain(c)));
}
