"""
PDF / HTML 报告渲染服务

生成结构化 HTML 报告，浏览器打开即可查看，点击"打印/保存为PDF"按钮或 Ctrl+P 即可导出 PDF。

报告类型：
- analysis_report: 作业学情分析报告（单作业/周期汇总）
- correction_workbook: 错题订正本
"""

import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def _extract_kp_display_names(kps) -> list[str]:
    """从 knowledge_points 字段中提取人类可读的知识点名称列表。"""
    if not kps:
        return []
    if isinstance(kps, list):
        names = []
        for item in kps:
            if isinstance(item, dict):
                name = item.get("name")
                if name:
                    names.append(str(name))
            else:
                names.append(str(item))
        return names
    if isinstance(kps, dict):
        name = kps.get("name")
        if name:
            return [str(name)]
    return [str(kps)]


def _format_type_distribution(type_counts: dict[str, int], total: int) -> str:
    """把题型数量映射格式化为 '12题（单选题3题、填空题3题）'。"""
    if not type_counts:
        return f"{total}题"
    parts = [f"{t}{c}题" for t, c in type_counts.items() if c > 0]
    if not parts:
        return f"{total}题"
    return f"{total}题（{'、'.join(parts)}）"

# 报告 HTML 模板（内联，避免外部文件依赖）
# 顶部工具栏在浏览器中可见，打印时自动隐藏
REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; padding: 40px; color: #333; line-height: 1.8; }}
  h1 {{ text-align: center; color: #1677ff; margin-bottom: 8px; }}
  .meta {{ text-align: center; color: #999; font-size: 13px; margin-bottom: 30px; }}
  h2 {{ color: #1677ff; border-bottom: 2px solid #1677ff; padding-bottom: 6px; margin-top: 30px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th, td {{ border: 1px solid #e8e8e8; padding: 10px 12px; text-align: left; }}
  th {{ background: #f0f5ff; font-weight: 600; }}
  .highlight {{ background: #fffbe6; padding: 2px 6px; border-radius: 3px; }}
  .error {{ color: #ff4d4f; }}
  .success {{ color: #52c41a; }}
  .summary-box {{ background: #f6ffed; border: 1px solid #b7eb8f; border-radius: 8px; padding: 16px; margin: 16px 0; }}
  .weak-box {{ background: #fff2f0; border: 1px solid #ffa39e; border-radius: 8px; padding: 16px; margin: 16px 0; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 40px; border-top: 1px solid #e8e8e8; padding-top: 20px; }}
  /* 顶部工具栏：屏幕可见，打印时自动隐藏 */
  .toolbar {{ position: fixed; top: 0; left: 0; right: 0; z-index: 999; background: #fff; border-bottom: 1px solid #e8e8e8; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .toolbar h3 {{ margin: 0; font-size: 15px; color: #1677ff; }}
  .btn-print {{ background: #1677ff; color: #fff; border: none; padding: 8px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500; }}
  .btn-print:hover {{ background: #4096ff; }}
  body {{ padding-top: 70px; }}
  /* 打印样式：隐藏工具栏，优化分页 */
  @media print {{
    .toolbar {{ display: none !important; }}
    body {{ padding-top: 0; }}
    .question-card {{ page-break-inside: avoid; }}
    h2 {{ page-break-after: avoid; }}
    @page {{ margin: 15mm; }}
  }}
</style>
</head>
<body>
<!-- 顶部工具栏：屏幕显示，打印自动隐藏 -->
<div class="toolbar">
  <h3>📄 {title}</h3>
  <button class="btn-print" onclick="window.print()">🖨️ 打印 / 保存为PDF</button>
</div>

<h1>{title}</h1>
<div class="meta">生成时间：{generated_at} | 学科：{subject} | 模式：{mode}</div>

{summary_section}

<h2>一、整体统计</h2>
<table>
  <tr><th>指标</th><th>数值</th></tr>
  <tr><td>作业总数</td><td>{total_assignments}</td></tr>
  <tr><td>题目总数</td><td>{total_questions}</td></tr>
  <tr><td>整体正确率</td><td class="{correct_rate_class}">{correct_rate}%</td></tr>
  <tr><td>错误题数</td><td class="error">{error_count}</td></tr>
</table>

<h2>二、作业明细</h2>
{assignments_table}

<h2>三、知识点分析</h2>
{kp_table}

<h2>四、改进建议</h2>
<ul>
{suggestions}
</ul>

<div class="footer">
  AI 助教系统自动生成 | 报告仅供教学参考
</div>
</body>
</html>"""

WORKBOOK_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; padding: 40px; color: #333; line-height: 1.8; }}
  h1 {{ text-align: center; color: #1677ff; margin-bottom: 8px; }}
  .meta {{ text-align: center; color: #999; font-size: 13px; margin-bottom: 30px; }}
  .question-card {{ border: 1px solid #e8e8e8; border-radius: 8px; padding: 16px; margin: 16px 0; page-break-inside: avoid; }}
  .question-card h3 {{ margin-top: 0; color: #1677ff; }}
  .student-answer {{ background: #fff2f0; padding: 8px 12px; border-radius: 4px; border-left: 3px solid #ff4d4f; }}
  .correct-answer {{ background: #f6ffed; padding: 8px 12px; border-radius: 4px; border-left: 3px solid #52c41a; }}
  .kp-tag {{ display: inline-block; background: #e6f7ff; color: #1677ff; padding: 2px 8px; border-radius: 3px; margin: 2px; font-size: 12px; }}
  .question-image {{ margin: 10px 0; }}
  .question-image img {{ max-width: 100%; height: auto; border: 1px solid #e8e8e8; border-radius: 4px; }}
  .question-source {{ color: #999; font-size: 13px; margin-top: -8px; margin-bottom: 8px; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 40px; border-top: 1px solid #e8e8e8; padding-top: 20px; }}
  /* 顶部工具栏：屏幕可见，打印时自动隐藏 */
  .toolbar {{ position: fixed; top: 0; left: 0; right: 0; z-index: 999; background: #fff; border-bottom: 1px solid #e8e8e8; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .toolbar h3 {{ margin: 0; font-size: 15px; color: #1677ff; }}
  .btn-print {{ background: #1677ff; color: #fff; border: none; padding: 8px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500; }}
  .btn-print:hover {{ background: #4096ff; }}
  body {{ padding-top: 70px; }}
  /* 打印样式 */
  @media print {{
    .toolbar {{ display: none !important; }}
    body {{ padding-top: 0; }}
    .question-card {{ page-break-inside: avoid; }}
    h2 {{ page-break-after: avoid; }}
    @page {{ margin: 15mm; }}
  }}
</style>
</head>
<body>
<!-- 顶部工具栏：屏幕显示，打印自动隐藏 -->
<div class="toolbar">
  <h3>📝 {title}</h3>
  <button class="btn-print" onclick="window.print()">🖨️ 打印 / 保存为PDF</button>
</div>

<h1>{title}</h1>
<div class="meta">生成时间：{generated_at} | 包含 {question_count} 道错题</div>

{questions_section}

<div class="footer">
  AI 助教系统自动生成 | 订正本仅供教学参考
</div>
</body>
</html>"""


class PdfRenderer:
    """
    报告渲染器

    使用方式：
        renderer = PdfRenderer()
        html = renderer.build_analysis_report(data)
        # 保存为 .html 文件，浏览器打开即可查看并通过工具栏按钮打印为 PDF
    """

    @staticmethod
    def build_analysis_report(
        subject: str = "未指定",
        mode: str = "single",
        total_assignments: int = 0,
        total_questions: int = 0,
        correct_rate: float = 0.0,
        error_count: int = 0,
        knowledge_points: Optional[list[dict]] = None,
        assignment_details: Optional[list[dict]] = None,
        suggestions: Optional[list[str]] = None,
    ) -> str:
        """
        构建作业分析报告 HTML

        Args:
            subject: 学科
            mode: single=单作业 / summary=周期汇总
            total_assignments: 作业总数
            total_questions: 题目总数
            correct_rate: 正确率（0-1）
            error_count: 错题数
            knowledge_points: 知识点分析 [{name, score_rate, frequency}]
            assignment_details: 作业明细 [{name, question_count, type_distribution, score, full_score, score_rate}]
            suggestions: 改进建议列表
        """
        # 正确率格式化
        rate_pct = f"{correct_rate * 100:.1f}"
        rate_class = "success" if correct_rate >= 0.8 else ("error" if correct_rate < 0.6 else "")

        # 作业明细表格
        if assignment_details:
            assignment_rows = []
            for detail in assignment_details:
                rate = detail.get("score_rate", 0) or 0
                rate_str = f"{rate * 100:.1f}%"
                assignment_rows.append(
                    f"<tr><td>{detail.get('name', '')}</td>"
                    f"<td>{_format_type_distribution(detail.get('type_distribution', {}), detail.get('question_count', 0))}</td>"
                    f"<td>{detail.get('score', 0):.1f}/{detail.get('full_score', 0):.1f}</td>"
                    f"<td>{rate_str}</td></tr>"
                )
            assignments_table = (
                "<table><tr><th>作业名称</th><th>题量</th><th>得分/总分</th><th>得分率</th></tr>"
                + "".join(assignment_rows)
                + "</table>"
            )
        else:
            assignments_table = "<p>暂无作业明细数据</p>"

        # 知识点表格
        if knowledge_points:
            kp_rows = []
            for kp in knowledge_points:
                kp_rate = f"{kp.get('score_rate', 0) * 100:.1f}%"
                kp_rows.append(
                    f"<tr><td>{kp.get('name', '')}</td>"
                    f"<td>{kp.get('frequency', 0)}次</td>"
                    f"<td>{kp_rate}</td></tr>"
                )
            kp_table = "<table><tr><th>知识点</th><th>考察频次</th><th>得分率</th></tr>" + "".join(kp_rows) + "</table>"
        else:
            kp_table = "<p>暂无知识点分析数据</p>"

        # 改进建议
        if suggestions:
            sug_list = "".join(f"<li>{s}</li>" for s in suggestions)
        else:
            sug_list = "<li>继续保持当前学习节奏</li><li>关注薄弱知识点的针对性训练</li>"

        # 摘要区域
        if correct_rate >= 0.85:
            summary_section = '<div class="summary-box">📊 整体表现优秀！知识点掌握扎实，建议适当拓展拔高练习。</div>'
        elif correct_rate >= 0.6:
            summary_section = '<div class="summary-box">📊 整体表现良好，部分知识点需要加强，建议针对性练习。</div>'
        else:
            summary_section = '<div class="weak-box">⚠️ 需要重点关注！存在较多薄弱知识点，建议从基础概念开始系统复习。</div>'

        return REPORT_TEMPLATE.format(
            title=f"作业学情分析报告 - {subject}",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            subject=subject,
            mode="单作业" if mode == "single" else "周期汇总",
            summary_section=summary_section,
            total_assignments=total_assignments,
            total_questions=total_questions,
            correct_rate=rate_pct,
            correct_rate_class=rate_class,
            error_count=error_count,
            assignments_table=assignments_table,
            kp_table=kp_table,
            suggestions=sug_list,
        )

    @staticmethod
    def build_correction_workbook(
        subject: str = "未指定",
        question_count: int = 0,
        questions: Optional[list[dict]] = None,
    ) -> str:
        """
        构建错题订正本 HTML

        Args:
            subject: 学科
            question_count: 错题数量
            questions: 错题列表 [{question_number, assignment_name, image_url, student_answer, correct_answer, knowledge_points, wrong_reason}]
        """
        if questions:
            q_sections = []
            for q in questions:
                kp_names = _extract_kp_display_names(q.get("knowledge_points"))
                kp_tags = " ".join(f'<span class="kp-tag">{kp}</span>' for kp in kp_names)
                wrong_reason = q.get("wrong_reason", "")
                reason_html = f"<p><strong>错因分析：</strong>{wrong_reason}</p>" if wrong_reason else ""

                image_url = q.get('image_url')
                image_html = (
                    f"<div class='question-image'><img src='{image_url}' alt='第{q.get('question_number', '?')}题'></div>"
                    if image_url else ""
                )
                assignment_name = q.get('assignment_name', '')
                source_html = (
                    f"<div class='question-source'>来源：{assignment_name}</div>"
                    if assignment_name else ""
                )
                q_sections.append(
                    f"<div class='question-card'>"
                    f"<h3>第{q.get('question_number', '?')}题</h3>"
                    f"{source_html}"
                    f"{image_html}"
                    f"{reason_html}"
                    f"<div class='student-answer'><strong>❌ 我的答案：</strong>{q.get('student_answer', '未作答')}</div>"
                    f"<div class='correct-answer'><strong>✅ 正确答案：</strong>{q.get('correct_answer', '未知')}</div>"
                    f"<div style='margin-top:8px'>{kp_tags}</div>"
                    f"</div>"
                )
            questions_section = "".join(q_sections)
        else:
            questions_section = "<p>暂无错题记录</p>"

        return WORKBOOK_TEMPLATE.format(
            title=f"错题订正本 - {subject}",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            question_count=question_count,
            questions_section=questions_section,
        )

    @staticmethod
    def build_report_data_from_analytics(
        homework_stats: dict,
        dashboard_items: list[dict],
        heatmap_items: list[dict],
        total_assignments: int | None = None,
        total_questions: int | None = None,
        correct_rate: float | None = None,
        error_count: int | None = None,
        assignment_details: Optional[list[dict]] = None,
        suggestions: Optional[list[str]] = None,
    ) -> dict:
        """
        从学情聚合数据构建报告渲染数据

        将 analytics_aggregator 的三个输出整合为报告渲染所需的统一格式。
        如果传入 total_assignments / total_questions / correct_rate / error_count / suggestions，
        则优先使用这些精确统计值；否则按旧逻辑估算（知识点频次求和等）。
        """
        # 计算整体正确率
        total_rate = 0.0
        if dashboard_items:
            total_rate = sum(d.get("score_rate", 0) for d in dashboard_items) / len(dashboard_items)

        # 知识点数据
        kp_data = [
            {
                "name": item.get("knowledge_point", ""),
                "frequency": item.get("frequency", 0),
                "score_rate": item.get("score_rate", 0),
            }
            for item in (heatmap_items or [])
        ]

        # 薄弱知识点
        weak_kps = [item for item in kp_data if item["score_rate"] < 0.6]

        # 生成建议（仅当调用方未传入建议时使用默认规则）
        if suggestions is None:
            suggestions = []
            if weak_kps:
                suggestions.append(
                    f"重点复习以下薄弱知识点：{'、'.join(kp['name'] for kp in weak_kps[:5])}"
                )

            effective_rate = correct_rate if correct_rate is not None else total_rate
            if effective_rate < 0.6:
                suggestions.append("建议从基础概念开始系统复习，打牢基础后再进行拔高练习。")
            elif effective_rate < 0.8:
                suggestions.append("基础较好，建议增加综合应用题和变式题的训练量。")
            else:
                suggestions.append("掌握扎实，建议尝试更高难度的拓展题目，进一步提升综合能力。")

        return {
            "total_assignments": total_assignments if total_assignments is not None else homework_stats.get("total", 0),
            "total_questions": total_questions if total_questions is not None else sum(item.get("frequency", 0) for item in kp_data),
            "correct_rate": correct_rate if correct_rate is not None else total_rate,
            "error_count": error_count if error_count is not None else sum(1 for item in kp_data if item["score_rate"] < 0.6),
            "knowledge_points": kp_data,
            "suggestions": suggestions,
            "assignment_details": assignment_details or [],
        }
