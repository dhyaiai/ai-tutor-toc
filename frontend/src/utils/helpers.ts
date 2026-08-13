export function formatDate(dateStr: string, dateOnly?: boolean): string {
  if (!dateStr) return "-";
  // 服务端存储本地时间，直接解析
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return "-";
  if (dateOnly) {
    return d.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function getScoreRate(score: number | null, fullScore: number | null, preCalculated?: number): string {
  if (preCalculated != null) return `${(preCalculated * 100).toFixed(1)}%`;
  if (score == null || fullScore == null || fullScore === 0) return "-";
  return `${((score / fullScore) * 100).toFixed(1)}%`;
}

export function getFileExtension(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() || "";
}

/**
 * 解析口语测评的 score 字段（格式："4.5/4.5" 或 "4.5/6"）
 * @returns [分子, 分母]，解析失败返回 [null, null]
 */
export function parseOralScore(score: string | null): [number | null, number | null] {
  if (!score) return [null, null];
  const parts = score.split("/");
  if (parts.length === 2) {
    const numerator = parseFloat(parts[0]);
    const denominator = parseFloat(parts[1]);
    if (!isNaN(numerator) && !isNaN(denominator)) {
      return [numerator, denominator];
    }
  }
  // 如果格式不是 "x/y"，尝试直接解析为数字
  const num = parseFloat(score);
  if (!isNaN(num)) {
    return [num, null];
  }
  return [null, null];
}

/** "答案见解析"类占位答案短语：命中时答案本体无信息量，需隐藏并改用解析兜底 */
const ANSWER_PLACEHOLDER_PHRASES = ["答案见解析", "详见解析", "见解析", "答案详见解析", "解析见答案"];

/**
 * 判断答案文本是否为"答案见解析"类占位内容。
 * 去首尾空白 + 去掉末尾单个标点后与占位短语做全等匹配，
 * 避免误伤正常作答（如"证明见解析"这类含"见解析"字样但非占位符的文本）。
 */
export function isPlaceholderAnswer(text: string | null | undefined): boolean {
  if (!text) return false;
  const t = text.trim().replace(/[。.！!？?；;、，,:：]$/, "");
  return ANSWER_PLACEHOLDER_PHRASES.includes(t);
}
