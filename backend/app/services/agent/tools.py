"""
Agent 工具函数定义。

4 个工具，底层复用 rag_service 和 analytics_aggregator：
- search_analysis_chunks: 向量检索作业分析文本
- get_assignment_score: 获取平均分、提交数量等统计
- get_error_knowledge: 查询错题知识点分布
- get_score_trend: 获取分数趋势数据
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
        """学情概览"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        return await aggregator.get_overview(
            user_id=self.user_id,
            grade=args.get("grade"),
            subject=args.get("subject"),
        )

    async def _get_error_kp(self, args: dict) -> dict:
        """错题知识点"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        weak_points = await aggregator.get_weakness(
            user_id=self.user_id,
            grade=args.get("grade"),
            subject=args.get("subject"),
            semester=args.get("semester"),
            limit=args.get("limit", 10),
        )
        return {"weak_points": weak_points}

    async def _get_trend(self, args: dict) -> dict:
        """分数趋势"""
        from app.services.analytics_aggregator import AnalyticsAggregator

        aggregator = AnalyticsAggregator(self.db)
        trends = await aggregator.get_score_trend(
            user_id=self.user_id,
            grade=args.get("grade"),
            subject=args.get("subject"),
            semester=args.get("semester"),
        )
        return {"trends": trends}
