// 临时验证脚本：编译真实 MarkdownPreview.tsx 并用 react-dom/server 渲染，
// 覆盖用户报告的两个问题场景 + 回归场景。
const path = require("path");
const React = require("react");
const { build } = require("esbuild");

(async () => {
  const result = await build({
    entryPoints: [path.join(__dirname, "src/components/MarkdownPreview.tsx")],
    bundle: true,
    format: "cjs",
    platform: "node",
    external: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"],
    write: false,
    jsx: "automatic",
    logLevel: "silent",
  });
  const code = result.outputFiles[0].text;
  const mod = { exports: {} };
  new Function("module", "exports", "require", code)(mod, mod.exports, require);
  const PreviewBlock = mod.exports.PreviewBlock;
  const MarkdownPreview = mod.exports.default;
  const RichText = mod.exports.RichText;

  const { renderToStaticMarkup } = require("react-dom/server");

  const cases = [
    ["① 三棱锥题干（6 个 $ 公式，用户场景）",
      "在三棱锥 $P-ABC$ 中，平面 $PAB\\perp$ 平面 $ABC$，$PA\\perp PB$，且 $PA=PB=3\\sqrt{2}$，$\\triangle ABC$ 是等边三角形，则该三棱锥外接球的表面积为____。"],
    ["② 编辑弹窗预览答案段（**答案：** 48\\pi，用户场景）",
      "**答案：** 48\\pi"],
    ["③ 普通题目对照组", "已知 $x=1$，求 $y$ 的值。"],
    ["④ 块级公式 $$...$$", "$$\\frac{\\sqrt{21}}{7}$$"],
    ["⑤ 题干+答案+解析 完整预览（模拟 QuestionEditModal 拼接）",
      "在三棱锥 $P-ABC$ 中，平面 $PAB\\perp$ 平面 $ABC$。\n\n**答案：** 48\\pi\n\n**解析：** 利用 $\\triangle ABC$ 外接圆半径 $r$，球心到顶点距离相等列方程。"],
    ["⑥ 纯文本（无公式，语文题）", "请赏析文章第三段的环境描写作用。"],
    ["⑦ 裸 LaTeX 单独出现", "\\frac{2\\sqrt{3}}{3}"],
  ];

  for (const [name, md] of cases) {
    const html = renderToStaticMarkup(React.createElement(PreviewBlock, { markdown: md }));
    const leaked = html.includes("@@MATH{");
    console.log(`【${name}】${leaked ? "❌ 泄漏 token!" : "✓"}`);
    // 截取关键片段验证公式是否渲染为 KaTeX
    const ka = html.match(/<span class="math-text-formula"[^>]*>[\s\S]{0,120}/);
    if (ka) console.log("  行内公式:", ka[0].replace(/\s+/g, " ").slice(0, 130));
    if (html.includes("math-block")) {
      const mb = html.match(/<div class="math-block"[^>]*>[\s\S]{0,100}/);
      if (mb) console.log("  块级公式:", mb[0].replace(/\s+/g, " ").slice(0, 110));
    }
    const strong = html.match(/<strong>[\s\S]{0,60}<\/strong>[\s\S]{0,80}/);
    if (strong) console.log("  答案段:", strong[0].replace(/\s+/g, " ").slice(0, 140));
    console.log();
  }

  // 用真实数据跑一遍 MarkdownPreview（sections 形式，模拟编辑弹窗右栏）
  const sections = [
    { label: "第 1 题", markdown: "在三棱锥 $P-ABC$ 中，平面 $PAB\\perp$ 平面 $ABC$，$PA\\perp PB$，且 $PA=PB=3\\sqrt{2}$，$\\triangle ABC$ 是等边三角形，则该三棱锥外接球的表面积为____。" },
    { label: "答案", markdown: "**答案：** 48\\pi" },
  ];
  const html2 = renderToStaticMarkup(React.createElement(MarkdownPreview, { sections }));
  console.log("MarkdownPreview sections 渲染，泄漏 token:", html2.includes("@@MATH{") ? "❌ 有" : "✓ 无");
  const pi = html2.includes("π") || /class="math-text-formula"/.test(html2);
  console.log("答案区公式渲染:", pi ? "✓" : "❌ 未渲染");
})().catch((e) => { console.error(e); process.exit(1); });
