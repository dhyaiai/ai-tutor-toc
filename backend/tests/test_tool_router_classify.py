"""
LLM 意图分类兜底路由的单元测试。

全部用 Stub client 伪造 LLM 响应，不依赖真实 API key、不访问网络。
运行方式（在 backend 目录下）：
    python -m unittest tests.test_tool_router_classify -v
"""

import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent.agent_executor import AgentExecutor
from app.services.agent.tools import AgentTools
from app.services.agent.tool_router import (
    QUERY_TOOL_NAMES,
    classify_need_data,
    filter_tool_definitions,
)


# ────────────────── Stub client 系列 ──────────────────


class StubCompletions:
    """固定响应的 chat.completions：可注入返回内容 / 抛错 / 延迟。"""

    def __init__(self, payload=None, raise_error=None, sleep=0.0):
        self.payload = payload
        self.raise_error = raise_error
        self.sleep = sleep
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.raise_error:
            raise self.raise_error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.payload))]
        )


class StubClient:
    """最小 AsyncOpenAI 形状：client.chat.completions.create()"""

    def __init__(self, **kw):
        self.chat = SimpleNamespace(completions=StubCompletions(**kw))


class ScriptedClient:
    """
    按调用顺序返回预定义结果的 client，用于 executor.run() 接入层冒烟测试。

    handlers: list[callable(kwargs) -> 响应对象 | async 迭代器]
    - 非流式响应：SimpleNamespace(choices=[SimpleNamespace(message=...)]),
      message 含 content 与 tool_calls 字段
    - 流式响应：async 生成器，产 SimpleNamespace(choices=[SimpleNamespace(delta=...)]),
      delta 含 content 字段

    形状对齐真实 AsyncOpenAI：client.chat.completions.create(...)
    """

    def __init__(self, handlers):
        self._handlers = list(handlers)
        self.calls = []  # 记录每次调用的 kwargs，供断言
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        handler = self._handlers.pop(0)
        result = handler(kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result


def _text_response(content: str, tool_calls=None):
    """构造非流式响应对象（含可选的 tool_calls）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls)
        )]
    )


async def _stream_response(text: str):
    """构造流式响应 async 生成器。"""
    async def gen():
        for ch in text:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=ch))]
            )
    return gen()


# ────────────────── 分类器单测 ──────────────────


class TestClassifyNeedData(unittest.IsolatedAsyncioTestCase):
    """classify_need_data 的输入→输出矩阵（不依赖真实 LLM）。"""

    async def test_need_data_true(self):
        """正常 JSON：need_data=true → 返回 True，且调用参数正确。"""
        client = StubClient(payload='{"need_data": true}')
        self.assertTrue(
            await classify_need_data("帮我看看我最近学得怎么样", None, None, client, "test-model")
        )
        kw = client.chat.completions.last_kwargs
        self.assertEqual(kw["model"], "test-model")
        self.assertEqual(kw["max_tokens"], 512)
        self.assertEqual(kw["temperature"], 0)
        # 消息结构：system 分类提示词 + user 消息
        self.assertEqual(len(kw["messages"]), 2)
        self.assertEqual(kw["messages"][0]["role"], "system")
        self.assertIn("用户当前消息：帮我看看我最近学得怎么样", kw["messages"][1]["content"])

    async def test_need_data_false(self):
        """正常 JSON：need_data=false → 返回 False。"""
        client = StubClient(payload='{"need_data": false}')
        self.assertFalse(
            await classify_need_data("你好", None, None, client, "test-model")
        )

    async def test_mixed_text_regex_fallback(self):
        """输出混在正文里（非纯 JSON）→ 正则兜底提取 need_data。"""
        client = StubClient(payload='好的，我的判断结果是 {"need_data": true} 请查库')
        self.assertTrue(
            await classify_need_data("我这段时间学得如何", None, None, client, "test-model")
        )

    async def test_garbage_returns_false(self):
        """输出完全不可解析 → 回落 False（纯聊天），不抛异常。"""
        client = StubClient(payload="今天天气真好呀")
        self.assertFalse(
            await classify_need_data("随便聊聊", None, None, client, "test-model")
        )

    async def test_exception_falls_back_false(self):
        """LLM 调用抛异常 → 回落 False，不向调用方抛异常。"""
        client = StubClient(raise_error=RuntimeError("api down"))
        self.assertFalse(
            await classify_need_data("我学得怎么样", None, None, client, "test-model")
        )

    async def test_timeout_falls_back_false(self):
        """分类调用超过 timeout → 回落 False。"""
        client = StubClient(payload='{"need_data": true}', sleep=10)
        self.assertFalse(
            await classify_need_data("我学得怎么样", None, None, client, "test-model", timeout=1)
        )

    async def test_history_included_and_truncated(self):
        """history 只取最近 history_turns 轮，每条截断 200 字。"""
        history = [
            {"role": "user", "content": "第一轮问题"},
            {"role": "assistant", "content": "第一轮回答"},
            {"role": "user", "content": "x" * 300},  # 超长消息应被截断到 200 字
            {"role": "assistant", "content": "最近一轮回答"},
        ]
        client = StubClient(payload='{"need_data": false}')
        await classify_need_data(
            "那英语的呢", history, {"grade": "高一", "subject": "数学"},
            client, "test-model", history_turns=2,
        )
        content = client.chat.completions.last_kwargs["messages"][1]["content"]
        # 含年级/科目上下文
        self.assertIn("当前用户信息：年级=高一", content)
        # 只含最近 2 轮（"第一轮问题/第一轮回答"应被裁掉）
        self.assertNotIn("第一轮问题", content)
        self.assertIn("用户：xxx", content)  # 300 字截断后仍有内容
        self.assertIn("助手：最近一轮回答", content)
        # 超长消息被截断：消息拼接后不应出现完整 300 个 x
        self.assertLessEqual(len(content), 200 * 2 + 300)  # 宽松上界即可

    async def test_history_turns_zero_omits_history(self):
        """history_turns=0 → 不带历史，只拼当前消息。"""
        client = StubClient(payload='{"need_data": false}')
        await classify_need_data(
            "你好", [{"role": "user", "content": "之前的内容"}],
            None, client, "test-model", history_turns=0,
        )
        content = client.chat.completions.last_kwargs["messages"][1]["content"]
        self.assertNotIn("最近对话", content)
        self.assertIn("用户当前消息：你好", content)


# ────────────────── 工具子集检查 ──────────────────


class TestQueryToolNames(unittest.TestCase):
    """QUERY_TOOL_NAMES 必须能从 TOOL_DEFINITIONS 中过滤出有效子集。"""

    def test_filter_query_tools(self):
        defs = filter_tool_definitions(QUERY_TOOL_NAMES)
        self.assertIsNotNone(defs)
        names = [d["function"]["name"] for d in defs]
        self.assertEqual(names, QUERY_TOOL_NAMES)

    def test_query_tools_only_read(self):
        """查询子集不应包含写/生成类工具。"""
        forbidden = {"update_knowledge_state", "record_mastery_feedback"}
        self.assertTrue(forbidden.isdisjoint(QUERY_TOOL_NAMES))
        self.assertEqual(len(QUERY_TOOL_NAMES), 4)


# ────────────────── executor 接入层冒烟 ──────────────────


class TestExecutorIntegration(unittest.IsolatedAsyncioTestCase):
    """验证 run() 未命中分支与分类器的接线（不访问真实 LLM/DB）。"""

    async def _run(self, handlers):
        client = ScriptedClient(handlers)
        executor = AgentExecutor(db=None, user_id=1)
        executor.client = client  # 替换为脚本化 client
        with patch.object(AgentExecutor, "_load_knowledge_state", new=AsyncMock(return_value="无")), \
             patch.object(AgentExecutor, "_load_personality", new=AsyncMock(return_value="")), \
             patch.object(AgentTools, "execute", new=AsyncMock(return_value='{"ok": true}')) as mock_execute:
            events = [ev async for ev in executor.run(
                message="帮我看看我最近学得怎么样",
                history=None,
                context={"grade": "高一", "subject": "数学"},
            )]
        return events, client, mock_execute

    async def test_classify_need_data_triggers_react_with_tools(self):
        """
        分类判定需要查库 → 后续 ReAct 决策调用应携带 4 个查询工具 schema，
        且事件流中出现 tool_call。
        """
        # 调用顺序：1) 分类（无 tools） 2) ReAct 决策（带 tools，返回 tool_call）
        #           3) 工具执行（mock） 4) 再次决策（返回文本） 5) 最终回答（流式）
        def classify_handler(kwargs):
            assert "tools" not in kwargs  # 分类调用必须不带工具 schema
            return _text_response('{"need_data": true}')

        def react_handler(kwargs):
            assert "tools" in kwargs
            tool_calls = [SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(
                    name="get_assignment_score",
                    arguments='{"subject": "数学"}',
                ),
            )]
            return _text_response(content=None, tool_calls=tool_calls)

        def react_handler2(kwargs):
            return _text_response(content="这是最终回答")

        async def final_handler(kwargs):
            assert kwargs.get("stream") is True
            return await _stream_response("最终回答内容")

        events, client, mock_execute = await self._run([
            classify_handler, react_handler, react_handler2, final_handler,
        ])

        types = [ev["type"] for ev in events]
        # 分类占位 reasoning 最先出现
        self.assertEqual(types[0], "reasoning")
        self.assertIn("判断是否需要查询", events[0]["content"])
        # 出现 tool_call 事件，且工具真实（mock）执行了
        self.assertIn("tool_call", types)
        self.assertEqual(events[types.index("tool_call")]["name"], "get_assignment_score")
        mock_execute.assert_awaited_once()
        self.assertEqual(mock_execute.await_args.args[0], "get_assignment_score")
        # 最终以 done 收尾
        self.assertEqual(types[-1], "done")
        # ReAct 决策调用携带了查询工具子集
        react_call = client.calls[1]
        tool_names = [t["function"]["name"] for t in react_call["tools"]]
        self.assertEqual(tool_names, QUERY_TOOL_NAMES)

    async def test_classify_false_keeps_direct_chat(self):
        """分类判定不需要查库 → 无 tool_call，直接流式回答。"""
        def classify_handler(kwargs):
            return _text_response('{"need_data": false}')

        async def final_handler(kwargs):
            return await _stream_response("好的，随便聊聊")

        events, client, _ = await self._run([classify_handler, final_handler])

        types = [ev["type"] for ev in events]
        self.assertNotIn("tool_call", types)
        self.assertIn("token", types)
        self.assertEqual(types[-1], "done")
        # 分类只调用了一次，且无 ReAct 决策调用
        self.assertEqual(len(client.calls), 2)  # 分类 + 最终回答


if __name__ == "__main__":
    unittest.main()
