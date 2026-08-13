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
        # ON DELETE SET NULL：删除原题（作业/题目删除）时自动置空引用，
        # 否则 MySQL 外键 RESTRICT 会拒绝删除整张作业（接口 500）
        Integer, ForeignKey("assignment_questions.id", ondelete="SET NULL"), nullable=True
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True, comment="完整解析（解题思路、步骤、依据）")
    question_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 题目来源：upload=自有试题（上传转录）；NULL/'ai'=AI 生成。
    # 上传转录与 AI 生成共用本表，靠此列区分（收藏页"题目来源"筛选依据）
    source: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="题目来源：upload=自有试题(上传转录), NULL/ai=AI生成"
    )
    # 上传试题转录的自有元数据（grade/subject/semester 三列：上传时写用户表单值；
    # 历史 AI 题恒为 NULL，筛选时回落 source_question_id 关联原作业的元数据）
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="年级（上传题自有元数据）")
    subject: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="科目（上传题自有元数据）")
    semester: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="学期（上传题自有元数据）")
    knowledge_point: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 大题分组字段（group_id 非空表示属于某个大题）
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True, comment="大题分组ID（UUID），同组子题共享")
    sub_question_index: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="子题序号（从0开始）")
    question_context: Mapped[str | None] = mapped_column(Text, nullable=True, comment="大题背景材料")
    # 题目配图（纯 SVG 代码，AI 同类题生成时随题产出；无图时为空）
    image_svg: Mapped[str | None] = mapped_column(Text, nullable=True, comment="题目配图SVG代码")
    # 大题背景材料的配图（纯 SVG 代码）
    context_image_svg: Mapped[str | None] = mapped_column(Text, nullable=True, comment="大题背景材料配图SVG代码")
    # 原题图像存储标识（上传转录的自有试题：图片原文件或扫描 PDF 渲染首页；
    # 存储标识经预签名后返回可访问 URL，供编辑弹窗对照原图使用）
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="原题图像存储标识（上传转录）")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    answers = relationship("AIQuestionAnswer", back_populates="question", cascade="all, delete-orphan")
    # 反向关联用户（配合 User.ai_questions 的删除级联）
    user = relationship("User", back_populates="ai_questions")


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
