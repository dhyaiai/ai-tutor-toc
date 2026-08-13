"""智能体三：题目改造师（transform node）。

职责：基于原题信息 + 搜索参考资料 + 校准后的目标难度，用 LLM 改造生成
target_count 道同类变式题（与原题同知识点、同题型，但数据/情境/设问不同）。

设计要点：
- 复用 similar_generator 的三条命题铁律（NO_LATEX_RULE / ANTI_LEAK_RULE /
  FIGURE_RULE），保证输出风格与现有同类题生成完全一致、可直接落库；
- 改造目标难度取自 calibrate 节点的校准结果；
- verify 节点判 fail 时会携带 issues 回流，本节点把 issues 作为修正指令追加进 prompt；
- 结构化输出（with_structured_output）保证 JSON 合法；失败返回空列表而非抛异常。
"""

import logging

from langgraph.runtime import Runtime

from app.services.question_pipeline.client import structured_json
from app.services.question_pipeline.schemas import GeneratedQuestion, GeneratedQuestions
from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState
from app.services.similar_generator import ANTI_LEAK_RULE, FIGURE_RULE, NO_LATEX_RULE

logger = logging.getLogger(__name__)


def _build_transform_prompt(state: QuestionPipelineState) -> str:
    """组装改造指令 prompt。"""
    kp = "、".join(state.get("knowledge_points") or []) or "未知"
    qtype = state.get("question_type") or "未知"
    difficulty = state.get("difficulty") or "medium"
    target_count = state.get("target_count") or 3

    # 难度校准原因说明
    calibration = state.get("calibration") or {}
    difficulty_note = calibration.get("reason", "按目标难度出题")

    # 搜索结果参考
    refs = state.get("references") or []
    ref_block = "无"
    if refs:
        lines = []
        for i, r in enumerate(refs[:5], start=1):
            lines.append(
                f"{i}. 标题:{r.get('title', '')} 内容:{r.get('content', '')[:200]}"
            )
        ref_block = "\n".join(lines)

    # 校验回流修正意见（verify fail 时注入）
    # 安全处理：issues 来自 verify 节点 LLM 输出，需清洗防止 prompt injection
    issues = state.get("issues") or []
    fix_block = ""
    if issues:
        # 限制单条 issue 长度，去除可能干扰 prompt 的控制字符
        cleaned_issues = [
            issue[:200].replace("\n", " ").replace("\r", "").strip()
            for issue in issues
            if isinstance(issue, str) and issue.strip()
        ]
        if cleaned_issues:
            fix_block = (
                "\n【上一轮质量检查未通过，必须修复以下问题后重新生成】\n- "
                + "\n- ".join(cleaned_issues)
            )

    grade = state.get("grade") or ""

    # 输出 JSON 字段模板：明确列出每个必填字段及其取值约束，确保 LLM
    # 不遗漏 knowledge_point / difficulty 等字段（缺字段会触发重试死循环烧 token）
    json_template = f"""{{
  "questions": [
    {{
      "question_text": "题干（选择题以（单选题）/（多选题）开头）",
      "answer": "答案（多选题用逗号分隔正确选项，如 A,C,D）",
      "analysis": "完整解析",
      "knowledge_point": "{kp}",
      "difficulty": "{difficulty}",
      "question_type": "{qtype}",
      "options": [{{"label": "A", "text": "选项内容"}}],
      "image_svg": "SVG 代码（无图则为空字符串）"
    }}
  ]
}}"""

    return f"""你是一位经验丰富的教师，担任「题目改造师」。请根据原题信息生成 {target_count} 道同类变式题。

要求：
1. 题型必须与原题一致：{qtype}
2. 考察相同知识点：{kp}，但题目数据、情境或设问角度必须与原文不同
3. 目标难度：{difficulty}（{difficulty_note}）
4. 难度分布：与目标难度一致即可，不必强制三分
5. 选择题必须包含 options（label + text）；多选题题干开头标注"（多选题）"，
   答案用逗号分隔正确选项（如 A,C,D）；单选题题干开头标注"（单选题）"
6. 必须返回 question_type 字段，用"单选题/多选题/填空题/解答题"精确标注
7. 每道题必须返回 analysis，给出完整解析（思路、步骤、依据，选择题说明为何其它选项错误）
8. 每道题的 knowledge_point 必须等于目标知识点 "{kp}"，difficulty 必须等于 "{difficulty}"
9. {NO_LATEX_RULE}
10. {ANTI_LEAK_RULE}
11. {FIGURE_RULE}

【年级】{grade or '未知'}

【联网参考素材】（可参考其命题风格与考情，但不得直接照抄题干）
{ref_block}

原题信息：
- 题型：{qtype}
- 知识点：{kp}
- 学生答案：{state.get('student_answer') or '未作答'}
- 正确答案：{state.get('correct_answer') or '未知'}
- 分析：{state.get('analysis_detail') or '无'}
{fix_block}

请严格按以下 JSON 结构输出（字段一个都不能少）：
{json_template}"""


async def transform_node(
    state: QuestionPipelineState,
    runtime: Runtime[PipelineContext],
) -> dict:
    """题目改造节点：产出 questions 列表。"""
    prompt = _build_transform_prompt(state)
    result = await structured_json(
        prompt,
        GeneratedQuestions,
        max_tokens=6000,
        temperature=0.6,
        attempts=3,
    )

    if result is None or not result.questions:
        logger.warning("题目改造失败，返回空题目列表")
        return {
            "questions": [],
            "last_error": "题目改造节点 LLM 调用失败",
        }

    # 统一转成 dict 便于 state 序列化与落库
    # 缺省字段兜底：knowledge_point / difficulty 若被模型漏掉，用目标值补齐，
    # 避免下游拿到空字段的脏数据
    target_kp = "、".join(state.get("knowledge_points") or [])
    target_difficulty = state.get("difficulty") or "medium"
    questions: list[dict] = []
    for q in result.questions:
        qd = q.model_dump()
        # 归一化难度字段：以校准难度为准（避免 LLM 自定难度与目标不一致）
        qd["difficulty"] = target_difficulty or qd.get("difficulty", "medium")
        if not qd.get("knowledge_point"):
            qd["knowledge_point"] = target_kp
        questions.append(qd)

    logger.info("题目改造完成，生成 %d 道题", len(questions))
    return {"questions": questions}

