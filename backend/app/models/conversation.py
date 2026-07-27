"""
会话（Conversation）与消息（ConversationMessage）数据模型

支持悬浮聊天抽屉的多会话管理：
- 每个用户可以有多个会话
- 每个会话包含多条消息（对话记录）
- 删除采用软删除（status=deleted），数据不物理销毁
"""

from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, JSON, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class Conversation(Base):
    """
    会话表 - 记录用户与AI助教的对话会话

    字段说明：
    - user_id: 所属用户ID，建立索引便于按用户快速查询
    - title: 会话标题，默认为"新对话"，可由用户或AI自动更新
    - subject: 关联学科（可选），如"数学"、"英语"，方便按学科筛选
    - status: 状态标记（1=正常，0=已删除），软删除保留数据可追溯
    - created_at/updated_at: 创建和最后活跃时间，用于排序和展示
    """
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新对话")
    subject: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, comment="状态：1=正常，0=已删除")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    # 关联消息列表，按时间正序排列
    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        order_by="ConversationMessage.created_at",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        """转换为前端友好的字典格式"""
        return {
            "id": self.id,
            "title": self.title,
            "subject": self.subject,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationMessage(Base):
    """
    会话消息表 - 记录会话中的每条对话

    字段说明：
    - conversation_id: 所属会话ID，外键关联 conversations 表
    - role: 消息角色（"user"=用户提问，"assistant"=AI回答）
    - content: 消息正文文本
    - reasoning: AI的思考过程（可选），记录 ReAct 推理链
    - tool_calls: AI调用的工具列表（可选），JSON数组格式，如 ["search_knowledge", "generate_report"]
    - created_at: 消息创建时间，用于按时间排序展示
    """
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="消息角色：user/assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True, comment="AI思考过程（可选）")
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="AI调用的工具列表")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    # 反向关联回会话
    conversation = relationship("Conversation", back_populates="messages")

    def to_dict(self):
        """转换为前端友好的字典格式"""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "reasoning": self.reasoning,
            "tool_calls": self.tool_calls,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
