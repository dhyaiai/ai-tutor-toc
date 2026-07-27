from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatContext(BaseModel):
    grade: str | None = None
    subject: str | None = None


class ChatRequest(BaseModel):
    """
    AI 助教聊天请求体

    - message: 用户当前输入的消息文本
    - history: 最近的对话历史（由前端传入，用于构建 LLM 上下文）
    - context: 可选的年级/学科上下文信息
    - session_id: 可选的会话ID，用于关联消息到指定会话
                  传入后后端会在聊天过程中自动保存消息到对应会话
    """
    message: str
    history: list[ChatMessage] = []
    context: ChatContext | None = None
    session_id: int | None = None
