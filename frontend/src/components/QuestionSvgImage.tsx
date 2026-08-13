import React from "react";

/**
 * AI 同类题配图（SVG）渲染组件。
 *
 * 生成链路：similar_generator 的 prompt 要求 LLM 在题目需要配图时返回纯 SVG 代码
 * （image_svg 字段），这里通过 data URI 渲染。
 * 用 <img> 而非 dangerouslySetInnerHTML 渲染，避免 SVG 内嵌 <script> 带来的 XSS 风险。
 */

/** 将 SVG 代码转为 data URI（需处理中文等非 ASCII 字符，故先 encodeURIComponent 再 btoa） */
export function svgToDataUri(svg: string): string {
  const encoded = btoa(unescape(encodeURIComponent(svg)));
  return `data:image/svg+xml;base64,${encoded}`;
}

interface Props {
  svg: string;
  alt?: string;
  style?: React.CSSProperties;
}

export default function QuestionSvgImage({ svg, alt = "题目配图", style }: Props) {
  if (!svg || typeof svg !== "string") {
    return null;
  }
  return (
    <div style={{ margin: "8px 0", textAlign: "center", ...style }}>
      <img
        src={svgToDataUri(svg)}
        alt={alt}
        style={{ maxWidth: "100%", maxHeight: 300, border: "1px solid #f0f0f0", borderRadius: 6 }}
      />
    </div>
  );
}
