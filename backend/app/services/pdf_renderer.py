"""
PDF / HTML 报告渲染服务

先生成结构化 HTML 报告，再通过 Playwright 无头 Chromium 渲染为真正的 PDF 文件；
若 PDF 渲染失败则回退为 HTML（浏览器打开后可 Ctrl+P 手动导出）。

报告类型：
- analysis_report: 作业学情分析报告（单作业/周期汇总）
- correction_workbook: 错题订正本
"""

import asyncio
import logging
import threading
from typing import Optional
from datetime import datetime

from app.utils.latex_convert import to_plain

logger = logging.getLogger(__name__)

# Playwright 同步 API 的对象不是线程安全的，不能跨线程共享全局实例。
# 用 threading.local 按线程缓存浏览器：渲染通过 asyncio.to_thread 走固定线程池，
# 线程会被复用，因此每线程只需启动一次 Chromium（冷启动 1~3 秒），
# 避免了每次渲染 PDF 都 launch/close 的重复开销。
_thread_local = threading.local()


def _get_browser():
    """获取当前线程的 Chromium 浏览器实例（惰性启动，线程内复用）。"""
    browser = getattr(_thread_local, "browser", None)
    if browser is None:
        from playwright.sync_api import sync_playwright
        # Playwright 对象必须持有引用，否则被 GC 后浏览器资源会被回收
        _thread_local.playwright = sync_playwright().start()
        _thread_local.browser = _thread_local.playwright.chromium.launch()
        logger.info("PDF 渲染：Chromium 实例启动（线程 %s）", threading.current_thread().name)
    return _thread_local.browser


def _render_pdf_sync(html: str) -> bytes:
    """在独立线程中用 Playwright 同步 API 渲染 PDF（避免与主事件循环冲突）。"""
    browser = _get_browser()
    page = browser.new_page()
    try:
        page.set_content(html, wait_until="load")
        # page.pdf 默认使用 print 媒体样式：模板中的工具栏会自动隐藏
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "12mm", "right": "12mm"},
        )
    finally:
        page.close()
    return pdf_bytes


async def render_html_to_pdf(html: str) -> bytes | None:
    """
    将报告 HTML 渲染为 PDF 字节流。

    依赖 playwright（需执行过 `playwright install chromium`），
    未安装或渲染异常时返回 None，调用方应回退为保存 HTML。
    """
    try:
        return await asyncio.to_thread(_render_pdf_sync, html)
    except Exception:
        logger.error("HTML 渲染 PDF 失败，将回退为 HTML 文件", exc_info=True)
        return None


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
  /* 知识点双栏布局：条目不跨栏拆分 */
  .kp-grid {{ column-count: 2; column-gap: 6mm; margin: 16px 0; }}
  .kp-item {{ border: 1px solid #e8e8e8; border-radius: 6px; padding: 8px 12px; margin: 0 0 8px; break-inside: avoid; page-break-inside: avoid; }}
  .kp-item h4 {{ margin: 0 0 4px; color: #1677ff; font-size: 14px; word-break: break-word; }}
  .kp-item p {{ margin: 2px 0; font-size: 13px; }}
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

PLAN_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  /* A4 紧凑排版：学习计划以表格为主，固定列宽防止内容挤压错乱 */
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; padding: 20px; color: #333; line-height: 1.6; font-size: 13px; }}
  h1 {{ text-align: center; color: #1677ff; margin: 0 0 4px; font-size: 20px; }}
  .meta {{ text-align: center; color: #999; font-size: 12px; margin-bottom: 14px; }}
  /* 总体目标：浅蓝提示框，与报告模板的 summary-box 风格一致 */
  .goal-box {{ background: #e6f7ff; border: 1px solid #91caff; border-radius: 8px; padding: 12px 14px; margin: 0 0 14px; }}
  .goal-box strong {{ color: #1677ff; }}
  h2 {{ color: #1677ff; border-bottom: 2px solid #1677ff; padding-bottom: 4px; margin: 20px 0 10px; font-size: 16px; }}
  /* 每日计划表格：固定列宽 + 自动换行，长任务文本不撑破版面 */
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin: 0 0 14px; }}
  th, td {{ border: 1px solid #e8e8e8; padding: 8px 10px; text-align: left; vertical-align: top; word-break: break-word; }}
  th {{ background: #f0f5ff; font-weight: 600; text-align: center; }}
  .col-day {{ width: 6%; text-align: center; }}
  .col-theme {{ width: 14%; }}
  .col-kp {{ width: 14%; }}
  .col-task {{ width: 36%; }}
  .col-duration {{ width: 8%; text-align: center; }}
  .col-review {{ width: 22%; }}
  /* 学习建议列表 */
  ul {{ margin: 6px 0 0; padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  .footer {{ text-align: center; color: #999; font-size: 11px; margin-top: 16px; border-top: 1px solid #e8e8e8; padding-top: 10px; }}
  /* 打印样式：表头跨页重复，行不跨页拆分 */
  @media print {{
    thead {{ display: table-header-group; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
    h2 {{ page-break-after: avoid; }}
    @page {{ size: A4; margin: 12mm; }}
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">生成时间：{generated_at} | 学科：{subject} | 计划天数：{plan_days}天</div>

{goal_html}

<h2>一、每日学习计划</h2>
{plan_table}

{tips_html}

<div class="footer">
  AI 助教系统自动生成 | 计划依据真实学情定制，仅供学习参考
</div>
</body>
</html>"""

WORKBOOK_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  /* A4 双栏紧凑排版：正文字号缩小、行距收紧，节省纸张 */
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; padding: 20px; color: #333; line-height: 1.5; font-size: 12px; }}
  h1 {{ text-align: center; color: #1677ff; margin: 0 0 4px; font-size: 20px; }}
  .meta {{ text-align: center; color: #999; font-size: 12px; margin-bottom: 12px; }}
  /* 题目区域双栏布局，题卡不跨栏拆分 */
  .questions-wrap {{ column-count: 2; column-gap: 6mm; column-fill: auto; }}
  .question-card {{ border: 1px solid #e8e8e8; border-radius: 6px; padding: 8px 10px; margin: 0 0 8px; break-inside: avoid; page-break-inside: avoid; }}
  .question-card h3 {{ margin: 0 0 4px; color: #1677ff; font-size: 13px; }}
  .question-card p {{ margin: 4px 0; }}
  /* 我的答案与正确答案同行并排 */
  .answer-row {{ display: flex; gap: 6px; margin-top: 6px; }}
  .answer-row > div {{ flex: 1; min-width: 0; word-break: break-word; }}
  .student-answer {{ background: #fff2f0; padding: 4px 8px; border-radius: 4px; border-left: 3px solid #ff4d4f; }}
  .correct-answer {{ background: #f6ffed; padding: 4px 8px; border-radius: 4px; border-left: 3px solid #52c41a; }}
  .kp-tag {{ display: inline-block; background: #e6f7ff; color: #1677ff; padding: 1px 6px; border-radius: 3px; margin: 1px; font-size: 11px; }}
  .question-image {{ margin: 6px 0; }}
  .question-image img {{ max-width: 100%; height: auto; border: 1px solid #e8e8e8; border-radius: 4px; }}
  .question-source {{ color: #999; font-size: 11px; font-weight: normal; margin-left: 6px; }}
  .footer {{ text-align: center; color: #999; font-size: 11px; margin-top: 16px; border-top: 1px solid #e8e8e8; padding-top: 10px; }}
  /* 顶部工具栏：屏幕可见，打印时自动隐藏 */
  .toolbar {{ position: fixed; top: 0; left: 0; right: 0; z-index: 999; background: #fff; border-bottom: 1px solid #e8e8e8; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .toolbar h3 {{ margin: 0; font-size: 15px; color: #1677ff; }}
  .btn-print {{ background: #1677ff; color: #fff; border: none; padding: 8px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500; }}
  .btn-print:hover {{ background: #4096ff; }}
  body {{ padding-top: 60px; }}
  /* 打印样式 */
  @media print {{
    .toolbar {{ display: none !important; }}
    body {{ padding: 0; }}
    .question-card {{ break-inside: avoid; page-break-inside: avoid; }}
    h2 {{ page-break-after: avoid; }}
    @page {{ size: A4; margin: 12mm; }}
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

<div class="questions-wrap">
{questions_section}
</div>

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
                # 字段名与 get_precise_stats 返回的 assignment_details 保持一致：
                # total_score/total_full/question_types（勿用 score/full_score/type_distribution，
                # dict.get 取不到键会静默回退默认值 0，导致"得分/总分"显示 0.0/0.0）
                assignment_rows.append(
                    f"<tr><td>{detail.get('name', '')}</td>"
                    f"<td>{_format_type_distribution(detail.get('question_types', {}), detail.get('question_count', 0))}</td>"
                    f"<td>{detail.get('total_score', 0):.1f}/{detail.get('total_full', 0):.1f}</td>"
                    f"<td>{rate_str}</td></tr>"
                )
            assignments_table = (
                "<table><tr><th>作业名称</th><th>题量</th><th>得分/总分</th><th>得分率</th></tr>"
                + "".join(assignment_rows)
                + "</table>"
            )
        else:
            assignments_table = "<p>暂无作业明细数据</p>"

        # 知识点分析：双栏卡片布局（较表格更紧凑，减少报告页数）
        if knowledge_points:
            kp_items = []
            for kp in knowledge_points:
                kp_rate_val = kp.get("score_rate", 0) or 0
                kp_rate = f"{kp_rate_val * 100:.1f}%"
                # 得分率按档位着色：优秀绿色、薄弱红色
                rate_class = "success" if kp_rate_val >= 0.8 else ("error" if kp_rate_val < 0.6 else "")
                kp_items.append(
                    f"<div class='kp-item'>"
                    f"<h4>{kp.get('name', '')}</h4>"
                    f"<p>考察频次：{kp.get('frequency', 0)}次 ｜ 得分率："
                    f"<span class='{rate_class}'>{kp_rate}</span></p>"
                    f"</div>"
                )
            kp_table = "<div class='kp-grid'>" + "".join(kp_items) + "</div>"
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
                # 来源与题号同行显示，节省纵向空间
                source_html = (
                    f"<span class='question-source'>来源：{assignment_name}</span>"
                    if assignment_name else ""
                )
                # 答案/错因文本可能含 $...$ 包裹的 LaTeX 公式，PDF 无法渲染，
                # 统一降级为可读纯文本（如 $\frac{1}{2}$ → 1/2）
                student_answer_text = to_plain(q.get("student_answer")) or "未作答"
                correct_answer_text = to_plain(q.get("correct_answer")) or "未知"
                q_sections.append(
                    f"<div class='question-card'>"
                    f"<h3>第{q.get('question_number', '?')}题{source_html}</h3>"
                    f"{image_html}"
                    f"{reason_html}"
                    f"<div class='answer-row'>"
                    f"<div class='student-answer'><strong>❌ 我的答案：</strong>{student_answer_text}</div>"
                    f"<div class='correct-answer'><strong>✅ 正确答案：</strong>{correct_answer_text}</div>"
                    f"</div>"
                    f"<div style='margin-top:4px'>{kp_tags}</div>"
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
    def build_study_plan_html(
        title: str,
        plan_json: dict,
        subject: str = "未指定",
        plan_days: int = 7,
    ) -> str:
        """
        构建专项提升学习计划 HTML

        由 Agent 工具 generate_study_plan 调用：LLM 返回结构化计划 JSON 后，
        直接按结构化数据渲染成规整的表格排版（标题 + 总体目标 + 每日计划表 + 学习建议），
        避免把 Markdown 原文塞进 <pre> 导致 PDF 排版杂乱。

        Args:
            title: 计划标题（来自 LLM 的 plan_json.title）
            plan_json: LLM 返回的结构化计划 {"title", "overall_goal", "daily_plan": [{day, theme, knowledge_points, tasks, duration_minutes, review_items}], "tips"}
            subject: 学科
            plan_days: 计划天数
        """
        import html as html_lib

        # 所有 LLM 生成文本统一转义，防止特殊字符破坏 HTML 结构
        esc = html_lib.escape

        # 总体目标提示框
        goal = plan_json.get("overall_goal", "")
        goal_html = (
            f'<div class="goal-box"><strong>总体目标：</strong>{esc(goal)}</div>'
            if goal
            else ""
        )

        # 每日计划表格：固定 6 列（天/主题/知识点/任务/时长/复习项）
        rows = []
        for day in plan_json.get("daily_plan", []):
            kps = "、".join(esc(kp) for kp in (day.get("knowledge_points", []) or []))
            tasks = "；".join(esc(t) for t in (day.get("tasks", []) or []))
            reviews = "；".join(esc(r) for r in (day.get("review_items", []) or []))
            duration = f"{day.get('duration_minutes', 60)}分钟"
            rows.append(
                f"<tr>"
                f"<td class='col-day'>{esc(str(day.get('day', '')))}</td>"
                f"<td class='col-theme'>{esc(day.get('theme', ''))}</td>"
                f"<td class='col-kp'>{kps}</td>"
                f"<td class='col-task'>{tasks}</td>"
                f"<td class='col-duration'>{duration}</td>"
                f"<td class='col-review'>{reviews}</td>"
                f"</tr>"
            )
        plan_table = (
            "<table><thead><tr>"
            "<th class='col-day'>天</th><th class='col-theme'>主题</th>"
            "<th class='col-kp'>知识点</th><th class='col-task'>任务</th>"
            "<th class='col-duration'>时长</th><th class='col-review'>复习项</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

        # 学习建议列表（可为空）
        tips = plan_json.get("tips", []) or []
        if tips:
            tips_html = (
                "<h2>二、学习建议</h2><ul>"
                + "".join(f"<li>{esc(t)}</li>" for t in tips)
                + "</ul>"
            )
        else:
            tips_html = ""

        return PLAN_TEMPLATE.format(
            title=esc(title),
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            subject=subject,
            plan_days=plan_days,
            goal_html=goal_html,
            plan_table=plan_table,
            tips_html=tips_html,
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
