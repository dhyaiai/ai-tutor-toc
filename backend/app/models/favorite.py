"""我的收藏模型：用户收藏错题 / AI 生成题目"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # error=错题（指向 assignment_questions.id）；ai=AI生成题（指向 ai_generated_questions.id）
    # 注意：question_id 同时指向两张 id 空间独立的表，无法加跨表外键，存在性由应用层校验
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="收藏类型：error=错题, ai=AI题")
    question_id: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="锚点题ID：error=父题或独立题id；ai=独立题id或大题sub_question_index最小子题id",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    # 同一用户对同一题最多收藏一次（幂等约束）
    __table_args__ = (UniqueConstraint("user_id", "item_type", "question_id", name="uq_user_favorites"),)
