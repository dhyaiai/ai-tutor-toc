"""
用户知识状态追踪模型

维护跨会话的用户知识点掌握画像：
- 每个知识点独立记录掌握度（0-100分）
- 自动计算掌握等级（未掌握/初步掌握/熟练掌握/精通）
- 累计错误次数和最近练习时间，用于教学策略调整

覆盖场景：作业批改、题目讲解、口语测评、作文批改、错题订正
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class UserKnowledgeState(Base):
    """
    用户知识点掌握状态表

    设计要点：
    - (user_id, subject, point_name) 组合唯一，同一知识点同一学科只存一条记录
    - mastery_score 范围 0-100，由 update 操作累积调整
    - mastery_level 由 mastery_score 自动计算：0-30未掌握, 31-60初步掌握, 61-85熟练掌握, 86-100精通
    - wrong_count 累计错误次数，用于判断薄弱点优先级
    - last_practice_time 和 update_time 分开记录，前者记录实际练习时间，后者记录更新时间
    """
    __tablename__ = "user_knowledge_state"
    __table_args__ = (
        UniqueConstraint("user_id", "subject", "point_name", name="uq_knowledge_state_user_subject_point"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="所属用户ID"
    )
    subject: Mapped[str] = mapped_column(
        String(32), nullable=False, default="通用",
        comment="学科：数学/英语/语文等"
    )
    point_name: Mapped[str] = mapped_column(
        String(128), nullable=False,
        comment="知识点或能力维度名称"
    )
    mastery_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50,
        comment="熟练度分数 0-100"
    )
    mastery_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="初步掌握",
        comment="掌握等级：未掌握/初步掌握/熟练掌握/精通"
    )
    wrong_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="累计错误次数"
    )
    correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="累计正确次数"
    )
    last_practice_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
        comment="最近一次练习时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False,
        comment="记录更新时间"
    )

    @staticmethod
    def calc_mastery_level(score: int) -> str:
        """
        根据熟练度分数计算掌握等级

        等级划分（对齐规格文档4.4节）：
        - 0-30: 未掌握 → 从基础概念讲起，搭配基础题
        - 31-60: 初步掌握 → 侧重方法应用，搭配中档题
        - 61-85: 熟练掌握 → 侧重综合应用，搭配变式题
        - 86-100: 精通 → 拓展拔高，搭配压轴题
        """
        if score <= 30:
            return "未掌握"
        elif score <= 60:
            return "初步掌握"
        elif score <= 85:
            return "熟练掌握"
        else:
            return "精通"

    def to_dict(self) -> dict:
        """转换为前端友好的字典格式"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subject": self.subject,
            "point_name": self.point_name,
            "mastery_score": self.mastery_score,
            "mastery_level": self.mastery_level,
            "wrong_count": self.wrong_count,
            "correct_count": self.correct_count,
            "last_practice_time": self.last_practice_time.isoformat() if self.last_practice_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }
