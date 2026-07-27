"""AI 生成题目及作答记录模型"""

from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class AIGeneratedQuestion(Base):
    __tablename__ = "ai_generated_questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    source_question_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assignment_questions.id"), nullable=True
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True, comment="完整解析（解题思路、步骤、依据）")
    question_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    knowledge_point: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 大题分组字段（group_id 非空表示属于某个大题）
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True, comment="大题分组ID（UUID），同组子题共享")
    sub_question_index: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="子题序号（从0开始）")
    question_context: Mapped[str | None] = mapped_column(Text, nullable=True, comment="大题背景材料")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    answers = relationship("AIQuestionAnswer", back_populates="question", cascade="all, delete-orphan")


class AIQuestionAnswer(Base):
    __tablename__ = "ai_question_answers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_generated_questions.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    selected_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    full_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct_answer_revealed: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    question = relationship("AIGeneratedQuestion", back_populates="answers")
