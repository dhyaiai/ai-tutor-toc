"""
口语测评数据模型（3张表）

- listening_tests: 英语听力测试记录
- dictation_tasks: 单词听写任务记录
- mandarin_test_records: 普通话测评记录
"""

from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ListeningTest(Base):
    """英语听力测试记录"""
    __tablename__ = "listening_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="题型：短对话/长对话/短文理解/听写填空")
    difficulty: Mapped[str] = mapped_column(String(16), default="中等", comment="难度")
    question_count: Mapped[int] = mapped_column(Integer, default=5)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    user_score: Mapped[float] = mapped_column(Float, default=0.0)
    strict_level: Mapped[int] = mapped_column(Integer, default=3)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DictationTask(Base):
    """单词听写任务记录"""
    __tablename__ = "dictation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word_scope: Mapped[str] = mapped_column(String(128), nullable=False, comment="单词范围")
    word_count: Mapped[int] = mapped_column(Integer, default=10)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    strict_level: Mapped[int] = mapped_column(Integer, default=3)
    play_speed: Mapped[str] = mapped_column(String(16), default="正常", comment="播放速度")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MandarinTestRecord(Base):
    """普通话测评记录"""
    __tablename__ = "mandarin_test_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_level: Mapped[str] = mapped_column(String(16), nullable=False, comment="目标等级")
    test_part: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="测试分项")
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    dimension_scores: Mapped[str | None] = mapped_column(Text, nullable=True, comment="各维度得分JSON")
    suggestions: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    strict_level: Mapped[int] = mapped_column(Integer, default=3)
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class OralRecord(Base):
    """口语测评作业记录（统一表）

    学生每在任一子模块（英语听力/单词听写/普通话测评）提交一次，就生成一条记录。
    名称为“类别+年月日”，同一天多次则在名称后面加序号。
    """
    __tablename__ = "oral_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="英语听力/单词听写/普通话测评",
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="作业名称")
    score: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="成绩摘要")
    grade_level: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="学段：小学/初中/高中"
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="详情JSON")
    create_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
