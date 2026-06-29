/**
 * Excel 导出工具函数。
 * 使用 exceljs 库将表格数据导出为 .xlsx 文件，支持单元格样式（背景色、字体色等）。
 */

import ExcelJS from "exceljs";

/** 导出列定义 */
export interface ExportColumn {
  /** 数据字段名（对应数据行的 key） */
  key: string;
  /** 表头显示标题 */
  title: string;
}

/**
 * 单元格样式配置。
 * fill.fgColor 使用 6 位十六进制 RGB（如 "FF8080"，不含 # 前缀）。
 * exceljs 内部会自动添加 FF alpha 前缀为 ARGB 格式。
 */
export interface CellStyle {
  /** 填充（背景色），fgColor 为 6 位 RGB 十六进制字符串 */
  fill?: {
    fgColor?: { rgb: string };
    patternType?: string;
  };
  /** 字体 */
  font?: {
    color?: { rgb: string };
    bold?: boolean;
  };
  /** 对齐方式 */
  alignment?: {
    horizontal?: "left" | "center" | "right";
    vertical?: "top" | "middle" | "bottom";
  };
}

/** rgb() 颜色字符串转 6 位十六进制，如 "rgb(255,128,128)" → "FF8080" */
function rgbToHex(rgb: string): string {
  const match = rgb.match(/[\d.]+/g);
  if (!match || match.length < 3) return rgb;
  return match
    .slice(0, 3)
    .map((n) => parseInt(n).toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
}

/**
 * 将数据导出为 Excel 文件并触发浏览器下载（支持单元格样式）。
 *
 * @param columns  - 列定义（key 对应数据字段，title 为表头文字）
 * @param data     - 数据行数组
 * @param filename - 下载文件名（不含扩展名）
 * @param options  - 可选配置
 * @param options.cellStyles - 按列 key 返回单元格样式
 *
 * 使用示例：
 * ```ts
 * exportToExcel(
 *   [{ key: "name", title: "名称" }, { key: "rate", title: "得分率" }],
 *   data,
 *   "导出文件",
 *   {
 *     cellStyles: {
 *       rate: (val, row) => ({
 *         fill: { fgColor: { rgb: "FF8080" }, patternType: "solid" },
 *         font: { color: { rgb: "FFFFFF" }, bold: true },
 *       }),
 *     },
 *   },
 * );
 * ```
 */
export async function exportToExcel(
  columns: ExportColumn[],
  data: Record<string, unknown>[],
  filename: string,
  options?: {
    cellStyles?: Record<
      string,
      (
        value: unknown,
        row: Record<string, unknown>,
        colIndex: number,
        rowIndex: number,
      ) => CellStyle
    >;
  },
): Promise<void> {
  const workbook = new ExcelJS.Workbook();
  const worksheet = workbook.addWorksheet("Sheet1");

  // 写入表头
  const headerRow = worksheet.addRow(columns.map((c) => c.title));
  headerRow.eachCell((cell) => {
    cell.font = { bold: true, size: 12 };
    cell.alignment = { horizontal: "center", vertical: "middle" };
    // 表头浅灰背景
    cell.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FFF0F0F0" },
    };
    cell.border = {
      top: { style: "thin" },
      left: { style: "thin" },
      bottom: { style: "thin" },
      right: { style: "thin" },
    };
  });

  // 写入数据行
  for (let rowIdx = 0; rowIdx < data.length; rowIdx++) {
    const rowData = data[rowIdx];
    const rowValues = columns.map((c) => {
      const val = rowData[c.key];
      return val != null ? val : "";
    });
    const excelRow = worksheet.addRow(rowValues);

    // 应用单元格样式
    for (let colIdx = 0; colIdx < columns.length; colIdx++) {
      const cell = excelRow.getCell(colIdx + 1); // exceljs 列索引从 1 开始
      cell.alignment = { vertical: "middle" };

      // 数据行边框
      cell.border = {
        top: { style: "thin" },
        left: { style: "thin" },
        bottom: { style: "thin" },
        right: { style: "thin" },
      };

      // 如果有对应列的样式回调，应用样式
      if (options?.cellStyles) {
        const colKey = columns[colIdx].key;
        const styleFn = options.cellStyles[colKey];
        if (styleFn) {
          const value = rowValues[colIdx];
          const style = styleFn(value, rowData, colIdx, rowIdx);
          if (style) {
            // 背景色
            if (style.fill?.fgColor?.rgb) {
              const hex = rgbToHex(style.fill.fgColor.rgb);
              cell.fill = {
                type: "pattern",
                pattern: "solid",
                fgColor: { argb: `FF${hex}` },
              };
            }
            // 字体
            if (style.font) {
              cell.font = {
                bold: style.font.bold ?? false,
              };
              if (style.font.color?.rgb) {
                const fontHex = rgbToHex(style.font.color.rgb);
                cell.font.color = { argb: `FF${fontHex}` };
              }
            }
            // 对齐
            if (style.alignment) {
              cell.alignment = {
                horizontal: style.alignment.horizontal,
                vertical: style.alignment.vertical ?? "middle",
              };
            }
          }
        }
      }
    }
  }

  // 自动列宽（取表头和数据最大宽度）
  columns.forEach((col, colIdx) => {
    const headerLen = col.title.length;
    let maxDataLen = 0;
    data.forEach((row) => {
      const val = String(row[col.key] ?? "");
      // 中文字符按 2 个字符宽度计算
      const len = [...val].reduce((acc, ch) => acc + (ch.charCodeAt(0) > 127 ? 2 : 1), 0);
      if (len > maxDataLen) maxDataLen = len;
    });
    const width = Math.max(headerLen * 2, maxDataLen + 4);
    worksheet.getColumn(colIdx + 1).width = Math.min(width, 40);
  });

  // 生成 buffer 并触发下载
  const buffer = await workbook.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filename}.xlsx`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
