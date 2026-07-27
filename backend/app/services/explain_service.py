"""
分步讲解服务

调用 LLM 生成交互式分步讲解内容，遵循文档规定的规则：
- 分步引导式：每步只讲一个要点，结尾主动追问理解情况
- 语言贴合学生认知水平，抽象概念用生活化类比
- 明确点明本题考察的知识点
- 支持多种讲解风格（分步引导式/直接讲解式/基础科普式）
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

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1500,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=120,
            )

            content = response.choices[0].message.content or "{}"
            data = json.loads(content)

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
