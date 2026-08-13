"""助教性格配置 Schema"""

from pydantic import BaseModel, Field
from typing import Literal

# 支持的性格类型列表（与 prompt 模板一一对应，非法值会导致下游 prompt 无法识别）
PERSONALITY_TYPES = ["温柔鼓励型", "严谨专业型", "幽默活泼型", "严格督学型"]
PersonalityTypeLiteral = Literal["温柔鼓励型", "严谨专业型", "幽默活泼型", "严格督学型"]


class PersonalityUpdateRequest(BaseModel):
    """更新配置请求（自定义微调：性格类型/说话风格/语音音色/评分严格度）"""
    personality_type: PersonalityTypeLiteral | None = None
    speaking_style: str | None = Field(None, max_length=64, description="说话风格描述")
    voice_tone: Literal["male", "female"] | None = None
    strict_level: int | None = Field(default=None, ge=1, le=5)


class PersonalityResponse(BaseModel):
    """配置响应"""
    id: int
    user_id: int
    personality_type: str
    speaking_style: str
    voice_tone: str
    strict_level: int
    update_time: str | None = None

    model_config = {"from_attributes": True}
