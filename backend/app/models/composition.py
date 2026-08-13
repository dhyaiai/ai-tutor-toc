"""
作文批改数据模型

composition_corrections 表：存储作文批改记录
- 支持语文/英语双学科
- 结构化分项评分（JSON 格式存储）
- 逐处修改建议（JSON 格式存储）
"""

from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class CompositionCorrection(Base):
    """
    作文批改记录表

    字段说明：
    - dimension_scores: JSON格式分项评分，语文学科={立意,结构,内容,语言}，英语学科={内容,语言,规范}
    - revision_suggestions: JSON数组，每项={position, original_text, revised_text, reason, revision_type}
    - sample_essay: AI生成的参考范文
    - pdf_url: 导出的PDF批改报告链接
    """
    __tablename__ = "composition_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    subject: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="学科：语文/英语"
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, default="未命名作文"
    )
    total_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="总分")
    full_score: Mapped[int] = mapped_column(Integer, nullable=False, default=60, comment="满分")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="作文字数（不含标点）")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="作文原文")
    requirement: Mapped[str | None] = mapped_column(Text, nullable=True, comment="写作要求")
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="年级")
    dimension_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="分项得分")
    deductions: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扣分明细：键为扣分原因，值为扣分分值")
    revision_suggestions: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="逐处修改建议")
    overall_comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="整体评价")
    polish_advice: Mapped[str | None] = mapped_column(Text, nullable=True, comment="润色建议")
    sample_essay: Mapped[str | None] = mapped_column(Text, nullable=True, comment="参考范文")
    strict_level: Mapped[int] = mapped_column(Integer, nullable=False, default=3, comment="批改严格度")
    essay_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="作文类型：读后续写/应用文/议论文等")
    pdf_url: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="PDF报告链接")
    # 批改状态机：pending(已提交待批改) → correcting(批改中) → completed(完成) / failed(失败)
    # 存量记录默认 completed（迁移时 ADD COLUMN DEFAULT 'completed'），新建记录在 API 层显式写 pending
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed", comment="状态：pending/correcting/completed/failed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="批改失败原因")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subject": self.subject,
            "title": self.title,
            "total_score": self.total_score,
            "full_score": self.full_score,
            "content": self.content,
            "requirement": self.requirement,
            "grade": self.grade,
            "dimension_scores": self.dimension_scores,
            "revision_suggestions": self.revision_suggestions,
            "overall_comment": self.overall_comment,
            "polish_advice": self.polish_advice,
            "sample_essay": self.sample_essay,
            "strict_level": self.strict_level,
            "essay_type": self.essay_type,
            "pdf_url": self.pdf_url,
            "status": self.status,
            "error_message": self.error_message,
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }
