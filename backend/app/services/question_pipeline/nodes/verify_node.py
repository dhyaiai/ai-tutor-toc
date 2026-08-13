"""智能体四：质量检查师（verify node）。

职责：对改造师产出的题目逐题质检，判定是否通过。检查维度：
1. 题干是否泄露/暗示正确答案（选择题尤其要查）；
2. 答案与解析是否自洽、是否正确；
3. 是否考察指定知识点、题型是否与原题一致；
4. 难度是否落在目标难度区间；
5. 公式是否合规（$ 成对、无裸 LaTeX 命令、无 KaTeX 不支持的命令、公式与中文间留空格）；
6. 必备字段是否完整（question_text / answer / analysis）。

判定逻辑：
- passed=True → 流水线正常结束；
- passed=False → 把 issues 作为反馈回流 transform 重改（由 graph 的
  conditional edge 触发，最多 max_attempts 轮）。
"""

import logging

from langgraph.runtime import Runtime

from app.services.question_pipeline.client import structured_json
from app.services.question_pipeline.schemas import VerifyResult
from app.services.question_pipeline.state import PipelineContext, QuestionPipelineState

logger = logging.getLogger(__name__)


def _build_verify_prompt(state: QuestionPipelineState) -> str:
    """组装质检指令 prompt。"""
    kp = "、".join(state.get("knowledge_points") or []) or "未知"
    qtype = state.get("question_type") or "未知"
    difficulty = state.get("difficulty") or "medium"

    q_block = "\n\n".join(
        f"题目{i + 1}：\n{_fmt_question(q)}"
        for i, q in enumerate(state.get("questions") or [])
    ) or "（无题目）"

    return f"""你是一位资深命题专家，担任「质量检查师」。请逐项检查以下 AI 生成的题目是否合格，并给出通过/不通过判定。

检查清单：
1. 题干是否泄露或暗示正确答案（选择题必须重点检查）
2. 答案与解析是否自洽、是否数学/逻辑上正确
3. 是否考察知识点：{kp}
4. 题型是否与原题一致：{qtype}
5. 难度是否在 {difficulty} 附近
6. 公式是否合规：$ 与 $$ 分隔符是否成对；公式是否都用 $...$ 包裹（严禁裸写 \\frac、\\sqrt 等 LaTeX 命令而不带 $）；是否使用 KaTeX 不支持的命令（如 \\ce、\\cancel）；公式与相邻中文之间是否留空格
7. 必备字段是否完整（question_text / answer / analysis / knowledge_point）

【待检查题目】
{q_block}

请返回 JSON 判定结果。"""


def _fmt_question(q: dict) -> str:
    """把单题 dict 转成便于检查的文本。"""
    opts = ""
    if q.get("options"):
        opts = "\n" + "\n".join(
            f"  {o.get('label', '?')}. {o.get('text', '')}" for o in q["options"]
        )
    return (
        f"题型:{q.get('question_type', '')} 知识点:{q.get('knowledge_point', '')} "
        f"难度:{q.get('difficulty', '')}\n"
        f"题干:{q.get('question_text', '')}{opts}\n"
        f"答案:{q.get('answer', '')}\n"
        f"解析:{q.get('analysis', '')}"
    )


async def verify_node(
    state: QuestionPipelineState,
    runtime: Runtime[PipelineContext],
) -> dict:
    """质量检查节点：产出 verify_status / issues / attempts。"""
    questions = state.get("questions") or []
    # 空题目列表视为检查失败（触发重试，重试耗完则结束）
    if not questions:
        return {
            "verify_status": "checked",
            "issues": ["题目改造未产出任何题目"],
            "attempts": (state.get("attempts") or 0) + 1,
        }

    prompt = _build_verify_prompt(state)
    result = await structured_json(
        prompt,
        VerifyResult,
        max_tokens=1500,
        temperature=0.2,
        attempts=2,
    )

    if result is None:
        # 质检 LLM 调用失败：视为不通过（触发重试），而不是默认放行低质量题目
        logger.warning("质量检查 LLM 调用失败，视为不通过触发重试")
        return {
            "verify_status": "checked",
            "issues": ["质量检查服务暂时不可用，需要重新检查"],
            "attempts": (state.get("attempts") or 0) + 1,
        }

    attempts = (state.get("attempts") or 0) + 1
    logger.info(
        "质量检查完成: passed=%s, issues=%s, 已尝试 %d 轮",
        result.passed, result.issues, attempts,
    )
    return {
        "verify_status": "checked",
        "verify_passed": result.passed,
        "issues": result.issues,
        "attempts": attempts,
    }

