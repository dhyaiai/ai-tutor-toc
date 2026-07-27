"""
AI 助教对话 API（SSE 流式）

支持可选的 session_id 参数，传入后会在对话完成后自动保存消息到对应会话，
实现会话的持久化存储。
"""

import json
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.ai import ChatRequest

router = APIRouter(prefix="/ai-tutor", tags=["ai-tutor"])


async def _stream_chat(
    message: str,
    history: list[dict],
    context: dict | None,
    db,
    user_id: int,
    session_id: int | None = None,
):
    """
    SSE 流式返回 Agent 对话

    参数：
    - message: 用户输入的消息
    - history: 对话历史
    - context: 年级/学科等上下文
    - session_id: 可选的会话ID，传入后自动保存消息

    生成的事件类型：
    - reasoning: AI的思考过程
    - tool_call: AI调用工具
    - tool_result: 工具执行结果
    - token: AI回复的文本片段（流式）
    - error: 错误信息
    - done: 对话结束
    """
    from app.services.agent.agent_executor import AgentExecutor

    executor = AgentExecutor(db, user_id)

    # 收集完整的AI回复内容，用于后续持久化
    full_content = ""
    full_reasoning = ""
    tool_calls_list: list[str] = []

    async for event in executor.run(
        message=message,
        history=history,
        context=context,
    ):
        # 收集回复内容用于持久化
        if event.get("type") == "token":
            full_content += event.get("content", "")
        elif event.get("type") == "reasoning":
            full_reasoning += event.get("content", "")
        elif event.get("type") == "tool_call":
            tool_calls_list.append(event.get("name", ""))
        elif event.get("type") == "done" and session_id:
            # 对话结束时，自动保存用户消息和AI回复到数据库
            await _save_chat_messages(
                db=db,
                user_id=user_id,
                session_id=session_id,
                user_message=message,
                assistant_content=full_content,
                reasoning=full_reasoning,
                tool_calls=tool_calls_list,
            )

        yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _save_chat_messages(
    db,
    user_id: int,
    session_id: int,
    user_message: str,
    assistant_content: str,
    reasoning: str,
    tool_calls: list[str],
):
    """
    保存一轮对话（用户消息 + AI回复）到指定会话

    仅在会话归属当前用户且未被删除时才保存。
    保存成功后更新会话的 updated_at 时间戳。
    """
    from sqlalchemy import select

    # 验证会话归属
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
            Conversation.status == 1,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        # 会话不存在或无权访问，静默跳过
        return

    # 保存用户消息
    user_msg = ConversationMessage(
        conversation_id=session_id,
        role="user",
        content=user_message,
    )
    db.add(user_msg)

    # 保存AI回复（含推理过程和工具调用记录）
    assistant_msg = ConversationMessage(
        conversation_id=session_id,
        role="assistant",
        content=assistant_content,
        reasoning=reasoning if reasoning else None,
        tool_calls=tool_calls if tool_calls else None,
    )
    db.add(assistant_msg)

    # 更新会话活跃时间
    conv.updated_at = datetime.now()

    await db.flush()


@router.post("/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AI 助教对话接口（SSE 流式返回）

    请求体：
    - message: 当前消息（必填）
    - history: 历史对话消息列表
    - context: 年级/学科上下文（可选）
    - session_id: 关联的会话ID（可选，传入后自动持久化消息）

    返回：Server-Sent Events 流
    """
    return StreamingResponse(
        _stream_chat(
            message=req.message,
            history=[h.model_dump() for h in req.history],
            context=req.context.model_dump() if req.context else None,
            db=db,
            user_id=current_user.id,
            session_id=req.session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
