from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatContext(BaseModel):
    grade: str | None = None
    subject: str | None = None


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    context: ChatContext | None = None
