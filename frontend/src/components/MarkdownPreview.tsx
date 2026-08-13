/**
 * MarkdownPreview / RichText —— 富文本渲染组件族。
 *
 * 场景：用户在 bytemd 编辑器里写的是 markdown + $...$/$$...$$ LaTeX 公式 + 图片，
 * 而 react-markdown 不识别 $ 公式（会原样显示源码），因此采用"占位符 token"方案：
 *
 * 1. tokenize：先把公式段切出来，替换成占位 token（@@MATH{index}@@，
 *    markdown 解析器对 @ { } 无任何特殊含义，不会被拆散/转义），
 *    同时记录公式表 maths: [{ latex, display }]
 * 2. react-markdown（remark-gfm + remark-breaks）渲染替换后的文本，
 *    再用 rehype 插件在 hast 树上把 token 文本节点替换为公式元素：
 *    - 行内公式 → span.math-text-formula（KaTeX HTML）
 *    - $$...$$ 块级公式 → div.math-block（paragraph 组件检测后提升输出，div 不能嵌进 p）
 *    - img 组件：限制最大宽度（/api/v1/files/... 相对路径浏览器自动带 cookie）
 *    - remark-breaks：段落内单个换行 → <br>（兼容存量数据中的 \n 换行）
 *
 * 历史教训（几处踩坑，勿回退）：
 * - 最初用 PUA 私用区字符（U+E000/U+E001）做 token，Windows 系统字体把 PUA 字符
 *   映射成图标（Segoe MDL2），且 token 嵌在句子中间时整串 ^...$ 匹配失败，
 *   表现为预览显示"放大镜、0、对号"乱码、公式原样输出 → 改用 ASCII token
 * - react-markdown v10 起（底层 hast-util-to-jsx-runtime）components 表只对 HTML
 *   元素标签名生效，components.text 是死代码——text 节点由库内部直接渲染，
 *   组件层无法还原 token → 必须用 rehype 插件在 hast 树层面替换（勿回退到 text 组件）
 * - 初次实现用整串 ^...$ 匹配还原，公式嵌在句子中间（"已知 $x$ 求值"）时匹配失败
 *   → 必须用 split + 逐段匹配（rehype 插件内同样逐段切分）
 *
 * 裸 LaTeX 兜底（与 MathText 口径一致）：存量 LLM 数据偶发把公式输出成不带 $
 * 分隔符的裸 LaTeX（如 \frac{\sqrt{21}}{7}、48\pi）。按"反斜杠命令 + 连续非空白/
 * 非中文字符"切分出运行串按行内公式渲染，因此可以和 markdown 语法混排
 * （如 "**答案：** 48\pi"），不会把整段中文说明塞进公式。
 *
 * 图片尺寸指令（title 位置，见 parseImageSize）：markdown 标准语法不支持图片尺寸，
 * 本项目约定把尺寸指令放在 title 位置 —— ![alt](url "=WxH")：
 * - "=300x"   → 宽 300px、高自适应
 * - "=300x200" → 宽 300px、高 200px
 * - "=50%"    → 宽 50%、高自适应
 * 之所以不用 markdown-it 风格的 ![alt](url =300x) 后缀：remark-parse 会把该语法
 * 解析失败整段降级为纯文本（已实测），title 位置是 remark 唯一能正常解析的通道。
 * 非尺寸指令的 title（如 "图1"）保持原语义，仅作为 tooltip 显示。
 *
 * 已知限制：fenced code（```...```）内的 $...$ 会被误当公式替换（教学题目内容
 * 几乎不含代码块，接受此限制）。
 */
import React, { useMemo } from "react";
import ReactMarkdown, { type Components as MarkdownComponents } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import katex from "katex";

// 与 MathText.tsx 完全相同的公式切分正则，保证渲染口径一致
const MATH_SPLIT_RE = /(\$\$[^$]+\$\$|\$[^$]+\$)/g;
// 公式占位 token：普通 ASCII 文本，markdown 无特殊含义（勿改回 PUA 字符，见文件头说明）
const TOKEN_RE = /^@@MATH\{(\d+)\}@@$/;
// 全局切分正则（带捕获组，split 保留分隔符）：token 文本段内逐段还原
const TOKEN_GLOBAL_RE = /(@@MATH\{\d+\}@@)/g;

/** 单个公式信息（display=true 表示 $$...$$ 块级公式） */
interface MathInfo {
  latex: string;
  display: boolean;
}

// 裸 LaTeX 运行串（带捕获组，供 split 保留运行串本身）：反斜杠命令（\frac、\sqrt、
// \pi 等）+ 连续非空白/非中文字符（公式内允许 { } \ ^ _ 数字 / + - = 等）。
// 停在空格与中文/中文标点前，避免把句读吞进公式
// （一-鿿 汉字，　-〿、＀-￯ 中文标点）。
const BARE_LATEX_RUN_RE = /(\\[a-zA-Z]{2,}[^\s一-鿿　-〿＀-￯]*)/g;

/**
 * 把 markdown 中的公式段（$...$ / $$...$$ / 裸 LaTeX 运行串）替换为占位 token，
 * 返回替换后的文本与公式表。每个公式段获得唯一 token（@@MATH{index}@@）。
 */
function tokenize(markdown: string): { text: string; maths: MathInfo[] } {
  const maths: MathInfo[] = [];
  const parts = markdown.split(MATH_SPLIT_RE);
  const text = parts
    .map((part) => {
      // 公式段（split 带捕获组会保留分隔符）
      const isFormula = part.startsWith("$") && part.endsWith("$");
      if (isFormula) {
        const display = part.startsWith("$$");
        maths.push({
          latex: display ? part.slice(2, -2) : part.slice(1, -1),
          display,
        });
        return `@@MATH{${maths.length - 1}}@@`;
      }
      // 未闭合 $ 的段落按纯文本处理（$ 不成对时剩余内容归入普通段，不会误伤货币/数字）
      if (part.includes("$")) return part;
      // 裸 LaTeX 兜底：切出反斜杠命令运行串按行内公式渲染。可与 markdown 语法混排
      // （"**答案：** 48\pi"）；无反斜杠命令的纯文本段 split 结果只有一段，原样返回
      const runParts = part.split(BARE_LATEX_RUN_RE);
      if (runParts.length === 1) return part;
      return runParts
        .map((runPart, i) => {
          if (i % 2 === 0) return runPart; // 偶数位：普通文本
          maths.push({ latex: runPart, display: false });
          return `@@MATH{${maths.length - 1}}@@`;
        })
        .join("");
    })
    .join("");
  return { text, maths };
}

/** KaTeX 渲染为 HTML 字符串（公式元素直出用）：失败时转义原文兜底，保证内容可见 */
function katexHtml(latex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      strict: false,
    });
  } catch {
    // 转义 HTML 特殊字符后显示原文（防注入 + 内容可见）
    return latex
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
}

/**
 * 递归替换 hast 树中的 token 文本节点为公式元素：
 * - 行内公式 → <math-formula>（className 为空，渲染 span.math-text-formula）
 * - 块级公式 → <math-formula class="math-block">（paragraph 组件检测后提升，div 不能嵌进 p）
 * 公式 HTML 由 katexHtml 预先算好放进 data-html，渲染组件用 dangerouslySetInnerHTML 直出。
 */
function replaceTokensInTree(tree: Record<string, any>, maths: MathInfo[]) {
  if (!Array.isArray(tree.children)) return;
  tree.children = tree.children.flatMap((child: Record<string, any>) => {
    if (child.type === "element") {
      replaceTokensInTree(child, maths);
      return [child];
    }
    if (child.type !== "text" || !child.value.includes("@@MATH{")) return [child];
    // 逐段切分：token 段还原为公式元素，普通段保持文本（公式嵌在句子中间也命中）
    return child.value.split(TOKEN_GLOBAL_RE).map((part: string) => {
      const m = part.match(TOKEN_RE);
      if (!m) return { type: "text", value: part };
      const info = maths[Number(m[1])];
      if (!info) return { type: "text", value: part }; // 用户内容里手写的同名文本，不处理
      const display = info.display;
      return {
        type: "element",
        tagName: "math-formula",
        properties: {
          // className 供 paragraph 组件检测块级公式做提升输出
          className: display ? "math-block" : undefined,
          "data-html": katexHtml(info.latex, display),
          "data-display": display,
        },
        children: [],
      };
    });
  });
}

/**
 * rehype 插件：在 hast 树上把公式 token 还原为 KaTeX 元素
 * （react-markdown v10 的 components.text 不生效，必须在 rehype 阶段处理，见文件头）。
 * 按 unified 插件协议返回 attacher：use 时调用 attacher 得到转换器，处理时调用转换器。
 */
function rehypeRestoreMath(maths: MathInfo[]) {
  return function attacher() {
    return (tree: Record<string, any>) => {
      replaceTokensInTree(tree, maths);
    };
  };
}

/**
 * 公式元素渲染组件：行内 span / 块级 div，内容为 KaTeX 生成的 HTML。
 * 通过 components["math-formula"] 注册（v10 组件表只认标签名，自定义标签可直接映射）。
 */
const MathFormula: React.FC<{
  "data-html"?: string;
  "data-display"?: boolean;
  className?: string;
}> = ({ "data-html": html, "data-display": display, className }) => {
  if (display) {
    return (
      <div
        className={className || "math-block"}
        style={{ textAlign: "center", margin: "4px 0" }}
        dangerouslySetInnerHTML={{ __html: html ?? "" }}
      />
    );
  }
  return (
    <span
      className="math-text-formula"
      dangerouslySetInnerHTML={{ __html: html ?? "" }}
    />
  );
};

/**
 * 图片尺寸指令解析：title 形如 "=300x" / "=300x200" / "=50%"（见文件头说明），
 * 返回可直接用于 style 的 CSS 宽高（纯数字→px，百分比原样）。非尺寸 title 返回 null。
 * 导出供编辑弹窗（回显当前尺寸）与 ChatDrawer（聊天消息图片）复用。
 * 兼容写法：x 后无数字（"=300x"）等价于"=300"，均表示宽 300px、高自适应。
 */
export function parseImageSize(
  title?: string | null,
): { width: string; height?: string } | null {
  const m = title?.match(/^=(\d+(?:\.\d+)?%|\d+)(?:x(\d+(?:\.\d+)?%|\d+)?)?$/);
  if (!m) return null;
  const toCss = (v: string) => (v.endsWith("%") ? v : `${v}px`);
  const height = m[2] ? toCss(m[2]) : undefined;
  return { width: toCss(m[1]), height };
}

/**
 * 单段 markdown 的渲染：tokenize 后交给 react-markdown，
 * rehype 插件把公式 token 还原为 KaTeX 元素。
 */
export function PreviewBlock({ markdown }: { markdown: string }) {
  const { text, maths } = useMemo(() => tokenize(markdown ?? ""), [markdown]);
  // rehype 插件闭包捕获公式表，tokenize 结果变化时同步重建
  const rehypePlugins = useMemo(() => [rehypeRestoreMath(maths)], [maths]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      rehypePlugins={rehypePlugins}
      components={
        {
          // 公式元素（自定义标签名，react-markdown 组件表按标签名查找）
          "math-formula": MathFormula,
          // 段落：唯一子节点是块级公式（math-formula 且 className=math-block）
          // → 提升输出（div 不能嵌进 p，避免浏览器自动重构布局）
          p: ({ children }: { children?: React.ReactNode }) => {
            const single = Array.isArray(children) ? children : children ? [children] : [];
            if (
              single.length === 1 &&
              React.isValidElement(single[0]) &&
              (single[0].props as { className?: string } | null)?.className === "math-block"
            ) {
              return single[0];
            }
            return <p>{children}</p>;
          },
          // 图片：支持 title 尺寸指令（"=300x" / "=300x200" / "=50%"），
          // 应用指定宽高；其余情况限制最大宽度，防止超宽图撑破预览栏
          img: ({ src, alt, title }: { src?: string; alt?: string; title?: string }) => {
            const size = parseImageSize(title);
            return (
              <img
                src={src}
                alt={alt || ""}
                title={size ? undefined : title} // 尺寸指令不显示为 tooltip
                style={{ maxWidth: "100%", ...(size ?? {}) }}
              />
            );
          },
          // 链接：新标签页打开，避免离开当前页
          a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
            <a href={href} target="_blank" rel="noreferrer">{children}</a>
          ),
        } as unknown as MarkdownComponents
      }
    >
      {text}
    </ReactMarkdown>
  );
}

/** 预览小节：label 为小节标题（如"背景材料""小题 1"），markdown 为可渲染内容 */
export interface PreviewSection {
  label?: string;
  markdown: string;
}

interface MarkdownPreviewProps {
  /** 预览小节列表（大题时多块合并渲染，小节间自动加分隔线） */
  sections: PreviewSection[];
  style?: React.CSSProperties;
}

/**
 * 合并渲染多个预览小节：label 标题 + markdown 内容 + 小节间分隔线。
 * 大题时按 [背景材料] → [父题题干/答案/解析] → [小题 1..N] 顺序传入，
 * 模拟收藏页卡片的混排观感。
 */
const MarkdownPreview: React.FC<MarkdownPreviewProps> = ({ sections, style }) => {
  return (
    <div style={style}>
      {sections.map((section, i) => (
        <div key={i}>
          {section.label && (
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#595959",
                marginBottom: 6,
              }}
            >
              {section.label}
            </div>
          )}
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <PreviewBlock markdown={section.markdown} />
          </div>
          {i < sections.length - 1 && (
            <div
              style={{ borderBottom: "1px dashed #e8e8e8", margin: "12px 0" }}
            />
          )}
        </div>
      ))}
    </div>
  );
};

export default MarkdownPreview;

// ═══════════════════════════════════════════
// RichText —— 教学内容的富文本渲染（MathText 的增强替代）
// ═══════════════════════════════════════════

export interface RichTextProps {
  /** 文本内容（可能含 markdown + $...$ 公式 + ![图片]），null/undefined 时按空串处理 */
  content?: string | null;
  /** 字号，默认继承父级 */
  fontSize?: number | string;
  /** 与 MathText 接口对齐的占位参数（markdown 渲染无强制块级语义，保留兼容） */
  block?: boolean;
  /** 与 MathText 接口对齐：\n 换行已由 remark-breaks 统一转为 <br>，此参数保留兼容 */
  preserveNewline?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * RichText —— 用户可编辑的教学内容字段（题干/答案/解析/背景材料）专用渲染器。
 *
 * 为什么需要：编辑弹窗保存后，这些字段里写的是 markdown（含 **加粗、![图片]、
 * $...$ 公式），MathText 只识别 $ 公式不识别 markdown/图片，会把语法原样显示；
 * RichText 用 markdown 渲染器（公式 token 还原 + 图片限宽 + 裸 LaTeX 兜底），
 * 未编辑的存量纯文本/公式数据同样兼容（纯文本原样输出，行为与 MathText 一致）。
 */
export const RichText: React.FC<RichTextProps> = ({
  content,
  fontSize,
  className,
  style,
}) => {
  return (
    <span
      className={["rich-text", className].filter(Boolean).join(" ")}
      style={{ fontSize, ...style }}
    >
      <PreviewBlock markdown={content ?? ""} />
    </span>
  );
};
