from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base
import enum


class AssignmentStatus(str, enum.Enum):
    PENDING = "pending"          # 等待切割
    SPLITTING = "splitting"      # 正在切割（OCR 分题）
    SPLITTED = "splitted"        # 切割完成，等待 AI 评分
    GRADING = "grading"          # 正在 AI 分析
    PROCESSING = "processing"    # (兼容旧版) 分析中
    COMPLETED = "completed"      # 分析完成
    FAILED = "failed"            # 分析失败


class LayoutType(str, enum.Enum):
    A4_SINGLE = "a4_single"
    A4_DOUBLE = "a4_double"
    A3_DOUBLE = "a3_double"
    A3_TRIPLE = "a3_triple"
    A3_QUAD = "a3_quad"


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    grade: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    semester: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    usage_month: Mapped[str] = mapped_column(String(16), nullable=False)
    layout_type: Mapped[LayoutType] = mapped_column(
        SAEnum(LayoutType), default=LayoutType.A4_SINGLE, nullable=False
    )
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # 客观题识别区（答题卡）切图：学生将客观题答案誊写在卷首识别区时，
    # 评分时作为 [Answer Sheet] 第三图源拼入每道题，作为学生作答的优先识别来源
    answer_sheet_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        SAEnum(AssignmentStatus), default=AssignmentStatus.PENDING, nullable=False, index=True
    )
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    creator = relationship("User", back_populates="assignments")
    questions = relationship("Question", back_populates="assignment", cascade="all, delete-orphan")
    # 分析任务级联删除：删除作业（或级联删除用户）时一并清掉任务记录，
    # 避免 analysis_tasks.assignment_id 外键（无 ON DELETE CASCADE）阻止删除
    analysis_tasks = relationship("AnalysisTask", back_populates="assignment", cascade="all, delete-orphan")
