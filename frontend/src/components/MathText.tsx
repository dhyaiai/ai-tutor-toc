/**
 * MathText —— 统一公式渲染组件。
 *
 * 识别题目 / AI 生成题目的文本字段中含 $...$（行内）或 $$...$$（块级）包裹的
 * LaTeX 公式，本组件用 KaTeX 渲染成教材排版效果；不含 $ 的纯文本（存量数据、
 * 语文/英语等无公式场景）直接原样输出，零开销、零兼容成本。
 *
 * 裸 LaTeX 兼容：评分/生成 LLM 偶发把公式输出成不带 $ 分隔符的裸 LaTeX
 * （如 \frac{\sqrt{21}}{7}），这类文本按公式整体渲染，避免源码直接暴露。
 *
 * 兜底策略：单个公式段渲染失败（LLM 偶发输出 KaTeX 不支持的命令）时，
 * 按 fallback 策略显示源码（默认去掉 $ 分隔符），保证内容永远可见、不白屏。
 */
import React, { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

export interface MathTextProps {
  /** 文本内容（可能含 $...$ / $$...$$ 公式），null/undefined 时按空串处理 */
  content?: string | null;
  /** 字号，默认继承父级 */
  fontSize?: number | string;
  /** 强制块级公式居中显示 */
  block?: boolean;
  /** 保留 \n 换行（默认 true，转 <br/>） */
  preserveNewline?: boolean;
  /** 公式渲染失败兜底：raw=显示带 $ 的原文；stripDelimiters=去掉 $ 后显示（默认） */
  fallback?: "raw" | "stripDelimiters";
  className?: string;
  style?: React.CSSProperties;
}

// 切分正则：优先匹配块级 $$...$$，再匹配行内 $...$
// （[^$]+ 保证 $ 不成对时剩余内容归入普通段，不会误伤货币/数字）
const FORMULA_SPLIT_RE = /(\$\$[^$]+\$\$|\$[^$]+\$)/g;

// 裸 LaTeX 识别：文本不含 $ 分隔符，但出现反斜杠命令（\frac、\sqrt、\pi 等）。
// 评分/生成 LLM 偶发把答案输出成不带 $ 的裸 LaTeX（如 \frac{\sqrt{21}}{7}），
// 这类内容按公式整体渲染，避免把 LaTeX 源码当纯文本展示。
// 仅命中题目/答案/解析等教学字段（组件使用场景受限），误伤风险可忽略。
const BARE_LATEX_RE = /\\[a-zA-Z]{2,}/;

/** KaTeX 渲染单个公式（$ 包裹段与裸 LaTeX 段共用）。
 * 导出供 MarkdownPreview 等组件复用（与 MathText 渲染口径完全一致）。 */
export function renderFormula(
  latex: string,
  displayMode: boolean,
  fallback: "raw" | "stripDelimiters" = "stripDelimiters",
  /** 渲染失败时的原文（fallback=raw 时显示带 $ 的原始段） */
  rawText?: string,
): React.ReactNode {
  try {
    const html = katex.renderToString(latex, {
      displayMode,
      // 关闭抛错：KaTeX 内部对非法输入降级为红色错误输出，
      // 外层 try/catch 再兜底一次，双重保证不崩页面
      throwOnError: false,
      strict: false,
    });
    return (
      <span
        className="math-text-formula"
        style={{
          display: displayMode ? "block" : "inline",
          textAlign: displayMode ? "center" : undefined,
          margin: displayMode ? "4px 0" : undefined,
        }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  } catch {
    // 渲染失败兜底：显示原文（保证内容可见，绝不白屏）
    return <>{fallback === "raw" && rawText !== undefined ? rawText : latex}</>;
  }
}

const MathText: React.FC<MathTextProps> = ({
  content,
  fontSize,
  block = false,
  preserveNewline = true,
  fallback = "stripDelimiters",
  className,
  style,
}) => {
  const rendered = useMemo(() => {
    const text = content ?? "";
    // 无 $ 公式：疑似裸 LaTeX（评分 LLM 偶发输出不带 $ 的公式）按公式渲染，
    // 否则直接纯文本输出（存量 Unicode 数据零开销兼容）
    if (!text.includes("$")) {
      if (BARE_LATEX_RE.test(text)) {
        return renderFormula(text, block, fallback, text);
      }
      return <>{text}</>;
    }

    // 按公式/普通段交替切分（split 带捕获组会把分隔符也保留下来）
    const parts = text.split(FORMULA_SPLIT_RE);
    return parts.map((part, i) => {
      const isFormula = part.startsWith("$") && part.endsWith("$");
      if (!isFormula) {
        // 普通文本段：疑似裸 LaTeX 段按行内公式渲染
        if (BARE_LATEX_RE.test(part)) {
          return <React.Fragment key={i}>{renderFormula(part, false, fallback, part)}</React.Fragment>;
        }
        // 普通文本段：React 自动转义，防注入；按需保留换行
        // 多行文本逐行判断裸 LaTeX，避免把整段中文说明塞进公式
        if (preserveNewline && part.includes("\n")) {
          return (
            <React.Fragment key={i}>
              {part.split("\n").map((line, li) => (
                <React.Fragment key={li}>
                  {li > 0 && <br />}
                  {BARE_LATEX_RE.test(line)
                    ? renderFormula(line, false, fallback, line)
                    : line}
                </React.Fragment>
              ))}
            </React.Fragment>
          );
        }
        return <React.Fragment key={i}>{part}</React.Fragment>;
      }

      // 公式段：KaTeX 渲染成教材排版
      const displayMode = part.startsWith("$$") || block;
      const latex = part.startsWith("$$") ? part.slice(2, -2) : part.slice(1, -1);
      return <React.Fragment key={i}>{renderFormula(latex, displayMode, fallback, part)}</React.Fragment>;
    });
  }, [content, block, preserveNewline, fallback]);

  return (
    <span className={["math-text", className].filter(Boolean).join(" ")} style={{ fontSize, ...style }}>
      {rendered}
    </span>
  );
};

export default MathText;
