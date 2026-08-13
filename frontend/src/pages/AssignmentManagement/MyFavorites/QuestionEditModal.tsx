/**
 * QuestionEditModal —— 收藏题目"编辑"三栏弹窗。
 *
 * 布局（左中右）：
 * - 左栏：原题图像（错题显示切割原图 image_url；AI 题优先显示上传转录的原图 image_url，
 *         无原图回落 SVG 配图或空态）
 * - 中栏：编辑区（bytemd markdown 编辑器，支持 KaTeX 公式 + 粘贴图片自动上传）。
 *         错题/独立题按 [题干/答案/解析] 三个编辑器分块；大题按 [背景材料/父题/各小题] 折叠分块
 * - 右栏：实时预览（MarkdownPreview：markdown 语法 + $ 公式 + 图片完整渲染）
 *
 * 保存语义：全量覆盖发送（错题走 /questions/{id}/content，AI 题走 /ai-questions/{anchor}/content），
 * 只允许内容字段（题干/答案/解析），不触碰成绩/状态/选项/配图等。
 *
 * 队列进度：上传转录后逐题检查时传入 queueIndex/queueTotal，标题与保存提示显示"第 x/N 题"，
 * 避免"保存后弹出下一题"被误解为弹窗重复弹出；手动编辑单题时不传，行为与之前一致。
 */
import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  Button, Collapse, Empty, Image, Input, InputNumber, message, Modal, Radio, Space, Spin, Typography,
} from "antd";
import { Editor } from "@bytemd/react";
import gfm from "@bytemd/plugin-gfm";
import math from "@bytemd/plugin-math";
import zh_Hans from "bytemd/locales/zh_Hans.json";
import "bytemd/dist/index.css";
import type { BytemdPlugin } from "bytemd";
import type { FavoriteUnion } from "../../../services/favoriteService";
import type { SubQuestionItem } from "../../../services/errorQuestionService";
import type { AISubQuestionItem } from "../../../services/aiQuestionService";
import { questionService } from "../../../services/questionService";
import { aiQuestionService } from "../../../services/aiQuestionService";
import { assignmentService } from "../../../services/assignmentService";
import QuestionSvgImage from "../../../components/QuestionSvgImage";
import MarkdownPreview, { parseImageSize } from "../../../components/MarkdownPreview";

// bytemd 插件实例（模块级单例，避免每次渲染重建插件导致编辑器重挂载）
const PLUGINS = [gfm(), math()];

// 编辑器高度覆盖：bytemd 默认 300px，弹窗内多个编辑器叠放会太高，统一收窄为 220px
const EDITOR_CSS = `
.edit-modal-editor { height: 220px; margin-bottom: 12px; }
.edit-modal-editor .bytemd { height: 100%; border-radius: 6px; }
.edit-modal-editor .bytemd .bytemd-toolbar { border-radius: 6px 6px 0 0; }
/* 编辑器内图片语法高亮（CM5 markText）：提示可点击调整尺寸 */
.edit-modal-editor .md-img-token { cursor: pointer; color: #1677ff; border-bottom: 1px dashed #1677ff; }
`;

/**
 * ── 图片尺寸支持 ─────────────────────────────────────────────
 * 语法（title 位置，与 MarkdownPreview.parseImageSize 口径一致）：
 *   ![alt](url "=300x")    宽 300px、高自适应
 *   ![alt](url "=300x200") 宽 300px、高 200px
 *   ![alt](url "=50%")     宽 50%、高自适应
 * 编辑器内图片语法高亮显示，点击（或选中后点工具栏"图片尺寸"按钮）
 * 弹出设置弹窗，应用后改写源码；右栏预览实时反馈。
 *
 * 点击实现的坑（勿回退）：
 * - codemirror-ssr（bytemd 1.22 的依赖，CM5 的 SSR 兼容 fork）不触发 "click" 事件，
 *   源码中无任何 click 触发逻辑 → editor.on("click") 永远不执行，
 *   点击入口必须用 React 事件委托（EditableEditor 容器 div 的 onMouseUp）
 * - codemirror-ssr 的 coordsChar 默认 mode 是 "local"（非标准 CM5 的 "window"），
 *   会把视口坐标叠加 sizer 偏移导致命中失败 → 必须显式传 "window" + clientX/clientY
 */

/** 完整 markdown 图片语法（含可选尺寸 title）—— 全局扫描/行内探测共用 */
const IMAGE_SYNTAX_RE = /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g;

/** 工具栏"图片尺寸"按钮图标（bytemd icon 字段是内联 SVG 字符串，不支持 ReactNode） */
const IMG_SIZE_ICON =
  '<svg viewBox="0 0 1024 1024" width="16" height="16" xmlns="http://www.w3.org/2000/svg">' +
  '<path d="M928 160H96c-17.7 0-32 14.3-32 32v640c0 17.7 14.3 32 32 32h832c17.7 0 32-14.3 32-32V192c0-17.7-14.3-32-32-32zM338 304c35.3 0 64 28.7 64 64s-28.7 64-64 64-64-28.7-64-64 28.7-64 64-64zm513.9 437.1a8.11 8.11 0 0 1-5.2 1.9H177.2c-4.4 0-8-3.6-8-8 0-1.9.7-3.7 1.9-5.2l170.3-202c2.8-3.4 7.9-3.8 11.3-1 .3.3.7.6 1 1l99.4 118 158.1-187.5c2.8-3.4 7.9-3.8 11.3-1 .3.3.7.6 1 1l229.6 271.6c2.6 3.3 2.2 8.4-1.2 11.2z"/>' +
  "</svg>";

/** CM5 编辑器最小接口（bytemd 插件 ctx.editor 即此类型，避免强依赖 @types/codemirror） */
interface CmEditor {
  getValue(): string;
  getSelection(): string;
  getCursor(start?: string): { line: number; ch: number };
  getLine(line: number): string;
  indexFromPos(pos: { line: number; ch: number }): number;
  posFromIndex(index: number): { line: number; ch: number };
  getRange(from: { line: number; ch: number }, to: { line: number; ch: number }): string;
  replaceRange(text: string, from: { line: number; ch: number }, to?: { line: number; ch: number }): void;
  markText(
    from: { line: number; ch: number },
    to: { line: number; ch: number },
    options?: { className?: string },
  ): { clear(): void };
  on(event: string, handler: (...args: any[]) => void): void;
  off(event: string, handler: (...args: any[]) => void): void;
  /** mode 必须显式传 "window"（视口坐标）：codemirror-ssr 默认 "local" 会错误叠加偏移 */
  coordsChar(pos: { left: number; top: number }, mode?: string): { line: number; ch: number };
}

/** 编辑弹窗中命中（选中/点击）的图片语法，index 为文档字符偏移（CM5 posFromIndex 互转） */
interface ResizeTarget {
  index: number;
  endIndex: number;
  /** 图片语法原始文本 */
  text: string;
  alt: string;
  url: string;
  title?: string;
  /** 已解析尺寸（CSS 值，如 width: "50%" / "300px"） */
  size?: { width: string; height?: string };
}

/** 解析完整图片语法文本（含可选尺寸 title）→ 命中返回 alt/url/title/size，否则 null */
function parseImageSyntax(text: string): Omit<ResizeTarget, "index" | "endIndex" | "text"> | null {
  const m = text.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)$/);
  if (!m) return null;
  const title = m[3];
  return { alt: m[1], url: m[2], title, size: parseImageSize(title) ?? undefined };
}

/** 由目标信息 + 尺寸指令（"=50%" / "=300x200"，null=清除尺寸）生成新图片语法文本。
 *  普通 title（如 "图1"）保留；尺寸指令 title 随本次设置重写。 */
function buildImageText(target: ResizeTarget, sizeStr: string | null): string {
  const { alt, url, title } = target;
  const hasTextTitle = !!title && !title.startsWith("=");
  const suffix = hasTextTitle ? ` "${title}"` : sizeStr ? ` "${sizeStr}"` : "";
  return `![${alt}](${url}${suffix})`;
}

/**
 * 图片尺寸插件（每编辑器实例一份，闭包捕获各自的弹窗回调）：
 * - actions：工具栏"图片尺寸"按钮，选中图片语法或光标在语法内时打开设置弹窗
 * - editorEffect：CM5 增强 —— 全文扫描图片语法并 markText 高亮；
 *   文档 change 时重扫（改写源码后高亮自动刷新）。
 *   点击高亮段打开弹窗的入口在 EditableEditor 的 React 事件委托（见文件头注释：
 *   codemirror-ssr 不触发 click 事件，不能依赖 editor.on("click")）
 */
function createImageSizePlugin(deps: {
  /** 各编辑器实例的 CM5 editor 引用（effect/handler 写入，弹窗应用时读取） */
  getEditor: () => CmEditor | null;
  /** editorEffect 挂载时写入 CM5 实例、卸载时置空（React ref 的唯一写入点） */
  setEditor: (editor: CmEditor | null) => void;
  /** 命中图片语法时打开尺寸弹窗 */
  onOpen: (target: ResizeTarget) => void;
}): BytemdPlugin {
  /** 探测当前命中（选中文本或光标所在行）的图片语法 */
  const findImage = (editor: CmEditor): ResizeTarget | null => {
    // 1) 优先：选中文本恰为完整图片语法（含尺寸 title）
    const selection = editor.getSelection();
    const selInfo = parseImageSyntax(selection);
    if (selInfo) {
      const from = editor.indexFromPos(editor.getCursor("start"));
      const to = editor.indexFromPos(editor.getCursor("end"));
      return {
        index: Math.min(from, to),
        endIndex: Math.max(from, to),
        text: selection,
        ...selInfo,
      };
    }
    // 2) 兜底：光标所在行内扫描，光标落在语法范围内即命中
    const pos = editor.getCursor();
    const line = editor.getLine(pos.line);
    IMAGE_SYNTAX_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = IMAGE_SYNTAX_RE.exec(line))) {
      const start = m.index;
      const end = start + m[0].length;
      if (pos.ch >= start && pos.ch <= end) {
        const info = parseImageSyntax(m[0]);
        if (info) {
          const lineStart = editor.indexFromPos({ line: pos.line, ch: 0 });
          return { index: lineStart + start, endIndex: lineStart + end, text: m[0], ...info };
        }
      }
    }
    return null;
  };

  return {
    actions: [
      {
        title: "图片尺寸",
        icon: IMG_SIZE_ICON,
        handler: {
          type: "action",
          click: (ctx) => {
            const target = findImage(ctx.editor as unknown as CmEditor);
            if (target) deps.onOpen(target);
            else message.info("请先选中或点入图片语法（形如 ![图片](url)）");
          },
        },
      },
    ],
    editorEffect: (ctx) => {
      const editor = ctx.editor as unknown as CmEditor;
      // 写入实例引用：点击定位（handleEditorMouseUp）与尺寸弹窗应用（replaceRange）都靠它
      deps.setEditor(editor);
      // 当前高亮标记（含原文字符偏移，供点击命中判断）
      let marks: Array<{ index: number; endIndex: number; clear: () => void }> = [];

      /** 全文扫描图片语法 → 逐段高亮（change 后重扫，自动刷新位置）。
       *  markText 失败只影响高亮不影响编辑，try/catch 防御未知边缘情况 */
      const scan = () => {
        try {
          marks.forEach((mm) => mm.clear());
          marks = [];
          const text = editor.getValue();
          IMAGE_SYNTAX_RE.lastIndex = 0;
          let m: RegExpExecArray | null;
          while ((m = IMAGE_SYNTAX_RE.exec(text))) {
            const from = editor.posFromIndex(m.index);
            const to = editor.posFromIndex(m.index + m[0].length);
            marks.push({
              index: m.index,
              endIndex: m.index + m[0].length,
              clear: editor.markText(from, to, { className: "md-img-token" }).clear,
            });
          }
        } catch (e) {
          console.error("图片语法高亮扫描失败:", e);
        }
      };

      scan();
      editor.on("change", scan);
      return () => {
        try {
          editor.off("change", scan);
          marks.forEach((mm) => mm.clear());
        } catch (e) {
          console.error("图片语法高亮清理失败:", e);
        }
        deps.setEditor(null); // 实例随编辑器销毁，ref 置空防止弹窗应用时误用
      };
    },
  };
}

/** 图片尺寸设置弹窗：百分比档位 / 自定义像素宽高 / 原始尺寸，应用时改写编辑器源码 */
function ImageSizeModal({
  target,
  getEditor,
  onClose,
}: {
  target: ResizeTarget | null;
  getEditor: () => CmEditor | null;
  onClose: () => void;
}) {
  // 尺寸模式："25%"~"100%" 百分比档位 | "custom" 自定义像素 | "none" 原始尺寸
  const [mode, setMode] = useState<string>("none");
  const [pixelWidth, setPixelWidth] = useState<number | null>(null);
  const [pixelHeight, setPixelHeight] = useState<number | null>(null);

  // 打开时回显当前尺寸："50%" → 百分比档位；"300px" → 自定义像素；无尺寸 → 原始
  useEffect(() => {
    if (!target) return;
    const w = target.size?.width;
    if (w?.endsWith("%")) {
      setMode(w);
      setPixelWidth(null);
      setPixelHeight(null);
    } else if (w) {
      setMode("custom");
      setPixelWidth(Number(w.replace("px", "")) || null);
      const h = target.size?.height;
      setPixelHeight(h ? Number(h.replace("px", "")) || null : null);
    } else {
      setMode("none");
      setPixelWidth(null);
      setPixelHeight(null);
    }
  }, [target]);

  /** 应用：生成尺寸指令并改写编辑器源码（change 事件触发右栏预览实时更新） */
  const apply = () => {
    const editor = getEditor();
    if (!editor || !target) return;
    let sizeStr: string | null = null;
    if (mode === "custom") {
      // 仅宽度像素 → "=300x"（高自适应）；宽高都有 → "=300x200"
      sizeStr = pixelWidth ? `=${pixelWidth}${pixelHeight ? `x${pixelHeight}` : "x"}` : null;
    } else if (mode !== "none") {
      sizeStr = `=${mode}`;
    }
    editor.replaceRange(
      buildImageText(target, sizeStr),
      editor.posFromIndex(target.index),
      editor.posFromIndex(target.endIndex),
    );
    onClose();
  };

  return (
    <Modal
      open={!!target}
      onCancel={onClose}
      onOk={apply}
      okText="应用"
      cancelText="取消"
      destroyOnClose
      title="调整图片大小"
      width={440}
    >
      {target && (
        <div>
          {/* 图片预览（可点击放大查看原图） */}
          <div style={{ textAlign: "center", marginBottom: 12 }}>
            <Image
              src={target.url}
              alt={target.alt || "图片"}
              style={{ maxWidth: 280, maxHeight: 160, objectFit: "contain" }}
            />
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            宽度
          </Typography.Text>
          <div style={{ margin: "6px 0 12px" }}>
            <Radio.Group
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              {["25%", "50%", "75%", "100%"].map((p) => (
                <Radio.Button key={p} value={p}>
                  {p}
                </Radio.Button>
              ))}
              <Radio.Button value="custom">自定义</Radio.Button>
              <Radio.Button value="none">原始</Radio.Button>
            </Radio.Group>
          </div>
          {mode === "custom" && (
            <Space size={16} wrap style={{ marginBottom: 12 }}>
              <span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  宽度{" "}
                </Typography.Text>
                <InputNumber
                  min={1}
                  max={2000}
                  value={pixelWidth}
                  onChange={(v) => setPixelWidth(v ?? null)}
                  addonAfter="px"
                  style={{ width: 120 }}
                  placeholder="自适应"
                />
              </span>
              <span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  高度{" "}
                </Typography.Text>
                <InputNumber
                  min={1}
                  max={2000}
                  value={pixelHeight}
                  onChange={(v) => setPixelHeight(v ?? null)}
                  addonAfter="px"
                  style={{ width: 120 }}
                  placeholder="自适应"
                />
              </span>
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            保存为图片尺寸语法（![图片](url "=宽x高")），收藏卡片等所有展示页面均按此渲染
          </Typography.Text>
        </div>
      )}
    </Modal>
  );
}

/** 粘贴图片 / 工具栏图片按钮上传：上传后返回可直接用于 markdown 的访问 URL */
async function handleUploadImages(
  files: File[],
): Promise<Array<{ url: string; alt?: string; title?: string }>> {
  try {
    const results = await Promise.all(files.map((f) => assignmentService.uploadEditorImage(f)));
    return results.map((r, i) => ({ url: r.url, alt: files[i]?.name }));
  } catch (e: any) {
    message.error("图片上传失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    throw e; // 抛出让 bytemd 显示上传失败状态
  }
}

/** 可编辑字段（按块区分：question=题干 answer=答案 analysis=解析 context=背景材料） */
type FieldKey = "question" | "answer" | "analysis" | "context";

/** 归一化后的编辑块：一道题（父题/子题/背景材料）的内容编辑单元 */
interface EditBlock {
  /** "parent"（父题/独立题）| "context"（AI 大题背景材料）| "child-<id>"（子题） */
  key: string;
  /** 折叠面板标题（"第 3 题" / "背景材料" / "小题 1"） */
  label: string;
  /** 数据库记录 id（AI 大题背景材料块无 id，为 null） */
  id: number | null;
  /** 题型（AI 题来自 question_type，错题恒为 null；含"选"视为选择题，渲染选项编辑） */
  question_type: string | null;
  /** 选项（仅 AI 选择题有；错题恒为 null） */
  options: Array<{ label: string; text: string }> | null;
  /** 各字段内容（错题 answer=correct_answer、analysis=analysis_detail；AI 题 answer/analysis 同名） */
  fields: Record<FieldKey, string>;
}

/** 是否选择题：题型文本含"选"（单选/多选/选择题），与后端 submit 判定逻辑一致 */
const isChoiceType = (t: string | null | undefined): boolean => !!t && t.includes("选");

/**
 * 保存时选项归一化：label 按位置重排为 A/B/C/D（编辑期 label 固定来自数据库，
 * 用户删除中间选项后可能断号，保存时重排保证连续），并过滤空文本选项。
 */
const buildOptions = (
  opts: Array<{ label: string; text: string }> | null,
): Array<{ label: string; text: string }> =>
  (opts ?? [])
    .map((o, i) => ({ label: String.fromCharCode(65 + i), text: o.text.trim() }))
    .filter((o) => o.text.length > 0);

/** 选择题选项编辑器：每选项一行（字母 + 输入框 + 删除按钮），底部"添加选项"。
 * label 编辑期固定（来自数据库），保存时才由 buildOptions 重排。 */
function OptionsEditor({
  value,
  onChange,
}: {
  value: Array<{ label: string; text: string }>;
  onChange: (v: Array<{ label: string; text: string }>) => void;
}) {
  const update = (i: number, text: string) =>
    onChange(value.map((opt, idx) => (idx === i ? { ...opt, text } : opt)));
  const remove = (i: number) => onChange(value.filter((_, idx) => idx !== i));
  const add = () =>
    onChange([...value, { label: String.fromCharCode(65 + value.length), text: "" }]);
  return (
    <div style={{ marginBottom: 12 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        选项
      </Typography.Text>
      {value.map((opt, i) => (
        <Space.Compact key={i} style={{ display: "flex", marginTop: 6, width: "100%" }}>
          <span style={{ width: 22, lineHeight: "32px", fontWeight: 600, textAlign: "center" }}>
            {opt.label}.
          </span>
          <Input
            value={opt.text}
            onChange={(e) => update(i, e.target.value)}
            placeholder={`选项 ${opt.label} 内容`}
          />
          <Button danger size="small" onClick={() => remove(i)} disabled={value.length <= 1}>
            删除
          </Button>
        </Space.Compact>
      ))}
      <Button size="small" type="dashed" block onClick={add} style={{ marginTop: 6 }}>
        + 添加选项
      </Button>
    </div>
  );
}

/** 把收藏条目（错题/AI 题 × 独立题/大题）归一化为统一编辑块列表 */
function normalizeEntry(entry: FavoriteUnion): EditBlock[] {
  const blocks: EditBlock[] = [];
  if (entry.item_type === "error") {
    const item = entry.question;
    const children: SubQuestionItem[] = item.children ?? [];
    blocks.push({
      key: "parent",
      label: `第 ${item.question_number} 题${item.is_big_question ? "（大题）" : ""}`,
      id: item.id,
      question_type: null, // 错题无选项编辑
      options: null,
      fields: {
        question: item.question_text ?? "",
        answer: item.correct_answer ?? "",
        analysis: item.analysis_detail ?? "",
        context: "",
      },
    });
    // 错题大题：每个子题一个编辑块（父题块负责公共题干）
    if (item.is_big_question) {
      children.forEach((c, i) => {
        blocks.push({
          key: `child-${c.id}`,
          label: `小题 ${i + 1}`,
          id: c.id,
          question_type: null,
          options: null,
          fields: {
            question: c.question_text ?? "",
            answer: c.correct_answer ?? "",
            analysis: c.analysis_detail ?? "",
            context: "",
          },
        });
      });
    }
    return blocks;
  }

  // AI 题
  const item = entry.question;
  const children: AISubQuestionItem[] = item.children ?? [];
  if (item.is_big_question) {
    // AI 大题：背景材料块 + 每个子题一个编辑块
    blocks.push({
      key: "context",
      label: "背景材料",
      id: null,
      question_type: null,
      options: null,
      fields: { question: "", answer: "", analysis: "", context: item.question_context ?? "" },
    });
    children.forEach((c, i) => {
      blocks.push({
        key: `child-${c.id}`,
        label: `小题 ${i + 1}`,
        id: c.id,
        question_type: c.question_type ?? null,
        options: c.options ?? null,
        fields: {
          question: c.question_text ?? "",
          answer: c.answer ?? "",
          analysis: c.analysis ?? "",
          context: "",
        },
      });
    });
  } else {
    // AI 独立题：单块
    blocks.push({
      key: "parent",
      label: item.question_type ? `${item.question_type}题` : "题目",
      id: item.id,
      question_type: item.question_type ?? null,
      options: item.options ?? null,
      fields: {
        question: item.question_text ?? "",
        answer: item.answer ?? "",
        analysis: item.analysis ?? "",
        context: "",
      },
    });
  }
  return blocks;
}

/** bytemd 编辑器封装：定高 + 中文 + 粘贴图片上传 + 图片尺寸调整
 * （编辑器内图片语法高亮，点击或工具栏"图片尺寸"按钮弹出尺寸设置弹窗） */
function EditableEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  // CM5 编辑器实例（插件 effect/handler 写入，尺寸弹窗应用时读取）
  const editorRef = useRef<CmEditor | null>(null);
  // 尺寸弹窗目标：命中图片语法时非空（含原文范围，应用时改写源码）
  const [resizeTarget, setResizeTarget] = useState<ResizeTarget | null>(null);
  // 点击高亮文本时的按下坐标（mouseup 时位移 <6px 判定为点击而非拖选）
  const downPosRef = useRef<{ x: number; y: number } | null>(null);

  /** mousedown：仅在高亮 span 上按下时记录坐标，作为"点击"判定起点 */
  const handleEditorMouseDown = (e: ReactMouseEvent) => {
    downPosRef.current = (e.target as HTMLElement).closest(".md-img-token")
      ? { x: e.clientX, y: e.clientY }
      : null;
  };

  /** mouseup：位移 <6px 且命中高亮 span + 坐标落在图片语法区间 → 打开尺寸弹窗。
   * 用 React 事件委托替代 editor.on("click")：codemirror-ssr 不触发 click 事件（见文件头注释）。
   * 坐标必须显式传 mode="window"：codemirror-ssr 的 coordsChar 默认 mode 是 "local"，
   * 会把视口坐标叠加 sizer 偏移导致命中失败。 */
  const handleEditorMouseUp = (e: ReactMouseEvent) => {
    const down = downPosRef.current;
    downPosRef.current = null;
    if (!down) return;
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return; // 拖选不视为点击
    if (!(e.target as HTMLElement).closest(".md-img-token")) return;
    const editor = editorRef.current;
    if (!editor) return;
    try {
      const pos = editor.coordsChar({ left: e.clientX, top: e.clientY }, "window");
      const index = editor.indexFromPos(pos);
      // 扫描全文，判断点击位置是否落在某段图片语法内（高亮标记与之一一对应）
      const text = editor.getValue();
      IMAGE_SYNTAX_RE.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = IMAGE_SYNTAX_RE.exec(text))) {
        if (index >= m.index && index < m.index + m[0].length) {
          const info = parseImageSyntax(m[0]);
          if (info) {
            setResizeTarget({
              index: m.index,
              endIndex: m.index + m[0].length,
              text: m[0],
              ...info,
            });
          }
          break;
        }
      }
    } catch (err) {
      console.error("点击图片语法定位失败:", err);
    }
  };

  // 图片尺寸插件：引用稳定（useMemo 一次，闭包只依赖 setState 与 ref，均稳定，
  // 不触发 bytemd 的 plugins off/on 重绑）；每实例一份，互不干扰
  const plugins = useMemo<BytemdPlugin[]>(
    () => [
      ...PLUGINS,
      createImageSizePlugin({
        getEditor: () => editorRef.current,
        // 写入 CM5 实例：editorEffect 挂载时调用（useMemo 闭包捕获的 ref 对象稳定，始终读到最新值）
        setEditor: (e) => {
          editorRef.current = e;
        },
        onOpen: (t) => setResizeTarget(t),
      }),
    ],
    [],
  );

  return (
    /* 容器 div 同时是"点击高亮文本"的事件委托点（codemirror-ssr 无 click 事件，见文件头注释） */
    <div
      className="edit-modal-editor"
      onMouseDown={handleEditorMouseDown}
      onMouseUp={handleEditorMouseUp}
    >
      <Editor
        value={value}
        onChange={onChange}
        plugins={plugins}
        mode="tab" // tab 模式默认只显示编辑区（右侧已有独立实时预览，不重复内置预览）
        locale={zh_Hans}
        uploadImages={handleUploadImages}
        placeholder={placeholder}
      />
      <ImageSizeModal
        target={resizeTarget}
        getEditor={() => editorRef.current}
        onClose={() => setResizeTarget(null)}
      />
    </div>
  );
}

export interface QuestionEditModalProps {
  /** 受控开关 */
  open: boolean;
  /** 要编辑的收藏条目（null 时不渲染内容） */
  entry: FavoriteUnion | null;
  onCancel: () => void;
  /** 保存成功回调（收藏页负责关闭 + 刷新各列表缓存） */
  onSaved: () => void;
  /** 编辑队列序号（上传转录后逐题检查：当前第几题，1 起） */
  queueIndex?: number;
  /** 编辑队列总题数（>1 时标题/保存提示显示进度） */
  queueTotal?: number;
}

export default function QuestionEditModal({
  open, entry, onCancel, onSaved, queueIndex, queueTotal,
}: QuestionEditModalProps) {
  // draft：编辑中的内容（key = `${block.key}:${field}`）；未编辑过的字段回退到归一化初值
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // 选项编辑中的内容（key = block.key）；未编辑过的块回退到归一化初值
  const [optionDrafts, setOptionDrafts] = useState<
    Record<string, Array<{ label: string; text: string }>>
  >({});
  const [saving, setSaving] = useState(false);

  // 归一化编辑块（打开弹窗且 entry 变化时重建）
  const blocks = useMemo(() => (open && entry ? normalizeEntry(entry) : []), [open, entry]);

  // 打开新题目时清空 draft，避免上一次编辑内容残留（Modal destroyOnClose 只销毁内部 DOM，state 在外层）
  useEffect(() => {
    if (open) {
      setDrafts({});
      setOptionDrafts({});
    }
  }, [open, entry?.favorite_id]);

  /** 读取选项当前值：优先 draft，未编辑过用归一化初值 */
  const getOptions = (blockKey: string): Array<{ label: string; text: string }> | null => {
    const draft = optionDrafts[blockKey];
    if (draft !== undefined) return draft;
    return blocks.find((b) => b.key === blockKey)?.options ?? null;
  };

  /** 更新某块某字段的编辑内容 */
  const setField = (blockKey: string, field: FieldKey, value: string) => {
    setDrafts((prev) => ({ ...prev, [`${blockKey}:${field}`]: value }));
  };

  /** 读取当前值：优先 draft，未编辑过用归一化初值 */
  const getValue = (blockKey: string, field: FieldKey): string => {
    const draft = drafts[`${blockKey}:${field}`];
    if (draft !== undefined) return draft;
    const block = blocks.find((b) => b.key === blockKey);
    return block?.fields[field] ?? "";
  };

  // 实时预览：draft 变化时重新计算（依赖 drafts/optionDrafts 驱动更新）
  const previewSections = useMemo(
    () =>
      blocks.map((b) => {
        // 背景材料块：单个编辑器内容
        if (b.key === "context") {
          return { label: "背景材料", markdown: getValue(b.key, "context") };
        }
        // 普通块：题干 + 选项 + 答案 + 解析 合并预览（空字段跳过）
        const parts: string[] = [];
        const q = getValue(b.key, "question");
        const opts = getOptions(b.key);
        const a = getValue(b.key, "answer");
        const an = getValue(b.key, "analysis");
        if (q) parts.push(q);
        // 选择题：选项以 "A. 内容" 逐行展示（含编辑中的草稿）
        if (isChoiceType(b.question_type) && opts?.length) {
          parts.push(opts.map((o) => `${o.label}. ${o.text}`).join("\n"));
        }
        if (a) parts.push(`**答案：** ${a}`);
        if (an) parts.push(`**解析：** ${an}`);
        return { label: b.label, markdown: parts.join("\n\n") };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [blocks, drafts, optionDrafts],
  );

  /** 保存：按条目类型分派到对应更新接口（全量覆盖发送） */
  const handleSave = async () => {
    if (!entry) return;
    // 防重复提交：保存中双击会重复写库
    if (saving) return;
    setSaving(true);
    try {
      if (entry.item_type === "error") {
        const item = entry.question;
        const childBlocks = blocks.filter((b) => b.key.startsWith("child-"));
        const payload: {
          question_text?: string;
          correct_answer?: string;
          analysis_detail?: string;
          children?: Array<{ id: number; question_text: string; correct_answer: string; analysis_detail: string }>;
        } = {
          question_text: getValue("parent", "question"),
          correct_answer: getValue("parent", "answer"),
          analysis_detail: getValue("parent", "analysis"),
        };
        // 大题：父题字段 + 全部子题内容
        if (childBlocks.length > 0) {
          payload.children = childBlocks.map((b) => ({
            id: b.id as number,
            question_text: getValue(b.key, "question"),
            correct_answer: getValue(b.key, "answer"),
            analysis_detail: getValue(b.key, "analysis"),
          }));
        }
        await questionService.updateContent(item.id, payload);
      } else {
        const item = entry.question;
        if (!item.is_big_question) {
          // AI 独立题（锚点 id = 自身 id）
          if (item.id == null) throw new Error("题目数据不完整，无法保存");
          const payload: {
            question_text: string;
            answer: string;
            analysis: string;
            options?: Array<{ label: string; text: string }>;
          } = {
            question_text: getValue("parent", "question"),
            answer: getValue("parent", "answer"),
            analysis: getValue("parent", "analysis"),
          };
          // 选择题：附带选项（label 重排 + 过滤空项后全量覆盖）
          if (isChoiceType(item.question_type ?? null)) {
            payload.options = buildOptions(getOptions("parent"));
          }
          await aiQuestionService.updateContent(item.id, payload);
        } else {
          // AI 大题（锚点 id = 组内第一子题 id）
          const anchorId = item.children?.[0]?.id;
          if (!anchorId) throw new Error("题目数据不完整，无法保存");
          const childBlocks = blocks.filter((b) => b.key.startsWith("child-"));
          await aiQuestionService.updateContent(anchorId, {
            question_context: getValue("context", "context"),
            children: childBlocks.map((b) => {
              const child: {
                id: number;
                question_text: string;
                answer: string;
                analysis: string;
                options?: Array<{ label: string; text: string }>;
              } = {
                id: b.id as number,
                question_text: getValue(b.key, "question"),
                answer: getValue(b.key, "answer"),
                analysis: getValue(b.key, "analysis"),
              };
              // 选择题子题：附带选项
              if (isChoiceType(b.question_type)) {
                child.options = buildOptions(getOptions(b.key));
              }
              return child;
            }),
          });
        }
      }
      // 队列逐题检查时（上传转录多题）明确剩余进度，避免"保存后弹出下一题"被误认为重复弹窗
      if (queueTotal && queueTotal > 1) {
        const remaining = queueTotal - (queueIndex ?? 1);
        message.success(
          remaining > 0
            ? `题目内容已保存（${queueIndex}/${queueTotal}），继续检查下一题`
            : `题目内容已保存（${queueIndex}/${queueTotal}），全部检查完成`,
        );
      } else {
        message.success("题目内容已保存");
      }
      onSaved();
    } catch (e: any) {
      message.error("保存失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setSaving(false);
    }
  };

  /** 左栏原题图像：
   *  - 错题：显示切割原图 image_url
   *  - AI 题：优先显示上传转录的原图 image_url（独立题取自身，大题取第一子题，
   *    同一文件的题共用一张原图，用户自行对照），无原图回落 SVG 配图（大题背景材料配图/题目配图），再空态
   */
  const renderOriginalImage = () => {
    if (!entry) return null;
    if (entry.item_type === "error") {
      const img = entry.question.image_url;
      return img ? (
        <Image src={img} alt="原题图像" style={{ width: "100%", borderRadius: 4 }} />
      ) : (
        <Empty description="无原图" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      );
    }
    // AI 题：上传转录的自有试题有原图（预签名 URL 可直接访问）
    const img = entry.question.is_big_question
      ? entry.question.children?.[0]?.image_url
      : entry.question.image_url;
    if (img) {
      return <Image src={img} alt="原题图像" style={{ width: "100%", borderRadius: 4 }} />;
    }
    const svg = entry.question.is_big_question
      ? entry.question.context_image_svg
      : entry.question.image_svg;
    return svg ? (
      <QuestionSvgImage svg={svg} />
    ) : (
      <Empty description="无原图" image={Empty.PRESENTED_IMAGE_SIMPLE} />
    );
  };

  return (
    <Modal
      open={open}
      onCancel={onCancel}
      width="95vw"
      style={{ top: 20 }}
      destroyOnClose
      title={
        queueTotal && queueTotal > 1
          ? `编辑题目（第 ${queueIndex ?? 1} / ${queueTotal} 题）`
          : "编辑题目"
      }
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>
            保存
          </Button>
        </Space>
      }
    >
      <style>{EDITOR_CSS}</style>
      {!entry ? (
        <Spin style={{ display: "block", margin: "40px auto" }} />
      ) : (
        <div style={{ display: "flex", gap: 16, height: "75vh" }}>
          {/* ── 左栏：原题图像 ── */}
          <div
            style={{
              width: 300,
              flexShrink: 0,
              overflow: "auto",
              border: "1px solid #d9d9d9",
              borderRadius: 6,
              padding: 8,
            }}
          >
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              原题图像
            </Typography.Text>
            <div style={{ marginTop: 8 }}>{renderOriginalImage()}</div>
          </div>

          {/* ── 中栏：编辑区（折叠面板，同时只挂载展开的编辑器实例防卡） ── */}
          <div style={{ flex: 1, overflow: "auto" }}>
            <Collapse
              defaultActiveKey={blocks.map((b) => b.key)}
              destroyInactivePanel
              items={blocks.map((b) => ({
                key: b.key,
                label: <Typography.Text strong style={{ fontSize: 13 }}>{b.label}</Typography.Text>,
                children:
                  b.key === "context" ? (
                    // AI 大题背景材料：单个编辑器
                    <EditableEditor
                      value={getValue(b.key, "context")}
                      onChange={(v) => setField(b.key, "context", v)}
                      placeholder="输入背景材料内容，支持 LaTeX 公式与粘贴图片"
                    />
                  ) : (
                    // 普通块：题干（选择题含选项）/ 答案 / 解析 编辑器
                    <div>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        题干
                      </Typography.Text>
                      <EditableEditor
                        value={getValue(b.key, "question")}
                        onChange={(v) => setField(b.key, "question", v)}
                        placeholder="输入题干内容，支持 LaTeX 公式与粘贴图片"
                      />
                      {isChoiceType(b.question_type) && (
                        <OptionsEditor
                          value={getOptions(b.key) ?? []}
                          onChange={(v) =>
                            setOptionDrafts((prev) => ({ ...prev, [b.key]: v }))
                          }
                        />
                      )}
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        答案
                      </Typography.Text>
                      <EditableEditor
                        value={getValue(b.key, "answer")}
                        onChange={(v) => setField(b.key, "answer", v)}
                        placeholder="输入正确答案"
                      />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        解析
                      </Typography.Text>
                      <EditableEditor
                        value={getValue(b.key, "analysis")}
                        onChange={(v) => setField(b.key, "analysis", v)}
                        placeholder="输入解析内容"
                      />
                    </div>
                  ),
              }))}
            />
          </div>

          {/* ── 右栏：实时预览 ── */}
          <div
            style={{
              flex: 1,
              overflow: "auto",
              border: "1px solid #d9d9d9",
              borderRadius: 6,
              padding: 12,
            }}
          >
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              预览
            </Typography.Text>
            <MarkdownPreview sections={previewSections} style={{ marginTop: 8 }} />
          </div>
        </div>
      )}
    </Modal>
  );
}
