"""
助教性格配置模型

存储用户个性化 AI 助教配置：
- 4 套预设模板（温柔鼓励型/严谨专业型/幽默活泼型/严格督学型）
- 支持用户自定义微调各项参数
- 配置全局生效，影响所有 AI 交互的语气、评分尺度、讲解风格
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class AgentPersonality(Base):
    """
    用户助教性格配置表

    每个用户最多一条记录（user_id 唯一）。
    strict_level 控制所有批改/测评的评严格度（1-5）。
    """
    __tablename__ = "agent_personality"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True,
        comment="用户ID（唯一）"
    )
    template_name: Mapped[str] = mapped_column(
        String(32), nullable=False, default="严谨专业型",
        comment="当前使用的预设模板名称"
    )
    personality_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="严谨专业型",
        comment="性格类型：温柔鼓励型/严谨专业型/幽默活泼型/严格督学型"
    )
    speaking_style: Mapped[str] = mapped_column(
        String(32), nullable=False, default="书面化正式",
        comment="说话风格：口语化亲切/书面化正式/简洁高效"
    )
    voice_tone: Mapped[str] = mapped_column(
        String(32), nullable=False, default="female",
        comment="语音音色：male/female（历史遗留中文值视为女声），对助教讲解/英语听力/单词听写的 TTS 播报生效"
    )
    strict_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
        comment="评分严格度 1-5，越高越严格"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False,
        comment="配置更新时间"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "template_name": self.template_name,
            "personality_type": self.personality_type,
            "speaking_style": self.speaking_style,
            "voice_tone": self.voice_tone,
            "strict_level": self.strict_level,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }
