"""智能出题工作流的图组装与运行入口。

图结构（LangGraph StateGraph 线性流水线 + 校验失败回流重试）：

    START → search → calibrate → transform → verify ──(通过)→ END
                                       ↑  │(校验不过 & 尝试轮数未耗完)
                                       └──┘   feedback(issues) 回流重改

difficulty 字段在 calibrate 节点写入，transform 读取；verify 通过 conditional
edge 决定结束还是回流 transform（最多 max_attempts 轮）。

LangGraph v1 迁移说明：
- 使用 context_schema=PipelineContext 注入运行时上下文（替代旧版 config["configurable"]）
- 节点通过 runtime.context 获取 db 会话，类型安全
"""

import asyncio
import logging

from langgraph.graph import END, START, StateGraph

from app.services.question_pipeline.nodes import calibrate_node, search_node, transform_node, verify_node
from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState

# 流水线总体超时（秒）：防止 LLM 重试死循环导致请求挂死
PIPELINE_TIMEOUT = 180

logger = logging.getLogger(__name__)


def _route_after_verify(state) -> str:
    """verify 节点之后的路径决策：重试 or 结束。

    - 校验通过（verify_passed=True）→ 结束
    - 校验失败但已尝试满 max_attempts → 结束（防止死循环）
    - 校验失败且未达上限 → 回流 transform 重改
    """
    verify_passed = state.get("verify_passed", False)
    attempts = state.get("attempts") or 0
    max_attempts = state.get("max_attempts") or 3
    if verify_passed or attempts >= max_attempts:
        return "end"
    return "retry"


_compiled_graph = None  # 模块级缓存：避免每次调用重复编译 LangGraph
# 线程安全说明：适用 uvicorn 多进程部署（每进程独立模块状态）。
# 若未来使用 gevent/eventlet 等协程池（同进程多协程），需加 threading.Lock 保护。


def build_graph():
    """构建并编译智能出题工作流图（带缓存，避免重复编译）。"""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    builder = StateGraph(
        state_schema=QuestionPipelineState,
        context_schema=PipelineContext,
    )

    builder.add_node("search", search_node)
    builder.add_node("calibrate", calibrate_node)
    builder.add_node("transform", transform_node)
    builder.add_node("verify", verify_node)

    builder.add_edge(START, "search")
    builder.add_edge("search", "calibrate")
    builder.add_edge("calibrate", "transform")
    builder.add_edge("transform", "verify")
    builder.add_conditional_edges(
        "verify",
        _route_after_verify,
        {"retry": "transform", "end": END},
    )

    _compiled_graph = builder.compile()
    return _compiled_graph


async def run_pipeline(
    db,
    *,
    user_id: int,
    knowledge_points: list[str],
    question_type: str,
    correct_answer: str,
    student_answer: str = "",
    analysis_detail: str = "",
    subject: str = "",
    grade: str = "",
    base_difficulty: str = "medium",
    target_count: int = 3,
    max_attempts: int = 3,
) -> dict:
    """智能出题工作流入口。

    Args:
        db: 异步数据库会话（传给节点做知识状态查询）
        user_id: 当前用户 ID（用于难度校准读取掌握度）
        knowledge_points: 目标知识点列表
        question_type: 题型（单选题/多选题/填空题/解答题）
        correct_answer: 原题正确答案
        student_answer: 原题学生答案（可选）
        analysis_detail: 原题解析（可选）
        subject / grade: 学科 / 年级（用于搜索与掌握度筛选，可选）
        base_difficulty: 原始难度（easy/medium/hard），默认 medium
        target_count: 期望生成题目数，默认 3
        max_attempts: 质量校验不通过时的最大重试轮数，默认 3

    Returns:
        dict，含 questions / difficulty / calibration / search_summary /
        verify_status / issues / attempts。
    """
    app = build_graph()

    initial_state: QuestionPipelineState = {
        "user_id": user_id,
        "subject": subject,
        "grade": grade,
        "knowledge_points": knowledge_points,
        "question_type": question_type,
        "student_answer": student_answer,
        "correct_answer": correct_answer,
        "analysis_detail": analysis_detail,
        "base_difficulty": base_difficulty,
        "target_count": target_count,
        # search 阶段
        "search_status": "pending",
        "search_summary": "",
        "references": [],
        # calibrate 阶段
        "calibration": None,
        # transform 阶段
        "difficulty": base_difficulty,
        "questions": [],
        # verify 阶段
        "verify_status": "pending",
        "issues": [],
        "attempts": 0,
        "max_attempts": max_attempts,
        "verify_passed": False,
        "last_error": "",
    }

    # LangGraph v1 Context API：通过 context= 注入运行时上下文，节点从 runtime.context 获取
    context = PipelineContext(db=db)
    try:
        final = await asyncio.wait_for(app.ainvoke(initial_state, context=context), timeout=PIPELINE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("智能出题工作流执行超时（%d秒）", PIPELINE_TIMEOUT)
        return {
            "questions": [],
            "difficulty": base_difficulty,
            "calibration": None,
            "search_summary": "",
            "verify_status": "failed",
            "issues": [f"工作流执行超时（{PIPELINE_TIMEOUT}秒），请重试"],
            "attempts": 0,
            "last_error": "timeout",
        }
    except Exception as e:
        logger.error("智能出题工作流执行失败: %s", e, exc_info=True)
        return {
            "questions": [],
            "difficulty": base_difficulty,
            "calibration": None,
            "search_summary": "",
            "verify_status": "failed",
            "issues": [f"工作流执行失败: {str(e)}"],
            "attempts": 0,
            "last_error": str(e),
        }

    return {
        "questions": final.get("questions") or [],
        "difficulty": final.get("difficulty"),
        "calibration": final.get("calibration"),
        "search_summary": final.get("search_summary", ""),
        "verify_status": final.get("verify_status", "pending"),
        "issues": final.get("issues") or [],
        "attempts": final.get("attempts", 0),
        "verify_passed": final.get("verify_passed", False),
        "last_error": final.get("last_error", ""),
    }