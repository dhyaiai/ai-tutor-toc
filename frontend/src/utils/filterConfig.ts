/**
 * 全站下拉筛选项统一配置。
 * 所有页面引用的年级、科目、学期、月份等筛选选项均从此文件读取，
 * 新增或修改选项只需改这一处即可全局生效。
 */

/** 年级列表 */
export const GRADE_OPTIONS = [
  "高三", "高二", "高一", "初三", "初二", "初一",
  "六年级", "五年级", "四年级", "三年级", "二年级", "一年级",
];

/** 科目列表 */
export const SUBJECT_OPTIONS = [
  "语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理",
];

/**
 * 支持 AI 讲解题目（含 TTS 语音播报）的科目白名单。
 * 数学/物理/化学含大量纯文本公式（√2、x²、H₂SO₄ 等），TTS 无法正确朗读，暂不开放。
 */
export const AI_EXPLAIN_SUBJECTS = [
  "语文", "英语", "生物", "政治", "历史", "地理",
];

/** 学期列表（可按需扩展） */
export const SEMESTER_OPTIONS = [
  "上学期", "下学期",
];

/** 月份列表（常用月份格式） */
export const MONTH_OPTIONS = Array.from({ length: 12 }, (_, i) => {
  const year = 2026;
  const month = String(i + 1).padStart(2, "0");
  return `${year}-${month}`;
});

/** 排版样式 */
export const LAYOUT_TYPE_LABELS: Record<string, string> = {
  a4_single: "A4 单栏",
  a4_double: "A4 双栏",
  a3_double: "A3 双栏",
  a3_triple: "A3 三栏",
  a3_quad: "A3 四栏",
};

export const LAYOUT_TYPES = [
  { value: "a4_single", label: "A4 单栏" },
  { value: "a4_double", label: "A4 双栏" },
  { value: "a3_double", label: "A3 双栏" },
  { value: "a3_triple", label: "A3 三栏" },
  { value: "a3_quad", label: "A3 四栏" },
];

/** 题型列表 */
export const QUESTION_TYPE_OPTIONS = [
  "单选题", "多选题", "选择题组", "填空题", "计算题", "应用题", "证明题",
  "简答题", "判断题", "阅读理解", "完形填空", "写作题", "作图题",
];

/** 批量生成下拉选项的辅助函数 */
export function toSelectOptions(arr: string[]): { value: string; label: string }[] {
  return arr.map((item) => ({ value: item, label: item }));
}
