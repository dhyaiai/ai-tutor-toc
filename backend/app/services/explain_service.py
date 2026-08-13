"""
分步讲解服务

调用 LLM 生成讲解内容，包含两套流程：
1. explain：分步引导式讲解（供 Agent 聊天工具 explain_exercise 使用）
2. explain_full + check_thinking_answer：完整讲解 + 思考题作答判题
   （供题目卡片 AI 讲解入口使用，讲解文本兼顾 TTS 语音播报）
"""

import json
import logging
from typing import AsyncGenerator
from openai import AsyncOpenAI
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 分步讲解的 system prompt
EXPLAIN_SYSTEM_PROMPT = """你是专业的学科辅导老师，严格按照以下规则进行题目讲解。

【核心规则】
1. 分步引导式：每步只讲一个核心要点，结尾主动追问学生理解情况，禁止直接给完整答案
2. 语言贴合学生认知水平：抽象概念用生活化类比，用学生听得懂的方式讲解
3. 明确点明本题考察的知识点，关联学生已有知识
4. 严格等级越高，步骤拆分越细，追问越深入
5. 卡片模式下每步内容控制在100字以内，用短句表达

【输出格式（JSON）】
{
  "knowledge_points": ["知识点1", "知识点2"],
  "steps": [
    {
      "step_number": 1,
      "title": "审题分析",
      "content": "首先我们来看这道题...",
      "key_point": "本题考察的是...",
      "follow_up_question": "你能看出题目中的关键条件是什么吗？"
    },
    ...
  ],
  "final_summary": "这道题的解题关键是...需要特别注意..."
}

【步骤数量规则】
- 基础题（单选题/多选题/填空题）：3-4 步
- 中档题（计算题/简答题）：4-5 步
- 难题（综合题/压轴题）：5-7 步
- strict_level 越高，步骤越多越细
"""


# 完整讲解 + 思考题的 system prompt（题目卡片 AI 讲解入口使用）
FULL_EXPLAIN_SYSTEM_PROMPT = """你是资深学科名师，为学生完整讲解一道题目，讲解必须严格遵守以下全部规则，缺一不可：

【讲解规则】
1. 解题前置：先拆解考点，说明本题考哪个知识点、对应课本章节、常见易错点，不直接上来做题
2. 审题分层：逐句拆解题干，标出隐藏条件、陷阱、限定词，区分已知量/待求量
3. 思路推导：先讲「思考逻辑」，再写步骤。先说"拿到这题第一步想什么，为什么这么想，
   有几种解题路线，最优解法是什么"
4. 分步演算：每一步附带文字解释，不跳步；公式、方程、逻辑推理标注依据（定理/公式/定义）
5. 错误对照：专门列出学生最容易犯的3类典型错解，说明错在哪、思维误区是什么
6. 总结迁移：做完题提炼通用解题模板，说明同类题怎么套用
7. 语言要求：逻辑清晰，由浅入深，不用超纲术语，抽象概念用生活化类比；
   理科标注单位、写清分类讨论边界；文科分层踩点作答
8. explanation 字段的输出结构固定为六段，按顺序以以下标题开头，缺一不可：
   【考点分析】→【题干拆解】→【解题思路】→【完整标准解答】→【常见错题剖析】→【方法总结】

【格式约束】
1. 讲解文本将用于语音朗读，除上述六个固定段落标题外，
   避免使用表格、列表符号、Markdown 等不适合朗读的排版
2. 严禁使用 LaTeX、MathJax 等数学排版语言，公式一律用普通文本表示；
   若输入的题目内容中包含 $...$ 或 $$...$$ 形式的数学公式，
   必须将其含义用普通文本转述（如 $\\frac{1}{2}$ 写成 1/2，$\\sqrt{2}$ 写成 √2，$x^2$ 写成 x²），
   讲解与思考题中不得出现 $、\\frac 等 LaTeX 字符
3. 讲解结束后出一道思考题（即「变式拓展」小题，放在 thinking_question 字段，
   不要写进 explanation），围绕同一考点变式提问，检验学生是否真正理解；
   严禁在讲解文本中泄露思考题的答案
4. 严格等级越高，讲解越细致深入

【输出格式（JSON）】
{
  "knowledge_points": ["知识点1", "知识点2"],
  "explanation": "完整讲解文本（六段固定结构）",
  "thinking_question": "一道同考点变式思考题"
}
"""


# 思考题判题的 system prompt
CHECK_ANSWER_SYSTEM_PROMPT = """你是专业的学科辅导老师，负责批改学生对思考题的回答。

【批改流程】
1. 先根据题目背景独立解出思考题，得到你自己的参考答案
2. 将学生回答与参考答案对比，判定结果：
   - correct：回答正确或与参考答案等价
   - partial：方向正确但不完整，或表述有明显瑕疵
   - wrong：回答错误或答非所问
3. 给出简短反馈（不超过120字）：正确时肯定并补充要点；错误或不完整时
   指出问题并给出正确思路；反馈中严禁使用 LaTeX，公式用普通文本表示；
   若题目或学生回答中含 $...$ 公式，转述为普通文本（如 $\\frac{1}{2}$ 写成 1/2）

【输出格式（JSON）】
{"verdict": "correct|partial|wrong", "feedback": "反馈文本"}
"""


class ExplainService:
    """
    分步讲解服务

    使用方式：
        service = ExplainService()
        async for event in service.explain(content="2x+3=7, 求x", subject="数学"):
            # event: {"type": "step", "step_number": 1, "title": "...", "content": "..."}
    """

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.model = settings.LLM_MODEL

    async def explain(
        self,
        exercise_content: str,
        subject: str = "未知",
        explanation_style: str = "分步引导式",
        strict_level: int = 3,
        card_mode: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """
        生成分步讲解，流式返回每一步

        Args:
            exercise_content: 题目完整内容（题干）
            subject: 所属学科
            explanation_style: 讲解风格（分步引导式/直接讲解式/基础科普式）
            strict_level: 讲解严格度 1-5，越高越细
            card_mode: 是否为卡片模式（内容更精简）

        Yields:
            {"type": "start", "knowledge_points": [...], "total_steps": N}
            {"type": "step", "step_number": N, "title": "...", "content": "...",
             "key_point": "...", "follow_up_question": "..."}
            {"type": "done", "final_summary": "..."}
            {"type": "error", "content": "..."}
        """
        try:
            # 构建 prompt
            style_note = ""
            if card_mode:
                style_note = "\n【注意】当前为卡片模式，每步内容控制在100字以内。"
            if explanation_style == "基础科普式":
                style_note += "\n当前为基础科普模式，从最基础的概念讲起，适合基础薄弱的学生。"

            user_prompt = (
                f"题目内容：{exercise_content}\n"
                f"学科：{subject}\n"
                f"讲解风格：{explanation_style}\n"
                f"严格等级：{strict_level}/5\n"
                f"{style_note}\n"
                f"请按 JSON 格式输出分步讲解。"
            )

            # 统一走 request_llm_json：deepseek 思考型模型可能把 max_tokens 预算
            # 消耗在推理上导致正文 JSON 为空/截断，封装内置重试 + 容错解析
            from app.services.llm_json import request_llm_json
            result = await request_llm_json(
                self.client,
                model=self.model,
                messages=[
                    {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1500,
                temperature=0.3,
                timeout=120,
                response_format={"type": "json_object"},
            )
            if result.data is None:
                # 全部重试失败（空内容/截断/调用异常）——上报失败事件而非抛异常
                yield {"type": "error", "content": f"讲解生成失败: {result.error or '模型未返回有效结果'}"}
                return
            data = result.data

            knowledge_points = data.get("knowledge_points", [])
            steps = data.get("steps", [])

            # 先发送开始事件
            yield {
                "type": "start",
                "knowledge_points": knowledge_points,
                "total_steps": len(steps),
            }

            # 逐步发送
            for step in steps:
                yield {
                    "type": "step",
                    "step_number": step.get("step_number", 0),
                    "title": step.get("title", ""),
                    "content": step.get("content", ""),
                    "key_point": step.get("key_point", ""),
                    "follow_up_question": step.get("follow_up_question", ""),
                }

            # 发送完成事件
            yield {
                "type": "done",
                "final_summary": data.get("final_summary", ""),
            }

        except Exception as e:
            logger.error("分步讲解生成失败: %s", e)
            yield {"type": "error", "content": f"讲解生成失败: {str(e)}"}

    async def explain_full(
        self,
        exercise_content: str,
        subject: str = "未知",
        explanation_style: str = "直接讲解式",
        strict_level: int = 3,
        images: list[str] | None = None,
    ) -> dict:
        """
        完整讲解一道题目，末尾附一道思考题

        Args:
            exercise_content: 题目上下文文本（题干/答案/解析拼接）
            subject: 所属学科
            explanation_style: 讲解风格
            strict_level: 讲解严格度 1-5
            images: 题目切割原图的 URL 列表（data URL 或 http URL）。
                传入后走多模态视觉模型（VISION_* 配置，DeepSeek 不支持视觉），
                LLM 先读图再讲解，避免"讲一道看不见的题"；
                为空时走纯文本 LLM。

        Returns:
            {"knowledge_points": [...], "explanation": "...", "thinking_question": "..."}

        Raises:
            生成失败或内容为空时抛出异常，由调用方处理
        """
        image_note = (
            "\n图片中是本题的题干原图，请先仔细阅读图片中的题目内容，"
            "再按规则讲解（图片与文字补充信息如有出入，以图片为准）。"
            if images
            else ""
        )
        user_prompt = (
            f"题目内容：{exercise_content}\n"
            f"学科：{subject}\n"
            f"讲解风格：{explanation_style}\n"
            f"严格等级：{strict_level}/5\n"
            f"{image_note}\n"
            f"请按 JSON 格式输出完整讲解和思考题。"
        )

        # 统一走 request_llm_json（含多模态路径：content 为图片+文本列表，OpenAI 兼容）
        from app.services.llm_json import request_llm_json

        async def _try_llm(
            llm_client: AsyncOpenAI,
            llm_model: str,
            llm_content,
            *,
            timeout: int,
            attempts: int,
            max_tokens: int,
        ) -> dict | None:
            """调用一次 LLM,返回「讲解+思考题均非空」的 data,否则 None。

            request_llm_json 只兜 JSON 解析层,「模型返回合法 JSON 但字段为空/
            被截断成残缺 JSON」仍需在此兜底——不满足要求时记日志并返回 None,
            由调用方切换下一条路径(多模态 → 纯文本 → 纯文本重试)。
            """
            result = await request_llm_json(
                llm_client,
                model=llm_model,
                messages=[
                    {"role": "system", "content": FULL_EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": llm_content},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
                timeout=timeout,
                attempts=attempts,
                response_format={"type": "json_object"},
            )
            data = result.data
            if (
                data is not None
                and (data.get("explanation") or "").strip()
                and (data.get("thinking_question") or "").strip()
            ):
                return data
            logger.warning(
                "讲解生成不完整（模型=%s，错误=%s），切换下一条路径",
                llm_model,
                result.error or "讲解/思考题字段为空",
            )
            return None

        # 候选路径按优先级依次尝试，全部失败才报错（避免单一模型故障导致讲解整体 500）：
        # 1. 多模态视觉模型（仅带图时；timeout 短、不重试，失败快速降级）
        # 2. 纯文本模型（deepseek；思考型模型易截断，max_tokens 放宽、允许重试）
        # 3. 纯文本再试一次（模型偶发输出残缺 JSON 时的兜底）
        data = None
        if images:
            vision_settings = get_settings()
            vision_client = AsyncOpenAI(
                api_key=vision_settings.VISION_API_KEY,
                base_url=vision_settings.VISION_API_BASE,
            )
            content_parts: list[dict] = []
            for img in images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img, "detail": "high"},
                })
            content_parts.append({"type": "text", "text": user_prompt})
            data = await _try_llm(
                vision_client,
                vision_settings.VISION_MODEL,
                content_parts,
                timeout=120,
                attempts=1,
                max_tokens=4000,
            )
        if data is None:
            data = await _try_llm(
                self.client, self.model, user_prompt,
                timeout=180, attempts=2, max_tokens=6000,
            )
        if data is None:
            data = await _try_llm(
                self.client, self.model, user_prompt,
                timeout=180, attempts=1, max_tokens=6000,
            )

        if data is None:
            raise ValueError("讲解生成失败：模型连续多次未返回完整内容，请稍后重试")

        return {
            "knowledge_points": data.get("knowledge_points", []),
            "explanation": (data.get("explanation") or "").strip(),
            "thinking_question": (data.get("thinking_question") or "").strip(),
        }

    async def check_thinking_answer(
        self,
        exercise_content: str,
        thinking_question: str,
        user_answer: str,
        subject: str = "未知",
    ) -> dict:
        """
        批改学生对思考题的回答（LLM 先自行解题再对比判定，
        参考答案不经过前端，避免答案泄露）

        Returns:
            {"verdict": "correct|partial|wrong", "feedback": "..."}
        """
        user_prompt = (
            f"题目背景：{exercise_content}\n"
            f"学科：{subject}\n"
            f"思考题：{thinking_question}\n"
            f"学生回答：{user_answer}\n"
            f"请按 JSON 格式输出判定结果。"
        )

        # 统一走 request_llm_json：判题失败（含 JSON 解析失败/空内容）时降级为空 dict
        from app.services.llm_json import request_llm_json
        result = await request_llm_json(
            self.client,
            model=self.model,
            messages=[
                {"role": "system", "content": CHECK_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=500,
            temperature=0.2,
            timeout=60,
            response_format={"type": "json_object"},
        )
        data = result.data or {}
        verdict = data.get("verdict", "")
        if verdict not in ("correct", "partial", "wrong"):
            # 判定字段异常时保守处理：不直接判错，交由学生结合反馈自行判断
            verdict = "partial"
        feedback = (data.get("feedback") or "").strip() or "已完成批改。"
        return {"verdict": verdict, "feedback": feedback}
