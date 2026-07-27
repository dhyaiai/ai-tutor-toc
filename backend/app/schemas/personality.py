"""助教性格配置 Schema"""

from pydantic import BaseModel, Field


class PersonalityUpdateRequest(BaseModel):
    """更新配置请求（自定义微调：性格类型/说话风格/评分严格度）"""
    personality_type: str | None = None
    speaking_style: str | None = None
    strict_level: int | None = Field(default=None, ge=1, le=5)


class PersonalityResponse(BaseModel):
    """配置响应"""
    id: int
    user_id: int
    personality_type: str
    speaking_style: str
    strict_level: int
    update_time: str | None = None

    class Config:
        from_attributes = True
