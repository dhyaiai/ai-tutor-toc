"""
会话管理 API

提供会话的 CRUD 操作和消息保存功能：
- GET  /conversations        → 获取当前用户的会话列表（按更新时间倒序）
- POST /conversations        → 创建新会话
- GET  /conversations/{id}   → 获取单个会话详情（含完整消息历史）
- PATCH /conversations/{id}  → 更新会话信息（标题、学科）
- DELETE /conversations/{id} → 软删除会话（status=0）
- POST /conversations/{id}/messages → 保存消息到指定会话
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationListItem,
    ConversationListResponse,
    ConversationMessageCreate,
    ConversationMessageResponse,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取当前用户的会话列表

    返回结果按 updated_at 倒序排列（最近活跃的在前）。
    仅返回 status=1（正常）的会话，已删除的不显示。
    每个会话附带消息数量和最后一条消息摘要。
    """
    # 查询当前用户的所有正常会话，按更新时间倒序
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id, Conversation.status == 1)
        .order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()

    items: list[ConversationListItem] = []
    for conv in conversations:
        # 统计消息数量
        count_result = await db.execute(
            select(func.count(ConversationMessage.id)).where(
                ConversationMessage.conversation_id == conv.id
            )
        )
        msg_count = count_result.scalar() or 0

        # 获取最后一条消息的摘要（截取前100字符）
        last_msg_result = await db.execute(
            select(ConversationMessage.content)
            .where(ConversationMessage.conversation_id == conv.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        last_content = last_msg_result.scalar()
        last_message = last_content[:100] if last_content else None

        items.append(ConversationListItem(
            id=conv.id,
            title=conv.title,
            subject=conv.subject,
            status=conv.status,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=msg_count,
            last_message=last_message,
        ))

    return ConversationListResponse(items=items, total=len(items))


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    req: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    创建新会话

    默认标题为"新对话"，用户可指定标题和学科。
    创建成功后返回空消息列表的会话详情。
    """
    conv = Conversation(
        user_id=current_user.id,
        title=req.title or "新对话",
        subject=req.subject,
    )
    db.add(conv)
    await db.flush()  # 先刷新获取自增ID
    await db.refresh(conv)

    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        subject=conv.subject,
        status=conv.status,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=[],
        message_count=0,
        last_message=None,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取单个会话详情，包含完整消息历史

    消息按 created_at 升序排列，前端可直接渲染对话界面。
    权限校验：只能查看自己的会话。
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    if conv.status == 0:
        raise HTTPException(status_code=404, detail="会话已删除")

    # 查询消息列表（按时间升序）
    msgs_result = await db.execute(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conv.id)
        .order_by(ConversationMessage.created_at.asc())
    )
    messages = msgs_result.scalars().all()

    msg_list = [
        ConversationMessageResponse(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            reasoning=m.reasoning,
            tool_calls=m.tool_calls,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]

    last_content = msg_list[-1].content[:100] if msg_list else None

    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        subject=conv.subject,
        status=conv.status,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=msg_list,
        message_count=len(msg_list),
        last_message=last_content,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    req: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    更新会话信息（如修改标题）

    权限校验：只能修改自己的会话。
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.status == 1,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 仅更新传入的字段
    if req.title is not None:
        conv.title = req.title
    if req.subject is not None:
        conv.subject = req.subject

    await db.flush()
    await db.refresh(conv)

    return await get_conversation(conversation_id, db, current_user)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    软删除会话（将 status 设为 0）

    数据不会物理删除，保留在数据库中便于后续数据分析和恢复。
    权限校验：只能删除自己的会话。
    关联消息通过外键级联保留，不受影响。
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.status == 1,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")

    conv.status = 0
    await db.flush()

    return {"detail": "会话已删除", "id": conversation_id}


# ============ 消息（Message）============

@router.post("/{conversation_id}/messages", response_model=ConversationMessageResponse)
async def save_message(
    conversation_id: int,
    req: ConversationMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    向指定会话保存一条消息

    权限校验：
    1. 会话必须属于当前用户
    2. 会话必须未被删除
    保存成功后自动更新会话的 updated_at 时间戳。
    """
    # 验证会话归属权
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.status == 1,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在或已删除")

    # 创建消息记录
    msg = ConversationMessage(
        conversation_id=conversation_id,
        role=req.role,
        content=req.content,
        reasoning=req.reasoning,
        tool_calls=req.tool_calls,
    )
    db.add(msg)

    # 更新会话的 updated_at 时间戳，确保列表排序正确
    from datetime import datetime
    conv.updated_at = datetime.now()

    await db.flush()
    await db.refresh(msg)

    return ConversationMessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        reasoning=msg.reasoning,
        tool_calls=msg.tool_calls,
        created_at=msg.created_at.isoformat(),
    )


@router.post("/{conversation_id}/messages/batch")
async def save_messages_batch(
    conversation_id: int,
    messages: list[ConversationMessageCreate],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    批量保存消息到指定会话

    用于在前端关闭抽屉时一次性持久化整段对话。
    也可用于实时逐条保存后的批量补充。
    """
    # 验证会话归属权
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.status == 1,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在或已删除")

    saved_ids = []
    for req in messages:
        msg = ConversationMessage(
            conversation_id=conversation_id,
            role=req.role,
            content=req.content,
            reasoning=req.reasoning,
            tool_calls=req.tool_calls,
        )
        db.add(msg)
        await db.flush()
        saved_ids.append(msg.id)

    # 更新会话时间戳
    from datetime import datetime
    conv.updated_at = datetime.now()

    await db.flush()
    return {"detail": f"已保存 {len(saved_ids)} 条消息", "ids": saved_ids}
