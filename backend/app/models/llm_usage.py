"""
LLM Token 用量日志模型

记录系统内每一次大模型调用的 Token 消耗（输入/输出/总量），
由 llm_usage_tracker 在 chat.completions.create 调用后自动写入，
为数据看板提供日均 Token 消耗量与日调用量统计数据源。
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class LlmUsageLog(Base):
    """
    LLM 调用用量日志表

    每次 chat.completions.create 调用产生一条记录。
    流式调用若上游未返回 usage，则 Token 字段为 0，但仍计入调用量。
    """
    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="调用的模型名称"
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="输入 Token 数"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="输出 Token 数"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="总 Token 数"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False, index=True,
        comment="调用时间"
    )
