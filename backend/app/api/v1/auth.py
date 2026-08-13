import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.core.config import get_settings, Settings
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.deps import get_db
from app.models.user import User
from app.schemas.user import (
    UserLogin,
    TokenResponse,
    TokenRefreshRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Refresh Token 已使用 JTI 短期内存黑名单（单次使用机制）。
# 记录最近 24 小时内使用过的 JTI，防止 Refresh Token 被截获后重放。
# key=jti, value=timestamp
# 注意：生产多 worker 部署时，应迁移到 Redis（REDIS_URL 已配置），否则各进程独立计数。
# TODO(#security): 多 worker 部署前迁移到 redis，key=refresh_jti:{jti}, TTL=86400
_used_refresh_jtis: dict[str, float] = {}
_MAX_JTI_CACHE_SIZE = 10000  # 防止内存无限增长（最多缓存 10000 个 JTI）

# 登录限流（双层）：
# 1. 按 IP 维度：防爆破，key=ip, value=[失败时间戳列表]
# 2. 按账号维度：用递增延迟替代硬锁，防锁定 DoS
# 注意：生产多 worker 部署时，应迁移到 Redis，否则各进程独立计数。
# TODO(#security): 多 worker 部署前迁移到 redis，key=login_attempts:{ip/phone}, TTL=1800
_login_attempts_ip: dict[str, list[float]] = {}
_login_attempts_phone: dict[str, list[float]] = {}
_LOGIN_MAX_ATTEMPTS_IP = 10      # 单 IP 5 分钟内最多 10 次失败
_LOGIN_MAX_ATTEMPTS_PHONE = 20   # 单账号 30 分钟内最多 20 次失败
_LOGIN_LOCKOUT_SECONDS_IP = 300  # IP 锁定 5 分钟
_LOGIN_LOCKOUT_SECONDS_PHONE = 1800  # 账号锁定 30 分钟
_MAX_LOGIN_ATTEMPTS_SIZE = 5000  # 防止内存无限增长

# 注：注册功能已移除，开户统一由超级管理员的"账号设置"完成（POST /users）

# 用户不存在时执行 dummy verify 用的合法 bcrypt 哈希。
# 注意：不能写死畸形字符串（如 "$2b$12$dummy..."）——passlib 对畸形哈希抛异常
# 而非返回 False，会让"账号不存在"的登录直接 500。必须在模块加载时生成一个真实哈希。
_DUMMY_HASH: str = hash_password("dummy-password-for-timing-side-channel")


def _set_access_cookie(response: Response, token: str, settings: Settings) -> None:
    """登录/刷新成功时把 access token 写入 cookie（仅 DEV 模式）。

    背景：作业切图、原图等通过 /api/v1/files/ 展示，私有目录做了登录鉴权，
    但浏览器 <img>/<audio> 标签无法附加 Authorization 头，只能靠 cookie 自动携带。
    因此 DEV 模式下登录/刷新时同步种下 access_token cookie，file_server 从 cookie 读取。
    不设 HttpOnly（与 localStorage 存 token 的安全等级相同，便于前端登出时清理）。

    注意：access token 有效期 30 分钟（ACCESS_TOKEN_EXPIRE_MINUTES），cookie 过期时间
    与其保持一致；页面停留超过有效期后图片会 401，刷新页面时前端自动刷新 token
    并重新种 cookie 即可恢复。
    """
    if not settings.DEV_MODE:
        return
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        path="/",
    )


def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP。

    TRUST_PROXY_HEADERS=false（默认）：直接使用 request.client.host，不信任客户端
    可伪造的 X-Forwarded-For 头（防止攻击者旋转 XFF 绕过限流、或借他人 IP 填充 XFF
    把任意 IP 锁死）。部署在可信反向代理后时设 TRUST_PROXY_HEADERS=true，
    此时取 XFF 最右侧一段（最后一级代理转发来的 IP）视为真实客户端。
    """
    if get_settings().TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # X-Forwarded-For 格式：client, proxy1, proxy2 —— 取最右侧（最接近代理）一段
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[-1]
    if request.client:
        return request.client.host
    return "unknown"


def _check_rate_limit_ip(client_ip: str) -> None:
    """检查 IP 维度限流，被限流时抛出 HTTP 429"""
    now = time.time()
    attempts = _login_attempts_ip.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _LOGIN_LOCKOUT_SECONDS_IP]
    _login_attempts_ip[client_ip] = attempts
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS_IP:
        raise HTTPException(
            status_code=429,
            detail=f"该 IP 登录尝试次数过多，请 {_LOGIN_LOCKOUT_SECONDS_IP // 60} 分钟后重试",
        )


def _check_rate_limit_phone(phone: str) -> None:
    """检查账号维度限流，被限流时抛出 HTTP 429"""
    now = time.time()
    attempts = _login_attempts_phone.get(phone, [])
    attempts = [t for t in attempts if now - t < _LOGIN_LOCKOUT_SECONDS_PHONE]
    _login_attempts_phone[phone] = attempts
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS_PHONE:
        raise HTTPException(
            status_code=429,
            detail="该账号登录尝试次数过多，请 30 分钟后重试",
        )


def _record_failed_attempt(client_ip: str, phone: str) -> None:
    """记录一次登录失败（带容量上限保护，防止内存放大 DoS）。"""
    now = time.time()

    ip_attempts = _login_attempts_ip.get(client_ip, [])
    ip_attempts.append(now)
    _login_attempts_ip[client_ip] = ip_attempts

    phone_attempts = _login_attempts_phone.get(phone, [])
    phone_attempts.append(now)
    _login_attempts_phone[phone] = phone_attempts

    # 容量保护：超过上限时清理最旧的 key（LRU 近似）
    for cache in (_login_attempts_ip, _login_attempts_phone):
        if len(cache) > _MAX_LOGIN_ATTEMPTS_SIZE:
            sorted_keys = sorted(cache.keys(), key=lambda k: max(cache[k]) if cache[k] else 0)
            for key in sorted_keys[:_MAX_LOGIN_ATTEMPTS_SIZE // 5]:
                cache.pop(key, None)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    data: UserLogin,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    response: Response = None,
):
    # 双层限流：IP 维度（防爆破）+ 账号维度（防锁定 DoS）
    client_ip = _get_client_ip(request)
    _check_rate_limit_ip(client_ip)
    _check_rate_limit_phone(data.phone)

    result = await db.execute(select(User).where(User.phone == data.phone))
    user = result.scalar_one_or_none()

    # 用户不存在时也执行一次 dummy verify，消除用户枚举时序侧信道
    if not user:
        verify_password(data.password, _DUMMY_HASH)
        _record_failed_attempt(client_ip, data.phone)
        raise HTTPException(status_code=401, detail="账号或密码错误，请重试")

    if not verify_password(data.password, user.hashed_password):
        _record_failed_attempt(client_ip, data.phone)
        raise HTTPException(status_code=401, detail="账号或密码错误，请重试")

    # 单设备登录（settings.SINGLE_DEVICE_LOGIN=false 时跳过版本号递增，允许多设备共存）：
    # 登录版本号原子 +1（并发登录时各自拿到递增后的不同值），
    # 旧设备已签发的 token 版本号落后 → 后续请求 401，实现"新登录踢掉旧设备"
    if settings.SINGLE_DEVICE_LOGIN:
        await db.execute(
            update(User).where(User.id == user.id).values(token_version=User.token_version + 1)
        )
        await db.refresh(user)  # 读回最新版本号，用于签发新 token

    access_token = create_access_token(user.id, settings, token_version=user.token_version)
    jti = str(uuid.uuid4())
    refresh_token = create_refresh_token(user.id, jti, settings, token_version=user.token_version)

    # DEV 模式：种 access_token cookie，让 <img>/<audio> 能自动携带凭证加载私有文件
    _set_access_cookie(response, access_token, settings)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        phone=user.phone,
        username=user.username,
        role=user.role,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    response: Response = None,
):
    try:
        payload = decode_token(data.refresh_token, settings)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Refresh Token 单次使用校验：检查 JTI 是否已被使用过（防重放攻击）
    # 使用 setdefault 原子操作占位，避免检查-写入之间的 TOCTOU 竞态
    # （两个并发请求携带同一 refresh token 时，只有一个能通过）
    jti = payload.get("jti")
    now = time.time()
    if jti:
        if _used_refresh_jtis.setdefault(jti, now) is not now:
            raise HTTPException(status_code=401, detail="Refresh token 已被使用，请重新登录")

    user = await db.get(User, int(user_id))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # 单设备登录（settings.SINGLE_DEVICE_LOGIN=false 时不校验版本号）：
    # 被踢设备（版本号落后）的 refresh token 同样失效，
    # 前端自动刷新失败后会清空本地凭证并跳转登录页
    if settings.SINGLE_DEVICE_LOGIN:
        token_version = payload.get("version", 0)  # 兼容老 token（签发时无 version 字段）
        if token_version != user.token_version:
            raise HTTPException(status_code=401, detail="账号已在其他设备登录，请重新登录")

    # 清理超过 24 小时的过期黑名单条目（避免内存无限增长）
    if jti:
        cutoff = time.time() - 86400
        expired = [k for k, v in _used_refresh_jtis.items() if v < cutoff]
        for k in expired:
            del _used_refresh_jtis[k]
        # 容量保护：仍超上限时移除最旧的条目
        if len(_used_refresh_jtis) > _MAX_JTI_CACHE_SIZE:
            sorted_items = sorted(_used_refresh_jtis.items(), key=lambda x: x[1])
            for k, _ in sorted_items[:len(sorted_items) - _MAX_JTI_CACHE_SIZE]:
                del _used_refresh_jtis[k]

    access_token = create_access_token(int(user_id), settings, token_version=user.token_version)
    new_jti = str(uuid.uuid4())
    new_refresh_token = create_refresh_token(int(user_id), new_jti, settings, token_version=user.token_version)

    # DEV 模式：刷新后同步更新 cookie（旧 token 30 分钟失效，不更新则图片/音频 401）
    _set_access_cookie(response, access_token, settings)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user_id=int(user_id),
        phone=user.phone,
        username=user.username,
        role=user.role,
    )
