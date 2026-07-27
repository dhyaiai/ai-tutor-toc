"""
ReAct Agent 执行器。

使用大模型 + 工具调用的 ReAct (Reasoning + Acting) 循环：
1. 用户输入 → LLM 思考
2. LLM 决定调用工具或生成最终回答
3. 工具执行 → 结果返回 LLM
4. 循环直到生成最终回答

通过 SSE 流式返回思考过程、工具调用和最终回答。
"""

import json
import logging
from typing import AsyncGenerator
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.services.agent.prompts import SYSTEM_PROMPT, USER_CONTEXT_TEMPLATE
from app.services.agent.tools import TOOL_DEFINITIONS, AgentTools

logger = logging.getLogger(__name__)


class AgentExecutor:
    """
    ReAct Agent 执行器。

    使用方式：
        executor = AgentExecutor(db_session, user_id)
        async for event in executor.run(message="分析数学薄弱点", history=[], context={}):
            yield f"data: {json.dumps(event)}\n\n"
    """

    def __init__(self, db, user_id: int):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.model = settings.LLM_MODEL
        self.tools = AgentTools(db, user_id)
        self.max_iterations = 5  # Max ReAct loops
        self.db = db
        self.user_id = user_id

    async def _load_knowledge_state(self) -> str:
        """
        从数据库加载当前用户的知识状态摘要，
        注入到系统提示词的 {{user_knowledge_state}} 占位符中。
        返回简化的知识状态文本描述。
        """
        try:
            from app.services.knowledge_tracker import KnowledgeTracker
            tracker = KnowledgeTracker(self.db)
            result = await tracker.query(user_id=self.user_id, query_type="掌握度汇总")
            if result["items"]:
                return result["summary"]
            return "暂无知识状态记录。完成作业分析后将自动建立学习画像。"
        except Exception:
            return "知识状态加载失败，本次对话中将不包含个性化学习画像。"

    async def _load_personality(self) -> str:
        """
        从数据库加载当前用户的助教性格配置，
        生成性格指令文本，追加到系统提示词末尾。

        配置缺失时返回默认的严谨专业型配置。
        """
        try:
            from app.services.personality_service import load_grading_directive

            # 与全系统 AI 批改共用同一套个性化指令（性格/说话风格/评分严格度）
            return await load_grading_directive(self.db, self.user_id)
        except Exception:
            return ""

    async def run(
        self,
        message: str,
        history: list[dict] | None = None,
        context: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        ReAct 循环，流式产出事件。

        Yields:
            {"type": "reasoning", "content": "..."}
            {"type": "tool_call", "name": "...", "args": {...}}
            {"type": "tool_result", "name": "...", "summary": "..."}
            {"type": "token", "content": "..."}
            {"type": "error", "content": "..."}
            {"type": "done"}
        """
        # 加载用户知识状态和性格配置并注入到系统提示词
        knowledge_state_text = await self._load_knowledge_state()
        personality_text = await self._load_personality()

        system_prompt = SYSTEM_PROMPT.format(
            user_knowledge_state=knowledge_state_text,
        )
        # 在系统提示词后追加性格配置指令
        system_prompt += "\n\n" + personality_text

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]

        # Add history
        if history:
            for h in history:
                messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})

        # Add current user message with context
        ctx_str = USER_CONTEXT_TEMPLATE.format(
            grade=context.get("grade", "未指定") if context else "未指定",
            subject=context.get("subject", "未指定") if context else "未指定",
            message=message,
        )
        messages.append({"role": "user", "content": ctx_str})

        # ReAct loop
        for iteration in range(self.max_iterations):
            try:
                yield {"type": "reasoning", "content": f"正在分析...（第 {iteration + 1} 轮思考）"}

                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    temperature=0.7,
                    frequency_penalty=0.3,
                    presence_penalty=0.2,
                    timeout=120,
                )

                choice = response.choices[0]
                msg = choice.message

                # Check for tool calls
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        tool_args = json.loads(tc.function.arguments)

                        yield {
                            "type": "tool_call",
                            "name": tool_name,
                            "args": tool_args,
                        }

                        # Execute tool
                        result_str = await self.tools.execute(tool_name, tool_args)

                        yield {
                            "type": "tool_result",
                            "name": tool_name,
                            "summary": result_str[:200] + ("..." if len(result_str) > 200 else ""),
                        }

                        # Add assistant + tool result to messages
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                            ],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        })

                else:
                    # Final answer — request a real streaming completion for this turn
                    yield {"type": "reasoning", "content": "正在生成回答..."}
                    stream = await self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=0.7,
                        frequency_penalty=0.3,
                        presence_penalty=0.2,
                        stream=True,
                        timeout=120,
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta and delta.content:
                            yield {"type": "token", "content": delta.content}
                    yield {"type": "done"}
                    return

            except Exception as e:
                logger.error("Agent iteration %d failed: %s", iteration, e)
                yield {"type": "error", "content": str(e)}
                yield {"type": "done"}
                return

        # Max iterations reached
        yield {"type": "token", "content": "抱歉，分析过程超过了最大轮次限制。请尝试更具体的问题。"}
        yield {"type": "done"}
