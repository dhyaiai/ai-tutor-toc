"""
Agent 工具路由配置。

将路由规则从代码中分离，支持热更新和更易维护。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class RouteRule:
    """单条路由规则"""
    name: str
    keywords: list[str]
    tool_names: list[str]
    priority: int = 0  # 数值越小优先级越高


# 默认路由规则表（按优先级排序）
# 可通过环境变量或配置文件覆盖
DEFAULT_ROUTE_RULES: list[RouteRule] = [
    RouteRule(
        name="study_plan",
        keywords=[
            "学习计划", "专项学习", "专项计划", "复习计划", "每日计划",
            "一周计划", "周计划", "学习安排", "复习安排", "制定学习", "安排学习",
        ],
        tool_names=["generate_study_plan"],
        priority=10,
    ),
    RouteRule(
        name="correction_workbook",
        keywords=[
            "订正", "错题本", "错题整理", "错题汇总", "错题打印",
        ],
        tool_names=["generate_correction_workbook"],
        priority=10,
    ),
    RouteRule(
        name="composition",
        keywords=[
            "作文", "写作", "批改作文", "润色", "写一篇",
        ],
        tool_names=["correct_composition"],
        priority=10,
    ),
    RouteRule(
        name="explain",
        keywords=[
            "讲解", "这题", "这道题", "题目", "解题", "怎么做",
            "不会做", "看不懂", "解析一下", "讲一下", "是什么意思",
        ],
        tool_names=["explain_exercise"],
        priority=10,
    ),
    RouteRule(
        name="report",
        keywords=[
            "分析报告", "报告", "学情分析", "汇总分析", "统计报告",
            "数据分析", "生成分析", "学习情况分析", "成绩报告",
        ],
        tool_names=["generate_analysis_report"],
        priority=10,
    ),
    RouteRule(
        name="overview",
        keywords=[
            "学情", "学习情况", "学习状态", "最近学习", "帮我分析",
        ],
        tool_names=[
            "get_assignment_score",
            "get_score_trend",
            "get_error_knowledge",
            "query_knowledge_state",
        ],
        priority=20,
    ),
    RouteRule(
        name="score",
        keywords=[
            "成绩", "分数", "得分", "平均分", "得分率", "正确率",
        ],
        tool_names=[
            "get_assignment_score",
            "get_score_trend",
            "get_error_knowledge",
        ],
        priority=20,
    ),
    RouteRule(
        name="trend",
        keywords=[
            "趋势", "变化", "进步", "提升", "退步", "走势",
        ],
        tool_names=[
            "get_score_trend",
            "query_knowledge_state",
        ],
        priority=20,
    ),
    RouteRule(
        name="knowledge_state",
        keywords=[
            "掌握", "知识点", "薄弱", "强项", "掌握度", "复习重点",
        ],
        tool_names=["query_knowledge_state"],
        priority=20,
    ),
    RouteRule(
        name="state_update",
        keywords=[
            "更新知识", "记住这个", "标记", "记录知识点", "反馈",
        ],
        tool_names=["update_knowledge_state", "record_mastery_feedback"],
        priority=20,
    ),
]


def load_route_rules() -> list[RouteRule]:
    """
    加载路由规则。未来可扩展为从配置文件/数据库/环境变量读取。
    当前返回默认规则。
    """
    # TODO: 支持从 JSON/YAML 配置文件加载
    # TODO: 支持热重载（文件监听或定时轮询）
    return sorted(DEFAULT_ROUTE_RULES, key=lambda r: r.priority)


# 查询工具名称集合（用于 LLM 意图分类兜底）
QUERY_TOOL_NAMES: list[str] = [
    "get_assignment_score",
    "get_error_knowledge",
    "get_score_trend",
    "query_knowledge_state",
]