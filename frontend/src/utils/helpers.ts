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
