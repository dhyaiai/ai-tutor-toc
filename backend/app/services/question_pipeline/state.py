"""智能出题工作流的 LangGraph State 与 Context 定义。

State 是各智能体节点之间共享的数据总线：
- 所有节点入参读 state，出参写回 state（增量合并）
- conditional edge（verify 失败回流）依赖 attempts / verify_status 字段做路由判断

Context 是 LangGraph v1 引入的运行时上下文注入机制（替代旧版 config["configurable"]）：
- 节点通过 runtime.context 获取，类型安全且可自动补全
"""

from typing import TypedDict


class PipelineContext(TypedDict):
    """流水线运行时上下文（通过 LangGraph context_schema 注入）。

    节点通过 runtime.context 获取，无需再从 config["configurable"] 手动提取。
    """
    db: object  # 异步数据库会话（AsyncSession）


class QuestionPipelineState(TypedDict):
    """智能出题工作流共享状态。

    结构上分为五段，与五个阶段一一对应：
    输入 → search → calibrate → transform → verify
    """

    # ──── 输入（由 run_pipeline 初始化，各节点只读）────
    user_id: int
    subject: str
    grade: str
    knowledge_points: list[str]
    question_type: str
    student_answer: str
    correct_answer: str
    analysis_detail: str
    # 原始难度（easy/medium/hard），calibrate 节点会根据掌握度修正
    base_difficulty: str
    # 期望生成题目数量（默认 3）
    target_count: int

    # ──── search 阶段（联网搜题）────
    # pending / searching / done / skipped / failed
    search_status: str
    # 搜索结果摘要（喂给 transform 作为参考，也可用于日志）
    search_summary: str
    # 搜索到的参考资料列表（题目 / 大纲 / 考情），每项为 dict
    references: list[dict]

    # ──── calibrate 阶段（难度校准）────
    # {"difficulty": str, "reason": str, "mastery": {知识点: 掌握度}}
    calibration: dict | None

    # ──── transform 阶段（题目改造）────
    # 校准后的目标难度（== calibration.difficulty，无校准时回落到 base_difficulty）
    difficulty: str
    # 生成题目列表，每项为 GeneratedQuestion 的 dict 表示
    questions: list[dict]

    # ──── verify 阶段（质量校验）────
    # pending / checked
    verify_status: str
    # 校验发现的问题列表（fail 时作为反馈喂给 transform 重改）
    issues: list[str]
    # 已尝试轮数（verify 失败回流 transform 时 +1）
    attempts: int
    # 最大重试轮数（超过后无论是否通过都结束，防止死循环）
    max_attempts: int
    # 质检是否通过（graph 的 conditional edge 依赖此字段路由）
    verify_passed: bool
    # 最近一次错误信息（节点异常时写入，用于日志排查）
    last_error: str
