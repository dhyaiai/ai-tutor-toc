"""
Agent 工具函数定义。

现有工具（6 个），底层复用 rag_service、analytics_aggregator 和 knowledge_tracker：
- search_analysis_chunks: 向量检索作业分析文本
- get_assignment_score: 获取平均分、提交数量等统计
- get_error_knowledge: 查询错题知识点分布
- get_score_trend: 获取分数趋势数据
- update_knowledge_state: 更新用户知识点掌握状态（跨会话持久化）
- query_knowledge_state: 查询用户知识点掌握状态
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Tool definitions (OpenAI function-calling format)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_analysis_chunks",
            "description": "搜索历史作业分析文本。输入自然语言查询，返回最相关的分析片段。可用于查找类似错题、了解某知识点的历史表现等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '分数加减法的错题分析'",
                    },
                    "grade": {
                        "type": "string",
                        "description": "年级过滤，如 '二年级'",
                    },
                    "subject": {
                        "type": "string",
                        "description": "科目过滤，如 '数学'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assignment_score",
            "description": "获取作业成绩统计数据：平均分、提交数量、总题数、错误率等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "description": "年级过滤"},
                    "subject": {"type": "string", "description": "科目过滤"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_error_knowledge",
            "description": "查询错题的知识点分布，返回高频错误知识点及错误率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "description": "年级过滤"},
                    "subject": {"type": "string", "description": "科目过滤"},
                    "semester": {"type": "string", "description": "学期过滤"},
                    "limit": {
                        "type": "integer",
                        "description": "返回 Top N",
                        "default": 10,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_score_trend",
            "description": "获取分数趋势数据，按月展示平均分变化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "grade": {"type": "string", "description": "年级过滤"},
                    "subject": {"type": "string", "description": "科目过滤"},
                    "semester": {"type": "string", "description": "学期过滤"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_composition",
            "description": "对语文/英语作文进行智能批改、评分与润色，生成结构化批改报告。包含总分、分项分、逐处修改建议、润色方案、参考范文。",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_analysis_report",
            "description": "生成作业学情分析报告。支持单作业分析或指定时间范围的周期汇总分析，输出结构化HTML报告。返回结果中的 download_link 字段是可直接复制到回复中的 Markdown 下载链接，不要自行编造 URL。报告包含整体统计、知识点分析、改进建议。",
            "parameters": {
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
                        "description": "汇总模式：时间范围，如'最近30天'",
                    },
                    "subject": {
                        "type": "string",
                        "description": "学科筛选",
                    },
                    "grade": {"type": "string", "description": "年级筛选"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_correction_workbook",
            "description": "生成错题订正本。支持基于单份作业或指定时间范围的错题汇总，输出结构化HTML订正本。返回结果中的 download_link 字段是可直接复制到回复中的 Markdown 下载链接，不要自行编造 URL。",
            "parameters": {
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
                        "description": "汇总模式：时间范围",
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_exercise",
            "description": "对单道题目进行分步讲解。支持分步引导式、直接讲解式、基础科普式三种风格。每步只讲一个要点，结尾主动追问理解情况。",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_mastery_feedback",
            "description": "记录学生对知识点讲解的掌握反馈，同步更新长期知识状态。在讲解步骤完成后根据学生反馈调用。",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_knowledge_state",
            "description": "更新用户的知识点掌握状态。在作业批改完成、题目讲解反馈、作文批改、口语测评后自动调用。支持批量更新多个知识点。",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_state",
            "description": "查询用户的知识点掌握状态。支持按学科筛选、查询薄弱点、掌握度汇总、进步点分析和学习建议。",
            "parameters": {
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
        },
    },
]


class AgentTools:
    """
    Agent 工具执行器。

    使用方式：
        tools = AgentTools(db_session, user_id)
        result = await tools.execute("search_analysis_chunks", {"query": "分数加减法"})
    """

    def __init__(self, db, user_id: int):
        self.db = db
        self.user_id = user_id

    async def execute(self, tool_name: str, arguments: dict) -> str:
        """执行工具调用，返回 JSON 字符串"""
        logger.info("Agent tool called: %s args=%s", tool_name, arguments)

        try:
            if tool_name == "search_analysis_chunks":
                result = await self._search_chunks(arguments)
            elif tool_name == "get_assignment_score":
                result = await self._get_scores(arguments)
            elif tool_name == "get_error_knowledge":
                result = await self._get_error_kp(arguments)
            elif tool_name == "get_score_trend":
                result = await self._get_trend(arguments)
            elif tool_name == "correct_composition":
                result = await self._correct_composition(arguments)
            elif tool_name == "generate_analysis_report":
                result = await self._generate_report(arguments)
            elif tool_name == "generate_correction_workbook":
                result = await self._generate_workbook(arguments)
            elif tool_name == "explain_exercise":
                result = await self._explain_exercise(arguments)
            elif tool_name == "record_mastery_feedback":
                result = await self._record_feedback(arguments)
            elif tool_name == "update_knowledge_state":
                result = await self._update_knowledge(arguments)
            elif tool_name == "query_knowledge_state":
                result = await self._query_knowledge(arguments)
            else:
                result = {"error": f"Unknown tool: {tool_name}"}

            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.error("Tool execution failed: %s, error=%s", tool_name, e)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def _search_chunks(self, args: dict) -> dict:
        """向量检索"""
        try:
            from app.services.rag_service import RAGService
            rag = RAGService()

            filters = {}
            if args.get("grade"):
                filters["grade"] = args["grade"]
            if args.get("subject"):
                filters["subject"] = args["subject"]

            results = await rag.search(
                query=args["query"],
                filters=filters if filters else None,
                limit=args.get("limit", 5),
            )
            return {
                "count": len(results),
                "results": [
                    {"score": round(r.score, 4), "text": r.text[:500], "metadata": r.metadata}
                    for r in results
                ],
            }
        except Exception:
            return {"count": 0, "results": [], "note": "向量检索暂不可用"}

    async def _get_scores(self, args: dict) -> dict:
        """学情概览（作业统计）"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        return await aggregator.get_homework_stats(
            user_id=self.user_id,
            grade=args.get("grade"),
            semester=args.get("semester"),
        )

    async def _get_error_kp(self, args: dict) -> dict:
        """错题知识点（热力图数据）"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        items = await aggregator.get_knowledge_heatmap(
            user_id=self.user_id,
            grade=args.get("grade"),
            subject=args.get("subject"),
        )
        return {"items": items}

    async def _get_trend(self, args: dict) -> dict:
        """分数趋势（得分率看板）"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        items = await aggregator.get_student_dashboard(
            user_id=self.user_id,
            grade=args.get("grade"),
            subject=args.get("subject"),
            semester=args.get("semester"),
        )
        return {"items": items}

    async def _correct_composition(self, args: dict) -> dict:
        """作文批改"""
        from app.services.composition_service import CompositionService

        service = CompositionService()
        result = await service.correct(
            content=args.get("composition_content", ""),
            subject=args.get("subject", "语文"),
            grade=args.get("grade"),
            title=args.get("composition_title"),
            requirement=args.get("requirement"),
            strict_level=args.get("strict_level", 3),
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
        from openai import AsyncOpenAI
        from app.core.config import get_settings

        settings = get_settings()
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

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

    async def _generate_report(self, args: dict) -> dict:
        """生成作业分析报告"""
        from app.services.analytics_aggregator import AnalyticsAggregator
        from app.services.pdf_renderer import PdfRenderer
        from sqlalchemy import select, func
        from app.models.assignment import Assignment, AssignmentStatus
        from app.models.question import Question

        mode = args.get("mode", "summary")
        subject = args.get("subject")
        grade = args.get("grade")

        aggregator = AnalyticsAggregator(self.db)

        # 获取学情数据
        homework_stats = await aggregator.get_homework_stats(
            user_id=self.user_id, grade=grade, semester=None
        )
        dashboard_items = await aggregator.get_student_dashboard(
            user_id=self.user_id, grade=grade, subject=subject, semester=None
        )
        heatmap_items = await aggregator.get_knowledge_heatmap(
            user_id=self.user_id, grade=grade, subject=subject
        )

        # 精确统计：按当前筛选条件（学科/年级）统计作业数、题目数、正确率、错题数
        base_conditions = [
            Assignment.creator_id == self.user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            base_conditions.append(Assignment.grade == grade)
        if subject:
            base_conditions.append(Assignment.subject == subject)

        # 作业总数
        stmt = select(func.count(Assignment.id)).where(*base_conditions)
        total_assignments = (await self.db.execute(stmt)).scalar() or 0

        # 题目总数、总得分、总满分（只统计有效打分的题目）
        stmt = (
            select(
                func.count(Question.id),
                func.coalesce(func.sum(Question.score), 0.0),
                func.coalesce(func.sum(Question.full_score), 0.0),
            )
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(
                *base_conditions,
                Question.score.isnot(None),
                Question.full_score.isnot(None),
                Question.full_score > 0,
            )
        )
        row = (await self.db.execute(stmt)).one()
        total_questions = row[0] or 0
        total_score = float(row[1] or 0)
        total_full_score = float(row[2] or 0)
        correct_rate = total_score / total_full_score if total_full_score > 0 else 0.0

        # 错题数（得分率 < 60%）
        error_conditions = base_conditions + [
            Question.score.isnot(None),
            Question.full_score.isnot(None),
            Question.full_score > 0,
            (Question.score / Question.full_score) < 0.6,
        ]
        stmt = (
            select(func.count(Question.id))
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*error_conditions)
        )
        error_count = (await self.db.execute(stmt)).scalar() or 0

        # 作业明细：名称、题量、得分、总分、得分率、助教有话说
        detail_stmt = (
            select(
                Assignment.id,
                Assignment.name,
                Assignment.ai_summary,
                func.count(Question.id).label("question_count"),
                func.coalesce(func.sum(Question.score), 0.0).label("total_score"),
                func.coalesce(func.sum(Question.full_score), 0.0).label("total_full"),
            )
            .join(Question, Assignment.id == Question.assignment_id)
            .where(
                *base_conditions,
                Question.score.isnot(None),
                Question.full_score.isnot(None),
                Question.full_score > 0,
            )
            .group_by(Assignment.id)
            .order_by(Assignment.created_at.asc())
        )
        detail_rows = (await self.db.execute(detail_stmt)).all()

        # 每个作业的题型分布
        type_stmt = (
            select(
                Assignment.id,
                Question.question_type,
                func.count(Question.id).label("type_count"),
            )
            .join(Question, Assignment.id == Question.assignment_id)
            .where(
                *base_conditions,
                Question.question_type.isnot(None),
                Question.question_type != "",
            )
            .group_by(Assignment.id, Question.question_type)
        )
        type_rows = (await self.db.execute(type_stmt)).all()
        type_map: dict[int, dict[str, int]] = {}
        for assignment_id, q_type, count in type_rows:
            type_map.setdefault(assignment_id, {})[q_type] = count

        assignment_details = []
        ai_summaries = []
        for row in detail_rows:
            assignment_id = row[0]
            name = row[1]
            ai_summary = row[2]
            question_count = row[3]
            assignment_score = float(row[4] or 0)
            assignment_full = float(row[5] or 0)
            score_rate = assignment_score / assignment_full if assignment_full > 0 else 0.0
            assignment_details.append({
                "name": name,
                "question_count": question_count,
                "type_distribution": type_map.get(assignment_id, {}),
                "score": assignment_score,
                "full_score": assignment_full,
                "score_rate": score_rate,
            })
            if ai_summary:
                ai_summaries.append(ai_summary)

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

        # 渲染 HTML
        html = PdfRenderer.build_analysis_report(
            subject=subject or "全学科",
            mode=mode,
            total_assignments=report_data["total_assignments"],
            total_questions=report_data["total_questions"],
            correct_rate=report_data["correct_rate"],
            error_count=report_data["error_count"],
            knowledge_points=report_data["knowledge_points"],
            assignment_details=report_data["assignment_details"],
            suggestions=report_data["suggestions"],
        )

        # 保存 HTML 到本地存储
        import os, uuid
        from app.core.config import get_settings
        settings = get_settings()
        filename = f"report_{uuid.uuid4().hex[:8]}.html"
        report_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "reports")
        os.makedirs(report_dir, exist_ok=True)
        filepath = os.path.join(report_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        file_url = f"/api/v1/files/reports/{filename}"
        return {
            "file_url": file_url,
            "report_title": f"学情分析报告-{subject or '全学科'}",
            "download_link": f"[📥 点击查看完整报告]({file_url})",
            "summary": f"报告已生成。总题目{report_data['total_questions']}道，正确率{report_data['correct_rate']*100:.1f}%。请在回复中使用 download_link 字段的值输出下载链接。",
            "correct_rate": report_data["correct_rate"],
        }

    async def _generate_workbook(self, args: dict) -> dict:
        """生成错题订正本"""
        from app.services.analytics_aggregator import AnalyticsAggregator
        from app.services.pdf_renderer import PdfRenderer
        from app.services.file_upload import StorageService
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload
        from app.models.question import Question
        from app.models.assignment import Assignment, AssignmentStatus

        mode = args.get("mode", "summary")
        subject = args.get("subject")
        storage = StorageService()

        # 查询错题（同时加载作业信息以获取名称）
        conditions = [
            Assignment.creator_id == self.user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
            Question.score.isnot(None),
            Question.full_score.isnot(None),
            Question.full_score > 0,
            (Question.score / Question.full_score) < 0.6,
        ]
        if mode == "single" and args.get("assignment_id"):
            conditions.append(Question.assignment_id == int(args["assignment_id"]))
        if subject:
            conditions.append(Assignment.subject == subject)

        result = await self.db.execute(
            select(Question)
            .options(joinedload(Question.assignment))
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*conditions)
            .limit(30)
        )
        questions_list = result.scalars().all()

        wrong_questions = []
        for q in questions_list:
            image_url = q.image_url
            try:
                image_url = await storage.get_presigned_url(q.image_url)
            except Exception:
                pass
            wrong_questions.append({
                "question_number": q.question_number,
                "assignment_name": q.assignment.name if q.assignment else "",
                "image_url": image_url,
                "student_answer": q.student_answer or "未作答",
                "correct_answer": q.correct_answer or "",
                "knowledge_points": q.knowledge_points or [],
                "wrong_reason": "",
            })

        # 渲染 HTML
        html = PdfRenderer.build_correction_workbook(
            subject=subject or "全学科",
            question_count=len(wrong_questions),
            questions=wrong_questions,
        )

        # 保存
        import os, uuid
        from app.core.config import get_settings
        settings = get_settings()
        filename = f"workbook_{uuid.uuid4().hex[:8]}.html"
        report_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "reports")
        os.makedirs(report_dir, exist_ok=True)
        filepath = os.path.join(report_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        file_url = f"/api/v1/files/reports/{filename}"
        return {
            "file_url": file_url,
            "workbook_title": f"错题订正本-{subject or '全学科'}",
            "download_link": f"[📥 点击查看错题订正本]({file_url})",
            "question_count": len(wrong_questions),
        }

    async def _explain_exercise(self, args: dict) -> dict:
        """分步讲解题目"""
        from app.services.explain_service import ExplainService

        service = ExplainService()
        steps_data = []
        knowledge_points = []
        final_summary = ""

        async for event in service.explain(
            exercise_content=args.get("exercise_content", ""),
            subject=args.get("subject", "未知"),
            explanation_style=args.get("explanation_style", "分步引导式"),
            strict_level=args.get("strict_level", 3),
            card_mode=args.get("card_mode", False),
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

    async def _record_feedback(self, args: dict) -> dict:
        """记录讲解反馈并更新知识状态"""
        # 将反馈转换为知识状态变化
        kp = args.get("knowledge_point", "")
        feedback = args.get("feedback_level", "部分听懂")

        if feedback == "完全听懂":
            mastery_change = 1
        elif feedback == "没听懂":
            mastery_change = -1
        else:
            mastery_change = 0

        if kp:
            await self._update_knowledge({
                "knowledge_points": [{
                    "point_name": kp,
                    "subject": "通用",
                    "mastery_change": mastery_change,
                    "behavior_type": "听懂讲解" if mastery_change >= 0 else "作业错误",
                }],
                "update_source": "题目讲解",
                "related_id": args.get("question_id"),
            })

        return {
            "knowledge_point": kp,
            "feedback": feedback,
            "updated": True,
        }

    async def _update_knowledge(self, args: dict) -> dict:
        """更新知识状态"""
        from app.services.knowledge_tracker import KnowledgeTracker

        tracker = KnowledgeTracker(self.db)
        count = await tracker.update(
            user_id=self.user_id,
            knowledge_points=args.get("knowledge_points", []),
            update_source=args.get("update_source", "练习测试"),
            related_id=args.get("related_id"),
        )
        return {"updated_count": count, "detail": f"已更新 {count} 个知识点的掌握状态"}

    async def _query_knowledge(self, args: dict) -> dict:
        """查询知识状态"""
        from app.services.knowledge_tracker import KnowledgeTracker

        tracker = KnowledgeTracker(self.db)
        result = await tracker.query(
            user_id=self.user_id,
            subject=args.get("subject"),
            time_range=args.get("time_range"),
            query_type=args.get("query_type", "掌握度汇总"),
        )
        return result
