"""
Agent 工具函数定义。

工具通过 @tool 装饰器声明：OpenAI function-calling 格式的 schema 与实现
方法绑定在一起，模块加载时自动收集注册。新增工具只需在 AgentTools 中
添加带 @tool 装饰器的方法，无需手工维护 schema 列表和 if/elif 分发。

- TOOL_DEFINITIONS: 自动收集的 schema 列表，传给 chat.completions.create(tools=...)
- TOOL_REGISTRY: 工具名 -> 实现函数 的注册表，供 AgentTools.execute 自动分发

现有工具，底层复用 analytics_aggregator 和 knowledge_tracker：
- get_assignment_score: 按科目/年级/月份获取作业数、题目数、总得分、得分率统计
- get_error_knowledge: 按科目/年级/月份查询错题知识点分布
- get_score_trend: 按科目/年级/月份获取每份作业的得分率趋势
- update_knowledge_state: 更新用户知识点掌握状态（跨会话持久化）
- query_knowledge_state: 查询用户知识点掌握状态

月份过滤说明：get_assignment_score / get_error_knowledge / get_score_trend
三个查询工具都支持 time_range 参数（自由文本，如'2026年4月'/'4月'），
由 _parse_usage_months 解析为作业 usage_month 的候选值后下钻聚合器。
"""

import asyncio
import inspect
import json
import logging
import re
from datetime import datetime
from typing import Callable, Optional

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.services.llm_json import request_llm_json

logger = logging.getLogger(__name__)

# 附加在被装饰函数上的 schema 属性名
_TOOL_ATTR = "__agent_tool__"


# 按工具名覆盖的执行超时（秒）。
# 生成类工具耗时长（嵌套 LLM 调用 + playwright 渲染），查询类工具走默认 TOOL_EXEC_TIMEOUT。
# 取值依据：报告链路 ≈ 5 个 SQL + 建议 LLM(60s) + playwright 渲染(20~40s) ≈ 100~150s；
# 学习计划链路 ≈ 4 个数据查询 + 1 次 LLM 生成 4000 tokens（实测 ~140s，qwen 长文本生成慢）+ PDF 渲染，
# 120s 实测偏紧（曾逼近超时上限），放宽到 180s，仍低于前端 4 分钟空闲超时。
TOOL_TIMEOUTS: dict[str, float] = {
    "generate_analysis_report": 180,
    "generate_correction_workbook": 120,
    "generate_study_plan": 180,
    "correct_composition": 180,
    "explain_exercise": 180,
}


class ToolExecutionError(Exception):
    """工具执行异常，包含工具名和原始错误"""
    
    def __init__(self, tool_name: str, message: str, original_error: Exception | None = None):
        self.tool_name = tool_name
        self.original_error = original_error
        super().__init__(f"Tool '{tool_name}' execution failed: {message}")


def _truncate_tool_result(text: str, max_chars: Optional[int] = None) -> str:
    """
    截断工具结果，控制塞回 LLM messages 的上下文体积。

    热力图/掌握度等工具返回的 JSON 可能很大，全量塞回会让每轮 LLM 调用
    越来越慢（上下文膨胀是对话越聊越慢的主要原因之一）。
    保留头部 + 追加截断标记，让 LLM 感知到数据不完整。
    """
    limit = max_chars or get_settings().TOOL_RESULT_MAX_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(结果过长已截断)"


def _build_plan_markdown(plan_json: dict, plan_days: int) -> str:
    """
    把 LLM 返回的结构化计划 JSON 组装成对话内展示的 Markdown。

    结构：标题 + 总体目标 + 每日计划表格（天/主题/知识点/任务/时长/复习项）+ 学习建议。
    该文本会同时用于对话内展示和下载文件内容。
    """
    title = plan_json.get("title", f"{plan_days}天学习计划")
    goal = plan_json.get("overall_goal", "")
    lines = [f"## {title}", ""]
    if goal:
        lines += [f"**总体目标：** {goal}", ""]

    lines.append("| 天 | 主题 | 知识点 | 任务 | 时长 | 复习项 |")
    lines.append("|---|---|---|---|---|---|")
    for day in plan_json.get("daily_plan", []):
        # 注意：不能用 str.format 拼接——LLM 生成的任务文本里可能含字面 { }（集合记号、
        # 数学公式等），.format 会把它当占位符解析并抛 KeyError/ValueError，导致整个
        # 计划生成工具崩溃。用 f-string 拼接值部分，避免模板再解析花括号。
        kps = "、".join(day.get("knowledge_points", []) or [])
        tasks = "；".join(day.get("tasks", []) or [])
        reviews = "；".join(day.get("review_items", []) or [])
        duration = f"{day.get('duration_minutes', 60)}分钟"
        lines.append(f"| {day.get('day', '')} | {day.get('theme', '')} | {kps} | {tasks} | {duration} | {reviews} |")
    lines.append("")

    tips = plan_json.get("tips", [])
    if tips:
        lines += ["## 学习建议", ""] + [f"- {t}" for t in tips] + [""]

    return "\n".join(lines)


def _parse_usage_months(time_range: Optional[str]) -> Optional[list[str]]:
    """把自由文本时间范围解析为作业 usage_month 的候选值列表。

    支持 '2026年4月' / '26年4月份' / '2026-04' / '2026/4' / '4月' 等写法，
    返回兼容历史数据的多种存储格式（如 '2026-04'、'2026-4'、'04'、'4'）；
    未写年份时默认当前年；无法解析出月份时（如'最近30天'）返回 None，
    表示不按月份过滤。
    """
    if not time_range:
        return None
    text = time_range.strip()
    year: Optional[int] = None
    month: Optional[int] = None
    m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{2})\s*年\s*(\d{1,2})", text)
        if m:
            year, month = 2000 + int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d{1,2})\s*月", text)
            if m:
                month = int(m.group(1))
    if not month or not 1 <= month <= 12:
        return None
    if year is None:
        # 未写年份默认当前年；跨年边界修正：当前是 1 月却问"12月"时，
        # 用户指的是去年 12 月（今年 12 月还没发生），否则会查出空数据
        today = datetime.now()
        year = today.year
        if month > today.month:
            year -= 1
    candidates = [f"{year}-{month:02d}", f"{year}-{month}", f"{month:02d}", str(month)]
    return list(dict.fromkeys(candidates))


def tool(
    *,
    description: str,
    parameters: Optional[dict] = None,
    name: Optional[str] = None,
) -> Callable:
    """
    Agent 工具装饰器。

    将 OpenAI function-calling 格式的 schema 附加到被装饰的方法上，
    模块加载时由 _collect_tools 收集进 TOOL_REGISTRY / TOOL_DEFINITIONS。

    Args:
        description: 工具功能描述，提供给 LLM 做工具选择
        parameters: JSON Schema 参数定义，缺省表示无参数
        name: 工具名，缺省使用函数名

    用法：
        @tool(description="...", parameters={...})
        async def my_tool(self, arg1: str, arg2: int = 3) -> dict: ...
    """

    def decorator(func: Callable) -> Callable:
        setattr(func, _TOOL_ATTR, {
            "name": name or func.__name__,
            "description": description,
            "parameters": parameters
            or {"type": "object", "properties": {}, "required": []},
        })
        return func

    return decorator


class AgentTools:
    """
    Agent 工具执行器。

    使用方式：
        tools = AgentTools(db_session, user_id)
        result = await tools.execute("query_knowledge_state", {"query_type": "掌握度汇总"})
    """

    def __init__(self, db, user_id: int):
        self.db = db
        self.user_id = user_id

    def _new_session(self):
        """创建新的数据库会话（用于并发查询避免 connection is busy）"""
        return async_session_factory()

    async def execute(self, tool_name: str, arguments: dict) -> str:
        """执行工具调用，返回 JSON 字符串（带执行超时与结果截断）"""
        logger.info("Agent tool called: %s args=%s", tool_name, arguments)

        func = TOOL_REGISTRY.get(tool_name)
        if func is None:
            # 未知工具返回标准错误格式（不抛异常，避免破坏 messages 序列）
            return json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

        # 按工具名取执行超时：生成类工具耗时长，查询类走默认值
        timeout = TOOL_TIMEOUTS.get(tool_name, get_settings().TOOL_EXEC_TIMEOUT)
        try:
            # 过滤掉 LLM 可能传入的未声明参数，避免 TypeError
            sig = inspect.signature(func)
            kwargs = {k: v for k, v in (arguments or {}).items() if k in sig.parameters}
            result = await asyncio.wait_for(func(self, **kwargs), timeout=timeout)
            # default=str 兜底：工具返回的 datetime 等非 JSON 原生类型
            # 转字符串，避免整个工具调用因序列化失败而返回 error
            return _truncate_tool_result(json.dumps(result, ensure_ascii=False, default=str))

        except asyncio.TimeoutError:
            # 超时返回标准错误格式（OpenAI API 要求 assistant tool_calls 后必须紧跟同 id 的 tool 消息）
            logger.error("Tool execution timeout: %s > %ss", tool_name, timeout)
            return json.dumps(
                {"error": f"工具 {tool_name} 执行超时({timeout}s)"}, ensure_ascii=False
            )
        except Exception as e:
            # 实际执行错误：抛出自定义异常供上层捕获处理
            logger.error("Tool execution failed: %s, error=%s", tool_name, e, exc_info=True)
            raise ToolExecutionError(tool_name, str(e), e)

    @tool(
        description="获取作业成绩统计数据：按科目返回作业数、题目数、总得分、得分率。可按科目、年级、月份筛选。",
        parameters={
            "type": "object",
            "properties": {
                "grade": {"type": "string", "description": "年级过滤"},
                "subject": {"type": "string", "description": "科目过滤，如'数学'"},
                "semester": {"type": "string", "description": "学期过滤"},
                "time_range": {
                    "type": "string",
                    "description": "时间范围（可选），按作业使用月份筛选，如'2026年4月'/'4月'/'2026-04'",
                },
            },
            "required": [],
        },
    )
    async def get_assignment_score(
        self,
        grade: Optional[str] = None,
        subject: Optional[str] = None,
        semester: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> dict:
        """学情概览（作业统计）——使用独立会话避免并发冲突"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        async with self._new_session() as session:
            aggregator = AnalyticsAggregator(session)
            return await aggregator.get_homework_stats(
                user_id=self.user_id,
                grade=grade,
                subject=subject,
                semester=semester,
                usage_months=_parse_usage_months(time_range),
            )

    @tool(
        description="查询错题的知识点分布，返回高频错误知识点及错误率。可按科目、年级、月份筛选。",
        parameters={
            "type": "object",
            "properties": {
                "grade": {"type": "string", "description": "年级过滤"},
                "subject": {"type": "string", "description": "科目过滤，如'数学'"},
                "semester": {"type": "string", "description": "学期过滤"},
                "time_range": {
                    "type": "string",
                    "description": "时间范围（可选），按作业使用月份筛选，如'2026年4月'/'4月'/'2026-04'",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回 Top N",
                    "default": 10,
                },
            },
            "required": [],
        },
    )
    async def get_error_knowledge(
        self,
        grade: Optional[str] = None,
        subject: Optional[str] = None,
        semester: Optional[str] = None,
        time_range: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """错题知识点（热力图数据）——使用独立会话避免并发冲突"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        async with self._new_session() as session:
            aggregator = AnalyticsAggregator(session)
            items = await aggregator.get_knowledge_heatmap(
                user_id=self.user_id,
                grade=grade,
                subject=subject,
                usage_months=_parse_usage_months(time_range),
                semester=semester,
            )
        return {"items": items}

    @tool(
        description="获取分数趋势数据：每份作业的名称、使用月份、得分率。可按科目、年级、月份筛选。",
        parameters={
            "type": "object",
            "properties": {
                "grade": {"type": "string", "description": "年级过滤"},
                "subject": {"type": "string", "description": "科目过滤，如'数学'"},
                "semester": {"type": "string", "description": "学期过滤"},
                "time_range": {
                    "type": "string",
                    "description": "时间范围（可选），按作业使用月份筛选，如'2026年4月'/'4月'/'2026-04'",
                },
            },
            "required": [],
        },
    )
    async def get_score_trend(
        self,
        grade: Optional[str] = None,
        subject: Optional[str] = None,
        semester: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> dict:
        """分数趋势（得分率看板）——使用独立会话避免并发冲突"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        async with self._new_session() as session:
            aggregator = AnalyticsAggregator(session)
            items = await aggregator.get_student_dashboard(
                user_id=self.user_id,
                grade=grade,
                subject=subject,
                semester=semester,
                usage_months=_parse_usage_months(time_range),
            )
        return {"items": items}

    @tool(
        description="对语文/英语作文进行智能批改、评分与润色，生成结构化批改报告。包含总分、分项分、逐处修改建议、润色方案、参考范文。",
        parameters={
            "type": "object",
            "properties": {
                "composition_content": {"type": "string", "description": "作文文本内容"},
                "subject": {"type": "string", "enum": ["语文", "英语"], "description": "学科"},
                "grade": {"type": "string", "description": "年级"},
                "composition_title": {"type": "string", "description": "作文题目"},
                "requirement": {"type": "string", "description": "写作要求"},
                "strict_level": {"type": "integer", "default": 3, "description": "评分严格度1-5"},
            },
            "required": ["subject", "composition_content"],
        },
    )
    async def correct_composition(
        self,
        composition_content: str = "",
        subject: str = "语文",
        grade: Optional[str] = None,
        composition_title: Optional[str] = None,
        requirement: Optional[str] = None,
        strict_level: int = 3,
    ) -> dict:
        """作文批改"""
        from app.services.composition_service import CompositionService

        service = CompositionService()
        result = await service.correct(
            content=composition_content,
            subject=subject,
            grade=grade,
            title=composition_title,
            requirement=requirement,
            strict_level=strict_level,
        )
        return result

    async def _generate_suggestions_from_ai_summary(
        self,
        subject: str,
        correct_rate: float,
        weak_kps: list[dict],
        ai_summaries: list[str],
    ) -> list[str]:
        """调用大模型，综合薄弱知识点和历次助教有话说，生成改进建议。"""
        from app.core.config import get_settings
        from app.services.agent.agent_executor import _get_llm_client

        settings = get_settings()
        client = _get_llm_client(settings.LLM_API_KEY, settings.LLM_API_BASE)

        weak_kp_names = [kp.get("name", "") for kp in weak_kps[:5] if kp.get("name")]
        summaries_text = "\n".join(
            f"- {s.strip()}" for s in ai_summaries if s and s.strip()
        )

        prompt = f"""你是一位经验丰富的中学学科助教。请根据以下信息，为学生生成3-5条综合、具体、可操作的改进建议。

学科：{subject or '未指定'}
整体正确率：{correct_rate * 100:.1f}%
薄弱知识点：{'、'.join(weak_kp_names) if weak_kp_names else '暂无'}
历次作业助教有话说：
{summaries_text}

要求：
1. 结合助教点评，指出学生当前最需要改进的方面；
2. 给出具体、可执行的学习建议；
3. 语言亲切、简洁，适合学生和家长阅读；
4. 直接输出建议列表，每条建议单独一行，不要加编号或多余解释。"""

        try:
            response = await client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一位专业的中学学科助教，擅长根据学生作业表现生成个性化学习建议。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=800,
                # 嵌套在报告生成链路里的 LLM 调用必须显式超时，
                # 否则 SDK 默认 600s 会让整个报告工具卡住无响应
                timeout=settings.LLM_REQUEST_TIMEOUT,
            )
            content = response.choices[0].message.content or ""
            suggestions = []
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                # 去掉常见的前导编号和列表符号
                line = line.lstrip("0123456789.）) ").lstrip("-•* ")
                if line:
                    suggestions.append(line)
            return suggestions
        except Exception as e:
            logger.warning("LLM 生成综合改进建议失败：%s", e)
            return []

    async def _save_report_file(self, html: str, prefix: str) -> dict:
        """
        保存报告 HTML 到存储，优先渲染为 PDF，失败时回退保存 HTML。

        generate_analysis_report / generate_correction_workbook / generate_study_plan
        三个生成类工具共用此保存链路：
        - dev 模式：本地 reports/{user_id}/ 目录，URL 走 /api/v1/files/（main.py 校验归属）
        - 生产模式：MinIO reports/{user_id}/ 前缀，URL 为带时效的预签名直链
          （/api/v1/files/ 在 DEV_MODE=false 时 404，直接写本地还会随容器重启丢失，
          必须经 StorageService 走对象存储）
        - 返回 {file_url, is_pdf}，调用方直接拼接 download_link

        参数：
        - html: 报告 HTML 内容
        - prefix: 文件名前缀（report / workbook / study_plan）
        """
        import uuid

        from app.services.file_upload import StorageService
        from app.services.pdf_renderer import render_html_to_pdf

        storage = StorageService()
        file_id = uuid.uuid4().hex[:8]
        pdf_bytes = await render_html_to_pdf(html)
        # 注意：pdf_bytes 可能为 None（渲染失败）或空字节（渲染成功但输出为空），
        # 两种情况都应回退为 HTML，故用 if pdf_bytes: 而非 if pdf_bytes is not None:
        if pdf_bytes:
            filename = f"{prefix}_{file_id}.pdf"
            object_name = await storage.save_report(pdf_bytes, filename, self.user_id)
            is_pdf = True
        else:
            filename = f"{prefix}_{file_id}.html"
            object_name = await storage.save_report(
                html.encode("utf-8"), filename, self.user_id
            )
            is_pdf = False
        if storage.dev_mode:
            file_url = f"/api/v1/files/{object_name}"
        else:
            # 生产模式：MinIO 预签名 URL（时效内可直接访问，无需额外鉴权头）
            file_url = await storage.get_presigned_url(object_name)
        return {
            "file_url": file_url,
            "is_pdf": is_pdf,
        }

    @tool(
        description="生成作业学情分析报告。支持单作业分析或指定时间范围的周期汇总分析，输出结构化HTML报告。返回结果中的 download_link 字段是可直接复制到回复中的 Markdown 下载链接，不要自行编造 URL。报告包含整体统计、知识点分析、改进建议。",
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["single", "summary"],
                    "description": "single=单作业分析，summary=周期汇总分析",
                },
                "assignment_id": {
                    "type": "string",
                    "description": "单作业模式：作业ID",
                },
                "time_range": {
                    "type": "string",
                    "description": "汇总模式：时间范围，如'2026年4月'/'2026-04'（按作业使用月份筛选）",
                },
                "subject": {
                    "type": "string",
                    "description": "学科筛选",
                },
                "grade": {"type": "string", "description": "年级筛选"},
            },
            "required": ["mode"],
        },
    )
    async def generate_analysis_report(
        self,
        mode: str = "summary",
        assignment_id: Optional[str] = None,
        time_range: Optional[str] = None,
        subject: Optional[str] = None,
        grade: Optional[str] = None,
    ) -> dict:
        """生成作业分析报告"""
        from app.services.analytics_aggregator import AnalyticsAggregator
        from app.services.pdf_renderer import PdfRenderer

        aggregator = AnalyticsAggregator(self.db)

        # 解析时间范围中的月份，按作业使用月份（usage_month）筛选
        usage_months = _parse_usage_months(time_range) if mode == "summary" else None

        # 单作业模式：解析 assignment_id（LLM 传来的是自由文本，须容错），
        # 后续所有查询（含聚合器）都按该作业过滤，保证报告只统计指定作业。
        # 注意：mode=single 但 assignment_id 缺失/无法解析时必须显式报错，
        # 不能静默退化为全量汇总报告——否则用户要"分析第3份作业"，
        # 拿到的却是全部作业的报告，且没有任何提示（A1-3 静默退化）。
        assignment_id_int: int | None = None
        if mode == "single":
            if not assignment_id:
                return {
                    "error": "单作业模式（mode=single）必须提供 assignment_id（数字作业 ID）"
                }
            try:
                assignment_id_int = int(str(assignment_id).strip())
            except (ValueError, TypeError):
                return {
                    "error": f"无法解析作业编号 '{assignment_id}'，请用数字作业 ID 重试（如 assignment_id=12）"
                }

        # 获取学情数据：使用独立会话并发执行，避免 aiomysql "connection is busy"
        async def _run_in_session(coro_factory):
            """在新会话中运行协程工厂函数"""
            try:
                async with self._new_session() as session:
                    return await coro_factory(session)
            except Exception as e:
                logger.warning("报告查询失败：%s", e)
                return None

        # 并发执行三个查询
        homework_stats, dashboard_items, heatmap_items = await asyncio.gather(
            _run_in_session(lambda s: AnalyticsAggregator(s).get_homework_stats(
                user_id=self.user_id, grade=grade, subject=subject,
                usage_months=usage_months, assignment_id=assignment_id_int
            )),
            _run_in_session(lambda s: AnalyticsAggregator(s).get_student_dashboard(
                user_id=self.user_id, grade=grade, subject=subject,
                usage_months=usage_months, assignment_id=assignment_id_int
            )),
            _run_in_session(lambda s: AnalyticsAggregator(s).get_knowledge_heatmap(
                user_id=self.user_id, grade=grade, subject=subject,
                usage_months=usage_months,
                assignment_ids=[assignment_id_int] if assignment_id_int else None
            )),
        )
        
        # 处理可能的 None 结果
        if homework_stats is None:
            homework_stats = {"total": 0, "subject_stats": []}
        if dashboard_items is None:
            dashboard_items = []
        if heatmap_items is None:
            heatmap_items = []

        # 精确统计：使用新会话
        precise_stats = await _run_in_session(lambda s: AnalyticsAggregator(s).get_precise_stats(
            user_id=self.user_id, grade=grade, subject=subject,
            usage_months=usage_months, assignment_id=assignment_id_int
        ))
        if precise_stats is None:
            precise_stats = {
                "total_assignments": 0, "total_questions": 0, "total_score": 0.0,
                "total_full_score": 0.0, "correct_rate": 0.0, "error_count": 0,
                "assignment_details": []
            }
        total_assignments = precise_stats["total_assignments"]
        total_questions = precise_stats["total_questions"]
        total_score = precise_stats["total_score"]
        total_full_score = precise_stats["total_full_score"]
        correct_rate = precise_stats["correct_rate"]
        error_count = precise_stats["error_count"]
        assignment_details = precise_stats["assignment_details"]

        # 从 assignment_details 中提取 AI 总评（detail_rows/type_map 已内联到 precise_stats）
        ai_summaries = [
            d.get("ai_summary") for d in assignment_details
            if d.get("ai_summary")
        ]

        # 构建报告数据
        report_data = PdfRenderer.build_report_data_from_analytics(
            homework_stats=homework_stats,
            dashboard_items=dashboard_items,
            heatmap_items=heatmap_items,
            total_assignments=total_assignments,
            total_questions=total_questions,
            correct_rate=correct_rate,
            error_count=error_count,
            assignment_details=assignment_details,
        )

        # 如果有 ai_summary，调用大模型综合生成改进建议
        if ai_summaries:
            llm_suggestions = await self._generate_suggestions_from_ai_summary(
                subject=subject or "全学科",
                correct_rate=correct_rate,
                weak_kps=[
                    item for item in report_data["knowledge_points"]
                    if item["score_rate"] < 0.6
                ],
                ai_summaries=ai_summaries,
            )
            if llm_suggestions:
                report_data["suggestions"] = llm_suggestions

        # 渲染 HTML（标题带上月份，便于确认筛选范围）
        month_label = f"{usage_months[0]} " if usage_months else ""
        html = PdfRenderer.build_analysis_report(
            subject=f"{month_label}{subject or '全学科'}",
            mode=mode,
            total_assignments=report_data["total_assignments"],
            total_questions=report_data["total_questions"],
            correct_rate=report_data["correct_rate"],
            error_count=report_data["error_count"],
            knowledge_points=report_data["knowledge_points"],
            assignment_details=report_data["assignment_details"],
            suggestions=report_data["suggestions"],
        )

        # 保存到本地存储（按用户目录隔离，下载时校验归属）：
        # 优先渲染为 PDF 直接下载，失败时回退为 HTML
        saved = await self._save_report_file(html, prefix="report")
        file_url = saved["file_url"]
        return {
            "file_url": file_url,
            "report_title": f"学情分析报告-{month_label}{subject or '全学科'}",
            "download_link": f"[📥 点击下载完整报告(PDF)]({file_url})" if saved["is_pdf"] else f"[📥 点击查看完整报告]({file_url})",
            "summary": f"报告已生成。总题目{report_data['total_questions']}道，正确率{report_data['correct_rate']*100:.1f}%。请在回复中使用 download_link 字段的值输出下载链接。",
            "correct_rate": report_data["correct_rate"],
        }

    @tool(
        description="生成错题订正本。支持基于单份作业或指定时间范围的错题汇总，输出结构化HTML订正本。返回结果中的 download_link 字段是可直接复制到回复中的 Markdown 下载链接，不要自行编造 URL。",
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["single", "summary"],
                    "description": "single=单作业错题，summary=周期错题汇总",
                },
                "assignment_id": {
                    "type": "string",
                    "description": "单作业模式：作业ID",
                },
                "time_range": {
                    "type": "string",
                    "description": "汇总模式：时间范围，如'2026年4月'/'2026-04'（按作业使用月份筛选）",
                },
                "subject": {
                    "type": "string",
                    "description": "学科筛选",
                },
                "include_knowledge_hint": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否包含知识点提示",
                },
                "include_full_solution": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否包含完整解题过程",
                },
            },
            "required": ["mode"],
        },
    )
    async def generate_correction_workbook(
        self,
        mode: str = "summary",
        assignment_id: Optional[str] = None,
        time_range: Optional[str] = None,
        subject: Optional[str] = None,
        include_knowledge_hint: bool = False,
        include_full_solution: bool = False,
    ) -> dict:
        """生成错题订正本"""
        from app.services.pdf_renderer import PdfRenderer
        from app.services.file_upload import StorageService
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload
        from app.models.question import Question
        from app.models.assignment import Assignment, AssignmentStatus

        storage = StorageService()

        # 查询错题（同时加载作业信息以获取名称）
        conditions = [
            Assignment.creator_id == self.user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
            Question.score.isnot(None),
            Question.full_score.isnot(None),
            Question.full_score > 0,
            Question.score < Question.full_score * 0.6,  # 使用乘法避免除法表达式潜在注入风险
        ]
        # 单作业模式：assignment_id 是 LLM 传来的自由文本，必须容错解析；
        # 缺参或解析失败直接返回错误提示，而不是静默回退到全量错题
        assignment_id_int: int | None = None
        if mode == "single":
            if not assignment_id:
                return {"error": "单作业模式（mode=single）必须提供 assignment_id（数字作业 ID）"}
            try:
                assignment_id_int = int(str(assignment_id).strip())
            except (ValueError, TypeError):
                return {"error": f"无法解析作业编号 '{assignment_id}'，请用数字作业 ID 重试（如 assignment_id=12）"}
            conditions.append(Question.assignment_id == assignment_id_int)
        if subject:
            conditions.append(Assignment.subject == subject)
        # 汇总模式：按作业使用月份（usage_month）筛选时间范围
        usage_months = _parse_usage_months(time_range) if mode == "summary" else None
        if usage_months:
            conditions.append(Assignment.usage_month.in_(usage_months))

        result = await self.db.execute(
            select(Question)
            .options(joinedload(Question.assignment))
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*conditions)
            .limit(30)
        )
        questions_list = result.scalars().all()

        wrong_questions = []

        # 并发下载题目图片：N 道错题 = N 次存储 GET，串行在 MinIO 下会把网络 RTT
        # 逐题累加（30 道 ≈ 30 次往返），gather 后降到单次最大延迟
        async def _download_image(q):
            """下载题目截图，失败返回 None（调用方回退到预签名 URL）"""
            if not q.image_url:
                return None
            try:
                return await storage.get_file_bytes(q.image_url)
            except Exception:
                return None

        downloaded = (
            await asyncio.gather(*[_download_image(q) for q in questions_list])
            if questions_list else []
        )

        for q, image_bytes in zip(questions_list, downloaded):
            # 优先将题目截图内联为 base64 data URI，
            # 保证无头浏览器渲染 PDF 时无需网络/鉴权即可加载图片
            image_url = q.image_url
            if image_bytes:
                import base64
                ext = q.image_url.rsplit(".", 1)[-1].lower() if "." in q.image_url else "png"
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
                image_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
            else:
                try:
                    image_url = await storage.get_presigned_url(q.image_url)
                except Exception:
                    pass
            # 答案可能含 $...$ 包裹的 LaTeX 公式（识别新格式），订正本/对话无法渲染，
            # 统一降级为可读纯文本（如 $\frac{1}{2}$ → 1/2）
            from app.utils.latex_convert import to_plain
            wrong_questions.append({
                "question_number": q.question_number,
                "assignment_name": q.assignment.name if q.assignment else "",
                "image_url": image_url,
                "student_answer": to_plain(q.student_answer) or "未作答",
                "correct_answer": to_plain(q.correct_answer) or "",
                "knowledge_points": q.knowledge_points or [],
                "wrong_reason": "",
            })

        # 渲染 HTML（标题带上月份，便于确认筛选范围）
        month_label = f"{usage_months[0]} " if usage_months else ""
        html = PdfRenderer.build_correction_workbook(
            subject=f"{month_label}{subject or '全学科'}",
            question_count=len(wrong_questions),
            questions=wrong_questions,
        )

        # 保存（按用户目录隔离，下载时校验归属）：优先 PDF，失败回退 HTML
        saved = await self._save_report_file(html, prefix="workbook")
        file_url = saved["file_url"]
        return {
            "file_url": file_url,
            "workbook_title": f"错题订正本-{month_label}{subject or '全学科'}",
            "download_link": f"[📥 点击下载错题订正本(PDF)]({file_url})" if saved["is_pdf"] else f"[📥 点击查看错题订正本]({file_url})",
            "question_count": len(wrong_questions),
        }

    @tool(
        description="生成个性化的一周专项学习计划。先查询该学生的错题知识点、知识掌握度和作业表现，再由 AI 制定每日学习安排（主题/知识点/任务/时长/复习项），返回对话内 Markdown 计划文本和可下载文件链接。返回结果中的 plan_md 需在回复中完整包含，download_link 原样复制。",
        parameters={
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "学科（如：数学/语文/英语）"},
                "grade": {"type": "string", "description": "年级（可选）"},
                "time_range": {
                    "type": "string",
                    "description": "统计数据时间范围，如'2026年6月'/'2026-04'（可选，默认全部）",
                },
                "plan_days": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 14,
                    "default": 7,
                    "description": "计划天数，默认7天",
                },
                "focus": {"type": "string", "description": "专注方向，如'函数与导数'（可选）"},
            },
            "required": [],
        },
    )
    async def generate_study_plan(
        self,
        subject: Optional[str] = None,
        grade: Optional[str] = None,
        time_range: Optional[str] = None,
        plan_days: int = 7,
        focus: Optional[str] = None,
    ) -> dict:
        """生成一周专项学习计划"""
        from openai import AsyncOpenAI

        from app.services.knowledge_tracker import KnowledgeTracker
        from app.services.analytics_aggregator import AnalyticsAggregator

        settings = get_settings()

        # ---- 数据收集（一次取够，单项失败不阻断整体流程） ----
        # 使用独立会话并发执行，避免 aiomysql "connection is busy"
        usage_months = _parse_usage_months(time_range) if time_range else None

        async def _query_weak():
            try:
                async with self._new_session() as session:
                    tracker = KnowledgeTracker(session)
                    return await tracker.query(
                        user_id=self.user_id, subject=subject, query_type="薄弱点查询"
                    )
            except Exception as e:
                logger.warning("查询薄弱知识点失败：%s", e)
                return {}

        async def _query_mastery():
            try:
                async with self._new_session() as session:
                    tracker = KnowledgeTracker(session)
                    return await tracker.query(
                        user_id=self.user_id, subject=subject, query_type="掌握度汇总"
                    )
            except Exception as e:
                logger.warning("查询掌握度汇总失败：%s", e)
                return {}

        async def _query_heatmap():
            try:
                async with self._new_session() as session:
                    aggregator = AnalyticsAggregator(session)
                    return await aggregator.get_knowledge_heatmap(
                        user_id=self.user_id,
                        grade=grade,
                        subject=subject,
                        usage_months=usage_months,
                    )
            except Exception as e:
                logger.warning("查询错题知识点失败：%s", e)
                return []

        async def _query_stats():
            try:
                # 必须与薄弱点/热力图同口径传 subject：
                # 生成"英语学习计划"时作业统计混入数学数据会写出错误建议（W1）
                async with self._new_session() as session:
                    aggregator = AnalyticsAggregator(session)
                    return await aggregator.get_homework_stats(
                        user_id=self.user_id,
                        grade=grade,
                        subject=subject,
                        usage_months=usage_months,
                    )
            except Exception as e:
                logger.warning("查询作业统计失败：%s", e)
                return {}

        # 并发执行四个独立会话的查询（各 _query_* 内部已用 _new_session() 独立会话，gather 并发安全）
        weak_res, mastery_res, heatmap, stats = await asyncio.gather(
            _query_weak(), _query_mastery(), _query_heatmap(), _query_stats()
        )

        # 薄弱知识点（按掌握度升序，取前 8 条）
        weak_items: list[dict] = (weak_res.get("items", []) or [])[:8]
        weak_summary = weak_res.get("summary", "暂无记录") or "暂无记录"
        # 掌握度汇总摘要
        mastery_summary = mastery_res.get("summary", "暂无记录") or "暂无记录"
        # 错题知识点热力图（作业维度的知识点错误分布）
        heatmap_text = "、".join(
            f"{h['knowledge_point']}(错题频次{h['frequency']})" for h in heatmap[:8]
        )
        # 作业统计（提交数量、科目分布）
        subject_stats = stats.get("subject_stats", [])
        stats_text = f"共提交{stats.get('total', 0)}份作业；" + "、".join(
            f"{s.get('subject', '')}{s.get('count', 0)}份" for s in subject_stats
        )

        # 组装 LLM 输入：薄弱知识点按掌握度升序列出（最薄弱排最前）
        weak_lines = "\n".join(
            f"- {item.get('point_name', '未知')}（掌握度{item.get('mastery_score', '?')}）"
            for item in weak_items
        ) or "- 暂无薄弱知识点记录"

        prompt = f"""你是资深的中学生学科学习规划师，根据学生真实学情制定可执行的学习计划。

学科：{subject or '未指定'}
年级：{grade or '未指定'}
计划天数：{plan_days}
专注方向：{focus or '整体提升'}

薄弱知识点（按掌握度从低到高）：
{weak_lines}

掌握度汇总：{weak_summary}
{mastery_summary}

错题知识点分布：{heatmap_text or '暂无'}

作业情况：{stats_text or '暂无'}

要求：
1. daily_plan 的每个知识点必须引用上面提供的真实数据，不编造
2. 任务具体可执行（如"完成10道二次函数图像题""重做错题本第3题"），包含时长
3. 每天的 review_items 安排间隔复习前几天的内容
4. 知识点按掌握度从低到高安排到各天（越薄弱越靠前）
5. 严格输出如下 JSON 结构，不要输出任何其他内容：
{{"title": "计划标题", "overall_goal": "总体目标", "daily_plan": [{{"day": 1, "theme": "主题", "knowledge_points": ["知识点"], "tasks": ["任务"], "duration_minutes": 60, "review_items": ["复习项"]}}], "tips": ["建议"]}}"""

        # ---- 一次 LLM 调用生成结构化计划（不嵌套第二层调用） ----
        # 重试/JSON 容错解析统一走 llm_json.request_llm_json（与 similar_generator 共用实现）
        from app.services.agent.agent_executor import _get_llm_client
        client = _get_llm_client(settings.LLM_API_KEY, settings.LLM_API_BASE)
        # max_tokens 必须足够大：7 天详细计划的完整 JSON 实测需 2500~3400 字符，
        # 4000 tokens 会被截断（实测 finish_reason=length，返回空内容或 JSON 断裂无法解析，表现为"生成失败"），
        # 放宽到 8000（实测 8000 输出耗时 ~60s，超时 160s 仍充裕，外层工具超时 180s 已含此调用）。
        plan_result = await request_llm_json(
            client,
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是一位资深的中学生学科学习规划师，擅长根据学生真实学情制定可执行的学习计划。严格输出 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=8000,
            timeout=160,
            attempts=2,
            retry_delay=2,  # 重试前短暂等待，避开瞬时限流；重试失败则直接落兜底文案
            extract_braces=True,  # 模型可能附带多余文本，提取首个 { 到末尾 } 再解析
        )
        plan_json = plan_result.data
        raw_text = plan_result.raw_text
        llm_error = plan_result.error

        # ---- 组装计划内容（Markdown 用于对话内展示，HTML 用于保存文件） ----
        # daily_plan 必须校验为「非空 list」：LLM 偶发把单天计划输出成对象
        # 或字符串，按 list 遍历会得到键名/字符而非天计划，下游渲染直接崩溃（A1-6）
        if (
            isinstance(plan_json, dict)
            and isinstance(plan_json.get("daily_plan"), list)
            and plan_json["daily_plan"]
        ):
            plan_md = _build_plan_markdown(plan_json, plan_days)
            title = plan_json.get("title", "学习计划")
            # 结构化渲染：按 JSON 生成规整的表格排版（标题/总体目标/每日计划表/学习建议），
            # 替代旧的 <pre> 塞 Markdown 原文方案（原方案导致 PDF 排版杂乱、表格竖线裸露）
            from app.services.pdf_renderer import PdfRenderer

            plan_html = PdfRenderer.build_study_plan_html(
                title,
                plan_json,
                subject=subject or "未指定",
                plan_days=plan_days,
            )
        else:
            # LLM 输出不是合法 JSON 时，直接把原始文本当计划内容；
            # 调用异常（raw_text 为空）时给出区分原因的失败提示，便于用户判断是否重试
            if raw_text:
                plan_md = raw_text
            else:
                err_lower = llm_error.lower()
                if "timeout" in err_lower or "timed out" in err_lower:
                    plan_md = "抱歉，学习计划生成超时，请稍后重试。"
                elif "rate" in err_lower or "limit" in err_lower:
                    plan_md = "抱歉，请求过于频繁被限流，请稍等片刻再试。"
                else:
                    plan_md = "抱歉，学习计划生成失败，请稍后重试。"
            title = f"{plan_days}天学习计划"
            # 无结构化数据时的兜底展示：Markdown 原文放入 <pre>，至少保证换行可读
            import html as html_lib

            plan_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{html_lib.escape(title)}</title></head>
<body style="font-family: 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 24px auto; line-height: 1.7;">
<pre style="white-space: pre-wrap; word-break: break-word;">{html_lib.escape(plan_md)}</pre>
</body>
</html>"""

        # ---- 保存文件（复用报告保存链路：优先 PDF，失败回退 HTML） ----
        saved = await self._save_report_file(plan_html, prefix="study_plan")

        weak_names = "、".join(item.get("point_name", "") for item in weak_items[:5])
        return {
            "title": title,
            "plan_md": plan_md,
            "file_url": saved["file_url"],
            "download_link": f"[📥 点击下载学习计划(PDF)]({saved['file_url']})" if saved["is_pdf"] else f"[📥 点击查看学习计划]({saved['file_url']})",
            "summary": f"已生成{plan_days}天学习计划，覆盖薄弱知识点：{weak_names or '暂无'}。请在回复中完整包含 plan_md 内容，并使用 download_link 字段的值输出下载链接。",
        }

    @tool(
        description="对单道题目进行分步讲解。支持分步引导式、直接讲解式、基础科普式三种风格。每步只讲一个要点，结尾主动追问理解情况。",
        parameters={
            "type": "object",
            "properties": {
                "exercise_content": {
                    "type": "string",
                    "description": "题目完整题干内容",
                },
                "subject": {"type": "string", "description": "题目所属学科"},
                "explanation_style": {
                    "type": "string",
                    "enum": ["分步引导式", "直接讲解式", "基础科普式"],
                    "default": "分步引导式",
                },
                "card_mode": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否为卡片模式，卡片模式下每步内容更精简",
                },
                "strict_level": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3,
                    "description": "讲解严格度，越高则步骤越细、追问越深",
                },
            },
            "required": ["exercise_content"],
        },
    )
    async def explain_exercise(
        self,
        exercise_content: str = "",
        subject: str = "未知",
        explanation_style: str = "分步引导式",
        card_mode: bool = False,
        strict_level: int = 3,
    ) -> dict:
        """分步讲解题目"""
        from app.services.explain_service import ExplainService

        service = ExplainService()
        steps_data = []
        knowledge_points = []
        final_summary = ""

        async for event in service.explain(
            exercise_content=exercise_content,
            subject=subject,
            explanation_style=explanation_style,
            strict_level=strict_level,
            card_mode=card_mode,
        ):
            if event["type"] == "start":
                knowledge_points = event.get("knowledge_points", [])
            elif event["type"] == "step":
                steps_data.append({
                    "step_number": event["step_number"],
                    "title": event["title"],
                    "content": event["content"],
                    "key_point": event["key_point"],
                    "follow_up_question": event["follow_up_question"],
                })
            elif event["type"] == "done":
                final_summary = event.get("final_summary", "")

        return {
            "knowledge_points": knowledge_points,
            "total_steps": len(steps_data),
            "steps": steps_data,
            "final_summary": final_summary,
        }

    @tool(
        description="记录学生对知识点讲解的掌握反馈，同步更新长期知识状态。在讲解步骤完成后根据学生反馈调用。",
        parameters={
            "type": "object",
            "properties": {
                "knowledge_point": {"type": "string", "description": "知识点名称"},
                "feedback_level": {
                    "type": "string",
                    "enum": ["完全听懂", "部分听懂", "没听懂"],
                    "description": "学生反馈等级",
                },
                "question_id": {"type": "string", "description": "关联题目ID（可选）"},
                "session_id": {"type": "string", "description": "关联会话ID（可选）"},
            },
            "required": ["knowledge_point", "feedback_level"],
        },
    )
    async def record_mastery_feedback(
        self,
        knowledge_point: str = "",
        feedback_level: str = "部分听懂",
        question_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """记录讲解反馈并更新知识状态"""
        # 将反馈转换为知识状态变化（与 /ai-tutor/feedback 直连接口共用同一映射，
        # 见 knowledge_tracker.parse_feedback_level，避免两处口径漂移）
        from app.services.knowledge_tracker import parse_feedback_level

        mastery_change, behavior_type = parse_feedback_level(feedback_level)

        if knowledge_point:
            await self.update_knowledge_state(
                knowledge_points=[{
                    "point_name": knowledge_point,
                    "subject": "通用",
                    "mastery_change": mastery_change,
                    "behavior_type": behavior_type,
                }],
                update_source="题目讲解",
                related_id=question_id,
            )

        return {
            "knowledge_point": knowledge_point,
            "feedback": feedback_level,
            "updated": True,
        }

    @tool(
        description="更新用户的知识点掌握状态。在作业批改完成、题目讲解反馈、作文批改、口语测评后自动调用。支持批量更新多个知识点。",
        parameters={
            "type": "object",
            "properties": {
                "knowledge_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "point_name": {"type": "string", "description": "知识点名称"},
                            "subject": {"type": "string", "description": "所属学科"},
                            "mastery_change": {
                                "type": "integer",
                                "enum": [-2, -1, 0, 1, 2],
                                "description": "掌握度变化：-2严重错误/-1错误/0不变/+1正确/+2优秀",
                            },
                            "behavior_type": {
                                "type": "string",
                                "enum": ["作业正确", "作业错误", "听懂讲解", "订正正确", "练习正确", "练习错误", "口语正确", "口语错误", "作文提升点", "作文扣分点"],
                                "description": "触发行为类型",
                            },
                        },
                        "required": ["point_name", "mastery_change", "behavior_type"],
                    },
                    "description": "需要更新的知识点列表",
                },
                "update_source": {
                    "type": "string",
                    "enum": ["作业分析", "题目讲解", "订正完成", "练习测试", "作文批改", "口语测评"],
                    "description": "更新来源",
                },
                "related_id": {"type": "string", "description": "关联的作业/题目/测评ID（可选）"},
            },
            "required": ["knowledge_points", "update_source"],
        },
    )
    async def update_knowledge_state(
        self,
        knowledge_points: Optional[list[dict]] = None,
        update_source: str = "练习测试",
        related_id: Optional[str] = None,
    ) -> dict:
        """更新知识状态——使用独立会话避免并发冲突"""
        from app.services.knowledge_tracker import KnowledgeTracker

        async with self._new_session() as session:
            tracker = KnowledgeTracker(session)
            count = await tracker.update(
                user_id=self.user_id,
                knowledge_points=knowledge_points or [],
                update_source=update_source,
                related_id=related_id,
            )
        return {"updated_count": count, "detail": f"已更新 {count} 个知识点的掌握状态"}

    @tool(
        description="查询用户的知识点掌握状态。支持按学科筛选、查询薄弱点、掌握度汇总、进步点分析和学习建议。",
        parameters={
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "学科筛选（可选）"},
                "time_range": {"type": "string", "description": "时间范围（可选）"},
                "query_type": {
                    "type": "string",
                    "enum": ["薄弱点查询", "掌握度汇总", "进步点分析", "学习建议"],
                    "default": "掌握度汇总",
                    "description": "查询类型",
                },
            },
            "required": [],
        },
    )
    async def query_knowledge_state(
        self,
        subject: Optional[str] = None,
        time_range: Optional[str] = None,
        query_type: str = "掌握度汇总",
    ) -> dict:
        """查询知识状态——使用独立会话避免并发冲突"""
        from app.services.knowledge_tracker import KnowledgeTracker

        async with self._new_session() as session:
            tracker = KnowledgeTracker(session)
            result = await tracker.query(
                user_id=self.user_id,
                subject=subject,
                time_range=time_range,
                query_type=query_type,
            )
        return result


def _collect_tools(cls) -> tuple[dict[str, Callable], list[dict]]:
    """
    扫描类中带 @tool 装饰器的方法，构建注册表与 schema 列表。

    按类中方法定义顺序收集，保证 TOOL_DEFINITIONS 顺序稳定。
    """
    registry: dict[str, Callable] = {}
    definitions: list[dict] = []
    for func in cls.__dict__.values():
        schema = getattr(func, _TOOL_ATTR, None)
        if schema is None:
            continue
        registry[schema["name"]] = func
        definitions.append({"type": "function", "function": schema})
    return registry, definitions


# Tool registry & definitions (OpenAI function-calling format)，模块加载时自动收集
TOOL_REGISTRY, TOOL_DEFINITIONS = _collect_tools(AgentTools)

