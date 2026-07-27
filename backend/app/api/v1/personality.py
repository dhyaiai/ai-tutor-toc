"""
助教性格配置 API

提供用户自定义微调配置（性格类型/说话风格/评分严格度）的查询与更新。
配置保存后实时生效，对系统内所有 AI 批改和 Agent 对话统一生效。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.personality import AgentPersonality
from app.schemas.personality import (
    PersonalityUpdateRequest,
    PersonalityResponse,
)
from app.services.personality_service import DEFAULT_PERSONALITY

router = APIRouter(prefix="/personality", tags=["personality"])


@router.get("", response_model=PersonalityResponse)
async def get_personality(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的助教配置，不存在则返回默认配置"""
    result = await db.execute(
        select(AgentPersonality).where(AgentPersonality.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        # 返回默认配置
        return PersonalityResponse(
            id=0,
            user_id=current_user.id,
            **DEFAULT_PERSONALITY,
            update_time=None,
        )
    return PersonalityResponse(
        id=config.id,
        user_id=config.user_id,
        personality_type=config.personality_type,
        speaking_style=config.speaking_style,
        strict_level=config.strict_level,
        update_time=config.update_time.isoformat() if config.update_time else None,
    )


@router.put("", response_model=PersonalityResponse)
async def update_personality(
    req: PersonalityUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新用户的助教配置（upsert）"""
    result = await db.execute(
        select(AgentPersonality).where(AgentPersonality.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()

    if config:
        # 更新已有记录
        if req.personality_type is not None:
            config.personality_type = req.personality_type
        if req.speaking_style is not None:
            config.speaking_style = req.speaking_style
        if req.strict_level is not None:
            config.strict_level = req.strict_level
    else:
        # 创建新记录
        config = AgentPersonality(
            user_id=current_user.id,
            personality_type=req.personality_type or DEFAULT_PERSONALITY["personality_type"],
            speaking_style=req.speaking_style or DEFAULT_PERSONALITY["speaking_style"],
            strict_level=req.strict_level or DEFAULT_PERSONALITY["strict_level"],
        )
        db.add(config)

    await db.flush()
    await db.refresh(config)

    return PersonalityResponse(
        id=config.id,
        user_id=config.user_id,
        personality_type=config.personality_type,
        speaking_style=config.speaking_style,
        strict_level=config.strict_level,
        update_time=config.update_time.isoformat() if config.update_time else None,
    )
