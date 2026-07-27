"""
会话管理相关的 Pydantic Schema 定义

包含会话创建、响应、消息保存等请求/响应模型。
所有日期字段使用 ISO 格式字符串返回，方便前端消费。
"""

from pydantic import BaseModel, Field
from datetime import datetime


# ============ 会话（Conversation）============

class ConversationCreate(BaseModel):
    """
    创建新会话的请求体

    title 和 subject 均为可选，后端会给默认标题"新对话"
    """
    title: str | None = Field(default="新对话", max_length=128, description="会话标题")
    subject: str | None = Field(default=None, max_length=32, description="关联学科")


class ConversationUpdate(BaseModel):
    """
    更新会话信息的请求体（如修改标题）
    """
    title: str | None = Field(default=None, max_length=128, description="新标题")
    subject: str | None = Field(default=None, max_length=32, description="关联学科")


class ConversationResponse(BaseModel):
    """
    会话详情响应体，包含消息列表

    messages 按 created_at 升序排列，可直接用于重建对话界面
    """
    id: int
    title: str
    subject: str | None = None
    status: int
    created_at: str
    updated_at: str
    messages: list["ConversationMessageResponse"] = []
    # 快捷字段：消息总数和最后一条消息摘要，方便列表页展示
    message_count: int = 0
    last_message: str | None = None

    class Config:
        from_attributes = True


class ConversationListItem(BaseModel):
    """
    会话列表项响应体（不含完整消息，仅含摘要信息，减少传输量）
    """
    id: int
    title: str
    subject: str | None = None
    status: int
    created_at: str
    updated_at: str
    message_count: int
    last_message: str | None = None

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """会话列表分页响应"""
    items: list[ConversationListItem] = []
    total: int = 0


# ============ 消息（ConversationMessage）============

class ConversationMessageCreate(BaseModel):
    """
    保存消息到会话的请求体

    支持两种角色：
    - user: 用户消息
    - assistant: AI 回复（含可选的 reasoning 和 tool_calls）
    """
    role: str = Field(..., pattern="^(user|assistant)$", description="消息角色")
    content: str = Field(..., min_length=1, description="消息文本内容")
    reasoning: str | None = Field(default=None, description="AI思考过程（可选）")
    tool_calls: list[str] | None = Field(default=None, description="AI调用的工具列表")


class ConversationMessageResponse(BaseModel):
    """
    消息记录响应体
    """
    id: int
    conversation_id: int
    role: str
    content: str
    reasoning: str | None = None
    tool_calls: list[str] | None = None
    created_at: str

    class Config:
        from_attributes = True
