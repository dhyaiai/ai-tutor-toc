// 此文件中的下拉选项已迁移到 filterConfig.ts，这里保留向后兼容的重新导出。
export {
  LAYOUT_TYPE_LABELS,
  LAYOUT_TYPES,
  GRADE_OPTIONS,
  SUBJECT_OPTIONS,
  SEMESTER_OPTIONS,
  toSelectOptions,
} from "./filterConfig";

export const ASSIGNMENT_STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: "default", label: "等待切割" },
  splitting: { color: "processing", label: "正在切割" },
  splitted: { color: "cyan", label: "切割完成" },
  grading: { color: "processing", label: "正在分析" },
  processing: { color: "processing", label: "分析中" },
  completed: { color: "success", label: "已完成" },
  failed: { color: "error", label: "分析失败" },
};

export const QUESTION_STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: "default", label: "待分析" },
  completed: { color: "success", label: "已完成" },
  failed: { color: "error", label: "失败" },
  confirmed: { color: "blue", label: "已确认" },
};
