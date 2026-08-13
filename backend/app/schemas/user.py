import re
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from app.models.user import UserRole


class UserLogin(BaseModel):
    """登录请求：使用手机号 + 密码"""
    phone: str = Field(..., description="手机号（登录账号）")
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: int
    phone: str          # 手机号（登录账号）
    username: str | None = None  # 显示名称（可选，无则前端显示手机号）
    role: UserRole  # 用户角色（admin=超级管理员/user=普通用户），前端据此控制"账号设置"入口


class UserCreate(BaseModel):
    """超级管理员创建用户请求：手机号必填，用户名选填（不填则显示手机号）"""

    phone: str = Field(..., min_length=11, max_length=11, description="中国大陆手机号（登录账号）")
    username: str | None = Field(None, max_length=64, description="用户名（显示名称，选填）")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        # 校验中国大陆手机号：1 开头 + 第二位 3-9 + 9 位数字，共 11 位
        v = v.strip()
        if not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("手机号格式不正确，请输入中国大陆手机号（1 开头共 11 位数字）")
        return v

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        return v if v else None  # 空字符串视为 None


class UserUpdate(BaseModel):
    """超级管理员编辑用户请求（全部字段可选，只更新传入的非 None 字段）"""

    phone: str | None = Field(
        None, min_length=11, max_length=11, description="新手机号（登录账号）"
    )
    username: str | None = Field(
        None, max_length=64, description="新用户名（显示名称）"
    )
    password: str | None = Field(
        None, min_length=8, max_length=64, description="新密码（至少 8 位），不传则不修改"
    )
    role: UserRole | None = Field(None, description="目标角色：admin=超级管理员 / user=普通用户")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("手机号格式不正确，请输入中国大陆手机号（1 开头共 11 位数字）")
        return v

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        return v if v else None


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    phone: str                    # 手机号（登录账号）
    username: str | None = None   # 用户名（显示名称，可能为空）
    email: str | None
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreateResponse(UserResponse):
    """创建用户响应：额外返回部分遮蔽的初始密码（仅创建时返回一次）。"""
    initial_password: str = Field(..., description="部分遮蔽的初始密码（仅创建时返回一次）")
    full_password: str | None = Field(None, description="完整初始密码（仅创建成功时返回一次，请妥善保存）")


class ChangePasswordRequest(BaseModel):
    """用户自助修改密码请求。"""
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=8, max_length=64, description="新密码（至少 8 位）")

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """校验密码强度：至少 8 位，包含字母和数字。"""
        if not re.search(r"[a-zA-Z]", v):
            raise ValueError("密码必须包含至少一个字母")
        if not re.search(r"\d", v):
            raise ValueError("密码必须包含至少一个数字")
        return v
