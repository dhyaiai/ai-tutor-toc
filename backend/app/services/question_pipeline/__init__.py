"""智能出题工作流（LangGraph 多智能体）。

基于 LangChain 生态的四智能体流水线：
    search(联网搜题) → calibrate(难度校准) → transform(题目改造) → verify(质量校验)
其中 verify 校验不过时通过 LangGraph 的 conditional edge 回流到 transform 重改（最多 N 次）。

与 backend/app/services/agent/（手写 ReAct Agent）双轨并存，
本模块仅依赖 langchain-core / langchain-openai / langgraph 三个包。

使用方式：
    from app.services.question_pipeline import run_pipeline
    result = await run_pipeline(db, user_id=1, knowledge_points=["二次函数"],
                                question_type="单选题", correct_answer="B", ...)
"""

from app.services.question_pipeline.graph import build_graph, run_pipeline
from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState

__all__ = ["PipelineContext", "QuestionPipelineState", "build_graph", "run_pipeline"]
