from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings, Settings
from app.core.deps import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # try/except 防御：数据库里存了畸形 bcrypt 哈希时（如历史脏数据），
    # passlib 会抛异常而不是返回 False，若不拦截会导致登录接口 500。
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: int,
    settings: Settings | None = None,
    token_version: int = 0,
) -> str:
    if settings is None:
        settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
        # 登录版本号：校验时与 users.token_version 比对，版本号落后 → 401（已被新设备踢下线）
        "version": token_version,
    }
    _sk = settings.SECRET_KEY
    assert _sk, "SECRET_KEY must be configured before creating tokens"
    return jwt.encode(payload, _sk, algorithm=settings.ALGORITHM)


def create_refresh_token(
    user_id: int,
    jti: str,
    settings: Settings | None = None,
    token_version: int = 0,
) -> str:
    if settings is None:
        settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
        "jti": jti,
        "version": token_version,
    }
    _sk = settings.SECRET_KEY
    assert _sk, "SECRET_KEY must be configured before creating tokens"
    return jwt.encode(payload, _sk, algorithm=settings.ALGORITHM)


def decode_token(token: str, settings: Settings | None = None) -> dict:
    if settings is None:
        settings = get_settings()
    _sk = settings.SECRET_KEY
    assert _sk, "SECRET_KEY must be configured before decoding tokens"
    return jwt.decode(token, _sk, algorithms=[settings.ALGORITHM])


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User

    try:
        payload = decode_token(token, settings)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # 防御：sub 非数字时 int() 会抛 ValueError → 500，这里统一收敛为 401
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await db.get(User, user_id_int)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # 单设备登录校验（可用 settings.SINGLE_DEVICE_LOGIN=false 关闭，允许多设备共存）：
    # token 里的版本号必须等于当前登录版本号。版本号落后（新设备登录后旧设备）→ 401，
    # 前端自动刷新失败后踢回登录页。兼容老 token（签发时无 version 字段，读默认 0）。
    if settings.SINGLE_DEVICE_LOGIN:
        token_version = payload.get("version", 0)
        if token_version != user.token_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="账号已在其他设备登录，请重新登录",
            )

    return user


async def get_current_admin(current_user=Depends(get_current_user)):
    """管理员权限校验依赖：仅 role=admin（超级管理员）可访问，否则返回 403。

    复用 get_current_user 的查库结果（完整 ORM 对象，.role 实时读库），
    角色变更无需重新签发 token 即生效。
    """
    from app.models.user import UserRole

    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作")
    return current_user
