// 临时复现脚本：验证 MarkdownPreview tokenize 后 react-markdown 的还原是否泄漏 token
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const ReactMarkdown = require("react-markdown").default;
const remarkGfm = require("remark-gfm").default;
const remarkBreaks = require("remark-breaks").default;

// 与 MarkdownPreview.tsx 相同的逻辑
const MATH_SPLIT_RE = /(\$\$[^$]+\$\$|\$[^$]+\$)/g;
const TOKEN_RE = /^@@MATH\{(\d+)\}@@$/;
const TOKEN_GLOBAL_RE = /(@@MATH\{\d+\}@@)/g;
const BARE_LATEX_RE = /\\[a-zA-Z]{2,}/;
const MARKDOWN_CHARS_RE = /[*_\[\]!#`>|~]/;

function tokenize(markdown) {
  const maths = [];
  const parts = markdown.split(MATH_SPLIT_RE);
  const text = parts
    .map((part) => {
      const isFormula = part.startsWith("$") && part.endsWith("$");
      if (isFormula) {
        const display = part.startsWith("$$");
        maths.push({ latex: display ? part.slice(2, -2) : part.slice(1, -1), display });
        return `@@MATH{${maths.length - 1}}@@`;
      }
      if (!part.includes("$") && !MARKDOWN_CHARS_RE.test(part) && BARE_LATEX_RE.test(part)) {
        maths.push({ latex: part, display: false });
        return `@@MATH{${maths.length - 1}}@@`;
      }
      return part;
    })
    .join("");
  return { text, maths };
}

function render(markdown) {
  const { text, maths } = tokenize(markdown);
  // 与 MarkdownPreview.tsx 完全一致的 text 组件还原逻辑
  const components = {
    text: ({ children }) => {
      const raw = typeof children === "string" ? children : "";
      const parts = raw.split(TOKEN_GLOBAL_RE);
      const out = parts.map((part, i) => {
        const m = part.match(TOKEN_RE);
        if (!m) return part;
        const info = maths[Number(m[1])];
        if (!info) return `[未还原:${part}]`;
        return info.display ? `<div class="math-block">${info.latex}</div>` : `[公式:${info.latex}]`;
      });
      return React.createElement(React.Fragment, null, out);
    },
  };
  const html = renderToStaticMarkup(
    React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm, remarkBreaks], components }, text)
  );
  console.log("markdown 原文:", markdown);
  console.log("tokenize 后:", text);
  console.log("maths:", JSON.stringify(maths));
  console.log("还原后 HTML:", html);
  console.log("────────────────");
}

// 1. 题干：三棱锥那题（真实数据）
render(
  "在三棱锥 $P-ABC$ 中，平面 $PAB\\perp$ 平面 $ABC$，$PA\\perp PB$，且 $PA=PB=3\\sqrt{2}$，$\\triangle ABC$ 是等边三角形，则该三棱锥外接球的表面积为____。"
);

// 2. 答案预览：**答案：** 48\pi（真实数据）
render("**答案：** 48\\pi");

// 3. 对照组：普通正常题目
render("已知 $x=1$，求 $y$ 的值。");
