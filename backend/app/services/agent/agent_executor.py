"""
ReAct Agent 执行器。

使用大模型 + 工具调用的 ReAct (Reasoning + Acting) 循环：
1. 用户输入 → LLM 思考
2. LLM 决定调用工具或生成最终回答
3. 工具执行 → 结果返回 LLM
4. 循环直到生成最终回答

通过 SSE 流式返回思考过程、工具调用和最终回答。

可靠性设计（解决"无匹配工具时反复调工具，最坏 10 分钟无结果"的问题）：
1. 工具路由：按关键词把用户消息路由到工具子集，只把子集 schema 传给 LLM；
   未命中任何规则时先用 LLM 意图分类判断是否需要查库（ROUTER_CLASSIFY_ENABLED）：
   需要 → 带 4 个查询工具走 ReAct；不需要/分类失败 → 直接流式回答
   （分类失败最坏结果=普通文本回答，不会更差）
2. 时间预算：整体预算 AGENT_TIME_BUDGET 内必须出文，
   预算剩余不足 AGENT_MIN_FINAL_BUDGET 时强制停止调工具
3. 最后一轮强制不带 tools，保证必定输出文字回答
4. 非流式调用超时降级为直接回答，而不是报错结束
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator
from openai import APITimeoutError, AsyncOpenAI
from app.core.config import get_settings
from app.services.agent.prompts import SYSTEM_PROMPT, USER_CONTEXT_TEMPLATE
from app.services.agent.tools import TOOL_DEFINITIONS, TOOL_TIMEOUTS, AgentTools, ToolExecutionError
from app.services.agent.tool_router import (
    QUERY_TOOL_NAMES,
    classify_need_data,
    filter_tool_definitions,
    route_message,
)

logger = logging.getLogger(__name__)


# 模块级 LLM 客户端缓存：避免每次 AgentExecutor 实例化都新建 AsyncOpenAI
# （内部 httpx.AsyncClient 会创建连接池，高频对话下会积累大量未关闭连接）
# 按 (api_key, base_url) 缓存，配置变更时自然失效（新 key 产生新 client）
_llm_client_cache: dict[tuple[str, str], AsyncOpenAI] = {}


def _get_llm_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """获取缓存的 AsyncOpenAI 客户端（按 api_key + base_url 缓存）。"""
    cache_key = (api_key, base_url)
    client = _llm_client_cache.get(cache_key)
    if client is None:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        _llm_client_cache[cache_key] = client
    return client


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
        self.settings = settings
        # 使用模块级缓存的客户端（避免每次实例化新建 httpx 连接池）
        self.client = _get_llm_client(settings.LLM_API_KEY, settings.LLM_API_BASE)
        self.model = settings.LLM_MODEL
        self.tools = AgentTools(db, user_id)
        # 轮数上限仍保留，但实际由时间预算主导退出（预算优先于轮数）
        self.max_iterations = settings.AGENT_MAX_ITERATIONS
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
                # 跳过 content 为空的消息：错误终止的轮次可能存下空回复的
                # assistant 消息，原样发送会被 OpenAI 兼容端点以 400 拒绝，
                # 使整轮 ReAct 无法开始
                if not h.get("content"):
                    continue
                messages.append({"role": h.get("role", "user"), "content": h["content"]})

        # Add current user message with context
        ctx_str = USER_CONTEXT_TEMPLATE.format(
            grade=context.get("grade", "未指定") if context else "未指定",
            subject=context.get("subject", "未指定") if context else "未指定",
            message=message,
        )
        messages.append({"role": "user", "content": ctx_str})

        # ── 1. 工具路由：只把与用户意图匹配的工具子集传给 LLM ──
        # 核心可靠性设计：路由未命中时 LLM 拿不到任何工具 schema，只能直接回答，
        # 从机制上杜绝"无匹配工具还反复调查询工具凑数据"的 10 分钟死循环。
        if self.settings.ROUTE_ENABLED:
            route = route_message(message, context)
            tool_defs = filter_tool_definitions(route.tool_names)
            logger.info("Agent route: %s tools=%s", route.route_name, route.tool_names)
        else:
            # 调试兜底：关闭路由时走全量工具老逻辑
            tool_defs = TOOL_DEFINITIONS

        # ── 2. 快路径：关键词未命中 → LLM 意图分类兜底 ──
        # 关键词规则覆盖不到但确实需要查库的问法（如"帮我看看我最近学得怎么样"）
        # 由分类器自主判断：需要 → 带 4 个查询工具走 ReAct；不需要/分类失败 → 纯聊天。
        # 分类失败最坏结果 = 直接文本回答（原快路径行为），绝不会比现状差。
        if not tool_defs:
            if self.settings.ROUTER_CLASSIFY_ENABLED:
                # 分类期间先给用户可见反馈（reasoning 事件前端已支持），避免等待无响应
                yield {"type": "reasoning", "content": "正在判断是否需要查询您的学习数据..."}
                need_data = await classify_need_data(
                    message=message,
                    history=history,
                    context=context,
                    client=self.client,
                    model=self.settings.ROUTER_CLASSIFY_MODEL or self.model,
                    timeout=self.settings.ROUTER_CLASSIFY_TIMEOUT,
                    history_turns=self.settings.ROUTER_CLASSIFY_HISTORY_TURNS,
                )
                if need_data:
                    # 判定需要查库 → 给 4 个查询工具，落入下方受约束 ReAct 循环
                    tool_defs = filter_tool_definitions(QUERY_TOOL_NAMES)
                    logger.info("Agent classify: need_data=True tools=%s", QUERY_TOOL_NAMES)
                else:
                    # 判定纯聊天 → 直接走快路径
                    logger.info("Agent classify: need_data=False -> direct chat")
            else:
                # ROUTER_CLASSIFY_ENABLED=False → 维持原纯聊天快路径
                logger.info("Agent: ROUTER_CLASSIFY_ENABLED=False -> direct chat")

            # 纯聊天快路径（两个分支共用一份实现，避免后续维护时改一处漏一处）
            if not tool_defs:
                yield {"type": "reasoning", "content": "正在生成回答..."}
                async for ev in self._stream_final_answer(messages):
                    yield ev
                yield {"type": "done"}
                return

        # ── 3. 受约束的 ReAct 循环 ──
        # 退出保证：无论发生什么（预算耗尽/轮次耗尽/超时），本方法必然产出文字或 done 事件
        start = time.monotonic()
        for iteration in range(self.max_iterations):
            elapsed = time.monotonic() - start
            remaining = self.settings.AGENT_TIME_BUDGET - elapsed
            is_last = iteration == self.max_iterations - 1

            # 预算不足或最后一轮 → 强制出文（不再给工具，保证必定有答案）
            if remaining < self.settings.AGENT_MIN_FINAL_BUDGET or is_last:
                yield {"type": "reasoning", "content": "正在基于已有信息生成回答..."}
                # 预算极紧时 remaining-5 可能为负：wait_for 负超时会立刻抛 TimeoutError 导致回答失败，
                # 因此下限钳到 20s——工具执行超时已被钳制在预算内（见工具执行处），
                # 走到这里时剩余预算通常足够流式出文，但绝不能少于正常流式的安全窗口
                async for ev in self._stream_final_answer(messages, timeout=max(remaining - 5, 20)):
                    yield ev
                yield {"type": "done"}
                return

            yield {"type": "reasoning", "content": f"正在分析...（第 {iteration + 1} 轮思考）"}

            # 单轮调用超时 = min(配置超时, 剩余预算中扣掉兜底出文时间的余量)
            # 下限钳到 10s：预算剩余刚过兜底线（如 remaining ∈ [30,35)）时
            # 差值会算出负数，负超时会让调用立即失败（httpx 抛错而非走超时降级），
            # 与 204-206 行最终回答路径的 max(remaining - 5, 20) 同理
            call_timeout = max(
                min(
                    self.settings.LLM_REQUEST_TIMEOUT,
                    remaining - self.settings.AGENT_MIN_FINAL_BUDGET - 5,
                ),
                10,
            )
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=tool_defs,
                        tool_choice="auto",
                        temperature=0.7,
                        frequency_penalty=0.3,
                        presence_penalty=0.2,
                        # 显式指定输出上限：不传会走端点默认值，qwen3 思考 token 计入后回答会被静默截断
                        max_tokens=self.settings.AGENT_MAX_OUTPUT_TOKENS,
                        timeout=call_timeout,
                    ),
                    # 外层 wait_for 必须存在：SDK 超时抛的是 openai.APITimeoutError
                    # （继承链为 APIError→OpenAIError，不含 asyncio.TimeoutError），
                    # 必须与 asyncio.TimeoutError 一起捕获，否则降级分支仍是死代码；
                    # SDK 超时（call_timeout）先到期抛 APITimeoutError 走降级，
                    # 外层 wait_for（+10 缓冲）兜底 SDK 内部挂起不抛的极端情况
                    timeout=call_timeout + 10,
                )
            except (asyncio.TimeoutError, APITimeoutError):
                # 决策调用超时 → 降级为直接回答，而不是报错结束
                # 记录实际超支：外层 wait_for 的 +10s 缓冲可能导致总耗时超出时间预算，
                # 便于后续调整 AGENT_TIME_BUDGET / AGENT_MIN_FINAL_BUDGET 参数
                overrun = time.monotonic() - start - self.settings.AGENT_TIME_BUDGET
                logger.warning(
                    "Agent iteration %d LLM timeout (budget overrun: +%.1fs)",
                    iteration, overrun,
                )
                yield {"type": "error", "content": "分析超时，正在直接回答..."}
                async for ev in self._stream_final_answer(messages):
                    yield ev
                yield {"type": "done"}
                return
            except Exception as e:
                logger.error("Agent iteration %d failed: %s", iteration, e, exc_info=True)
                yield {"type": "error", "content": "分析过程中遇到内部错误，请重试"}
                yield {"type": "done"}
                return

            choice = response.choices[0]
            msg = choice.message

            # Check for tool calls
            if msg.tool_calls:
                # 收集本轮所有 tool_calls 信息（OpenAI 协议：一个 assistant 消息包含全部 tool_calls）
                pending_tool_calls: list[dict] = []

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        logger.warning("Tool %s args parse failed: %s", tool_name, tc.function.arguments)
                        tool_args = {}

                    yield {
                        "type": "tool_call",
                        "name": tool_name,
                        "args": tool_args,
                    }

                    # 计算工具执行超时
                    inner_timeout = TOOL_TIMEOUTS.get(
                        tool_name, self.settings.TOOL_EXEC_TIMEOUT
                    )
                    tool_remaining = self.settings.AGENT_TIME_BUDGET - (
                        time.monotonic() - start
                    )
                    tool_timeout = min(
                        inner_timeout + 20,
                        max(
                            tool_remaining - self.settings.AGENT_MIN_FINAL_BUDGET,
                            10,
                        ),
                    )
                    try:
                        result_str = await asyncio.wait_for(
                            self.tools.execute(tool_name, tool_args),
                            timeout=tool_timeout,
                        )
                    except asyncio.TimeoutError:
                        result_str = json.dumps(
                            {"error": f"工具 {tool_name} 执行超时，请基于已有信息继续"},
                            ensure_ascii=False,
                        )
                    except ToolExecutionError as e:
                        # 工具内部执行错误：记录详细日志，返回标准错误格式
                        logger.error("ToolExecutionError in %s: %s", tool_name, e)
                        result_str = json.dumps(
                            {"error": f"工具 {tool_name} 执行失败: {e}"},
                            ensure_ascii=False,
                        )

                    yield {
                        "type": "tool_result",
                        "name": tool_name,
                        "summary": result_str[:200] + ("..." if len(result_str) > 200 else ""),
                    }

                    # 收集本轮所有 tool_call 信息，后续一次性写入 messages
                    pending_tool_calls.append({
                        "id": tc.id,
                        "tool_name": tool_name,
                        "arguments": tc.function.arguments,
                        "result": result_str,
                    })

                # 一次性写入：一个 assistant 消息包含全部 tool_calls + 各自对应的 tool 结果消息
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": ptc["id"],
                            "type": "function",
                            "function": {
                                "name": ptc["tool_name"],
                                "arguments": ptc["arguments"],
                            },
                        }
                        for ptc in pending_tool_calls
                    ],
                })
                for ptc in pending_tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": ptc["id"],
                        "content": ptc["result"],
                    })

            else:
                # Final answer — request a real streaming completion for this turn
                yield {"type": "reasoning", "content": "正在生成回答..."}
                async for ev in self._stream_final_answer(messages):
                    yield ev
                yield {"type": "done"}
                return

        # 理论不可达（最后一轮已强制 return）；兜底仍保证出文
        yield {"type": "token", "content": "抱歉，分析未能完成，请重试或换一种问法。"}
        yield {"type": "done"}

    async def _stream_final_answer(
        self,
        messages: list[dict],
        timeout: float | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        不带 tools 的流式最终回答。

        流式整体超时实现（Python 3.10 兼容，禁用 3.11+ 的 asyncio.timeout）：
        - 用队列桥接"流消费协程"与"事件产出"，每次 queue.get() 用 wait_for 限时，
          即使服务端中途挂起（连接保持但不再发数据）也能按时结束
        - 挂起超时后取消消费任务并产出 error 事件，保证 SSE 流必然结束
        """
        stream_timeout = timeout or self.settings.LLM_STREAM_TIMEOUT

        # 建立流式连接本身可能挂起，先包一层 wait_for；
        # 任何异常都转成 error 事件而不是抛出，保证调用方（run 各分支）的 SSE 流必然收尾
        try:
            stream = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                    frequency_penalty=0.3,
                    presence_penalty=0.2,
                    # 同决策调用：防止端点默认 max_tokens 过小导致流式回答中途截断
                    max_tokens=self.settings.AGENT_MAX_OUTPUT_TOKENS,
                    stream=True,
                    timeout=stream_timeout,
                ),
                timeout=stream_timeout,
            )
        except asyncio.TimeoutError:
            yield {"type": "error", "content": "回答生成超时，请重试。"}
            return
        except Exception as e:
            logger.error("Final answer create failed: %s", e, exc_info=True)
            yield {"type": "error", "content": "回答生成失败，请重试"}
            return

        async def _feed_queue(queue: asyncio.Queue):
            """消费 LLM 流并把事件放入队列；流结束或异常时放入哨兵。"""
            try:
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        await queue.put(("token", delta.content))
                    # 记录流结束原因：finish_reason=length 表示输出被 max_tokens 截断，便于后续排查
                    # 用 getattr 保护：部分 stub/兼容流可能缺失该属性，直接访问会抛异常混入 error 事件
                    finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                    if finish_reason == "length":
                        logger.warning("Final answer stream truncated by max_tokens (finish_reason=length)")
                await queue.put(("end", None))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Final answer stream failed: %s", e, exc_info=True)
                await queue.put(("error", "流式输出异常，请重试"))

        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(_feed_queue(queue))
        try:
            while True:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=stream_timeout)
                if kind == "end":
                    break
                if kind == "error":
                    yield {"type": "error", "content": payload}
                    break
                yield {"type": "token", "content": payload}
        except asyncio.TimeoutError:
            # 流整体挂起超时：取消消费任务，结束 SSE
            task.cancel()
            yield {"type": "error", "content": "回答生成超时，请重试。"}
        finally:
            if not task.done():
                task.cancel()
            # 关闭底层流式连接，释放 httpx 连接回连接池
            # （超时/异常后 stream 可能仍在打开状态，不关闭会导致连接泄漏）
            if hasattr(stream, 'aclose'):
                await stream.aclose()
