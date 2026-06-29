from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base
import enum


class QuestionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CONFIRMED = "confirmed"  # 保留以兼容历史数据，新流程不再使用


class AnalysisTaskType(str, enum.Enum):
    FULL_ANALYSIS = "full_analysis"
    REANALYSIS = "reanalysis"
    SIMILAR_GENERATION = "similar_generation"
    VECTORIZATION = "vectorization"


class AnalysisTaskStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Question(Base):
    __tablename__ = "assignment_questions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(Integer, ForeignKey("assignments.id"), nullable=False)
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    student_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    full_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    knowledge_points: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    common_mistakes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 学生可能犯的典型错误列表
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    answer_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 学生答案图片存储路径
    manual_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 人工审核备注
    page_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bbox_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_h: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[QuestionStatus] = mapped_column(
        SAEnum(QuestionStatus), default=QuestionStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    # 大题套小题：父子层级
    # parent_id = NULL 且无子题 → 普通独立题；parent_id = NULL 且有子题 → 大题父记录（容器）
    # parent_id = X → 大题X的第N个子题
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assignment_questions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sub_question_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 自引用关系：父题 ↔ 子题
    parent: Mapped[Optional["Question"]] = relationship(
        "Question", remote_side="Question.id", back_populates="children"
    )
    children: Mapped[List["Question"]] = relationship(
        "Question", back_populates="parent", cascade="all, delete-orphan"
    )

    assignment = relationship("Assignment", back_populates="questions")


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(Integer, ForeignKey("assignments.id"), nullable=False)
    question_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("assignment_questions.id"), nullable=True)
    type: Mapped[AnalysisTaskType] = mapped_column(SAEnum(AnalysisTaskType), nullable=False)
    status: Mapped[AnalysisTaskStatus] = mapped_column(
        SAEnum(AnalysisTaskStatus), default=AnalysisTaskStatus.PENDING, nullable=False
    )
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    assignment = relationship("Assignment")
    question = relationship("Question")
