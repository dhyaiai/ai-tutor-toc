"""
工具路由层：用户消息 → 工具子集。

背景：
    AI 助教之前把全部工具 schema 传给 LLM（tool_choice="auto"），
    LLM 在问题没有匹配工具时会反复调用查询工具"凑数据"，
    5 轮 ReAct × 每轮 120s 超时 = 最坏 10 分钟无结果。

解决思路：
    在进入 ReAct 循环前，用关键词规则把用户消息路由到"工具子集"，
    只把相关工具 schema 传给 LLM：
    - 命中规则 → 只传子集 schema，LLM 只能从子集内选择
    - 未命中任何规则 → 不带 tools 直接流式回答（纯聊天秒回）
    路由失败的最坏结果是"AI 直接文本回答"，远好于现状的"卡 10 分钟"。

已知限制：
    - 路由只看当前消息，跨轮追问（如"那英语的呢"）会落入纯聊天分支，
      由 LLM 依据历史上下文自行回答（复杂度高收益低，不做跨轮路由）。
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from app.services.agent.prompts import CLASSIFY_SYSTEM_PROMPT
from app.services.agent.tools import TOOL_DEFINITIONS
from app.services.agent.route_config import RouteRule, load_route_rules, QUERY_TOOL_NAMES

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """路由结果。tool_names 为 None 表示纯聊天，不带任何工具。"""

    route_name: str                # 命中规则名，如 "study_plan" / "chat"
    tool_names: Optional[list[str]]  # None = 不带 tools，直接流式回答


# 从配置加载路由规则（按优先级已排序）
_ROUTE_RULES: list[RouteRule] = load_route_rules()


def route_message(message: str, context: Optional[dict] = None) -> RouteResult:
    """
    按优先级顺序匹配关键词，返回命中的路由；未命中返回 tools=None 表示纯聊天。

    参数：
    - message: 用户当前消息
    - context: 年级/学科等上下文（目前不参与路由判断，保留参数位供后续扩展）
    """
    text = message or ""

    for rule in _ROUTE_RULES:
        if any(kw in text for kw in rule.keywords):
            return RouteResult(route_name=rule.name, tool_names=rule.tool_names)

    # 未命中任何规则 → 纯聊天，不带工具
    return RouteResult(route_name="chat", tool_names=None)


def filter_tool_definitions(tool_names: Optional[list[str]]) -> Optional[list[dict]]:
    """
    从 TOOL_DEFINITIONS 中按工具名过滤出 OpenAI function-calling schema 子集。

    tool_names 为 None 时返回 None（表示不带 tools 调用 LLM）。
    """
    if tool_names is None:
        return None
    return [d for d in TOOL_DEFINITIONS if d.get("function", {}).get("name") in tool_names]


# ── LLM 意图分类兜底（关键词路由未命中时的第二层判断）──
# 只读查询工具子集：全部是毫秒~秒级 SQL 聚合，无嵌套 LLM 调用，
# 即使 4 个全调用一遍也远小于 AGENT_TIME_BUDGET，不会复现旧版
# "全量工具 + 无预算 → LLM 反复调工具空转 10 分钟"的问题。
# QUERY_TOOL_NAMES 定义在 route_config.py，此处通过 import 引用


async def classify_need_data(
    message: str,
    history: Optional[list[dict]],
    context: Optional[dict],
    client: AsyncOpenAI,
    model: str,
    timeout: int = 5,
    history_turns: int = 4,
) -> bool:
    """
    LLM 意图分类：判断用户消息是否需要查询"该用户个人的学习数据"。

    返回 True = 需要查库（调用方应给 QUERY_TOOL_NAMES 子集走 ReAct）；
    返回 False = 纯聊天（调用方维持原快路径直接回答）。

    降级策略（保证最坏结果 = 直接文本回答，绝不比现状差）：
    - 分类调用超时 / LLM 异常 / 输出解析失败 → 一律返回 False
    - 分类失败不抛异常、不产出 error 事件（静默回落纯聊天）
    """
    # 1. 拼消息：分类系统提示词 + (可选)年级/科目上下文 + (可选)最近 N 轮历史 + 当前消息
    user_parts = []
    if context and (context.get("grade") or context.get("subject")):
        user_parts.append(
            f"当前用户信息：年级={context.get('grade', '未指定')}，"
            f"科目={context.get('subject', '未指定')}"
        )
    if history and history_turns > 0:
        # 历史截断：最多最近 history_turns 轮、每轮 200 字，
        # 足够覆盖"那英语的呢"这类跨轮追问，同时控制输入成本
        recent = [
            f"{'用户' if h.get('role') == 'user' else '助手'}：{str(h.get('content', ''))[:200]}"
            for h in history[-history_turns:]
        ]
        user_parts.append("最近对话：\n" + "\n".join(recent))
    user_parts.append(f"用户当前消息：{message}")
    messages = [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]

    # 2. 单次非流式调用：低温、wait_for 硬限时
    # 注意 max_tokens 不能给太小：deepseek-v4-flash/qwen3 等思考型模型把推理 token
    # 计入 max_tokens 上限，50 的预算会在思考阶段就用尽，正文（JSON）几乎必然为空，
    # 分类器系统性回落到纯聊天——数据型问题被降级为无工具编造回答。
    # 512 足够"思考 + 输出 JSON"，提示词也已要求跳过思考直接输出结论。
    #
    # 超时策略：SDK 内置 timeout 抛 APITimeoutError，外层 wait_for 提供 +3s 缓冲
    # 用于捕获 SDK 内部挂起但未抛出的极端情况（此时外层抛 asyncio.TimeoutError）。
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=512,
                temperature=0,
                timeout=timeout,
            ),
            timeout=timeout + 3,
        )
    except Exception as e:
        # 超时/网络错误/模型异常 → 回落纯聊天
        logger.warning("Agent classify failed (fallback to chat): %s", e)
        return False

    # 3. 容错解析：优先正则提 need_data 字段，再兜底 YES/NO 判断，再回落
    content = (response.choices[0].message.content or "").strip()
    m = re.search(r'"need_data"\s*:\s*(true|false)', content, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "true"
    m2 = re.search(r"\b(yes|no|true|false)\b", content, re.IGNORECASE)
    if m2:
        return m2.group(1).lower() in ("yes", "true")
    logger.warning("Agent classify unparseable: %r", content[:100])
    return False
