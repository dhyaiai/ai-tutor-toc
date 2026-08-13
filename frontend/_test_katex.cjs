// 临时验证：KaTeX 渲染裸 LaTeX（不带 $ 分隔符）
const katex = require("katex");

const cases = [
  "\\frac{\\sqrt{21}}{7}",  // 用户报告的问题数据
  "48\\pi",
  "\\frac{1}{2}",
  "x = \\frac{a}{b} + \\sqrt{2}",
];
for (const c of cases) {
  try {
    const html = katex.renderToString(c, { displayMode: false, throwOnError: false, strict: false });
    const hasError = html.includes("katex-error");
    console.log(JSON.stringify(c), "=>", hasError ? "RENDER ERROR" : "OK", hasError ? "" : html.replace(/\s+/g, " ").slice(0, 100));
  } catch (e) {
    console.log(JSON.stringify(c), "=> RENDER FAILED:", e.message);
  }
}
