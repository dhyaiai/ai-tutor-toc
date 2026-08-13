from datetime import datetime
from sqlalchemy import String, DateTime, Enum as SAEnum, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base
import enum


class UserRole(str, enum.Enum):
    USER = "user"   # 普通用户（原"教师"角色，2026-08 统一改为普通用户）
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 手机号：登录账号，必填，唯一索引
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    # 用户名：显示名称，选填（不填时前端显示手机号）
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    # 登录版本号：每次登录原子 +1 并写入 token，旧 token 版本号落后即失效。
    # 用于实现"同一账号同时只能一台设备登录"（新登录踢掉旧设备）
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False
    )

    @property
    def display_name(self) -> str:
        """显示名称：优先返回用户名，无则返回手机号"""
        return self.username or self.phone

    assignments = relationship("Assignment", back_populates="creator", cascade="all, delete-orphan")
    # AI 生成题目级联删除：删除用户时一并清掉其生成的题目及作答记录
    # （数据库层面无 ON DELETE CASCADE，必须通过 ORM relationship 级联，否则外键约束报错）
    ai_questions = relationship("AIGeneratedQuestion", back_populates="user", cascade="all, delete-orphan")
