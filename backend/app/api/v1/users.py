"""用户管理路由（仅超级管理员可用）。

注册功能已取消，新增账号只能由超级管理员（role=admin）在此创建：
- 手机号必填（作为登录账号）
- 用户名选填（作为显示名称，不填时前端显示手机号）
- 初始密码随机生成
- 新建用户默认角色为普通用户（user）

支持编辑（修改手机号/用户名/重置密码/切换角色）与删除（级联清理该用户全部数据）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.core.security import get_current_admin, get_current_user, hash_password, verify_password
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserCreateResponse, ChangePasswordRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户自助修改密码：需校验旧密码，新密码必须满足强度要求。"""
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="旧密码错误")

    current_user.hashed_password = hash_password(data.new_password)
    # token_version +1 使其他设备的 token 失效（安全要求：改密后其他设备需重新登录）
    current_user.token_version += 1
    await db.flush()
    await db.refresh(current_user)

    # 给当前设备重新签发 token：token_version 已递增，所有旧 token 全部失效，
    # 若不重新签发，刚改完密码的当前会话也会在下一次请求被 401 踢下线
    # （刚改完密码立刻被登出是明显体验缺陷）。其他设备的 token 不受影响地全部失效。
    import uuid
    from app.core.security import create_access_token, create_refresh_token
    access_token = create_access_token(current_user.id, token_version=current_user.token_version)
    jti = str(uuid.uuid4())
    refresh_token = create_refresh_token(
        current_user.id, jti, token_version=current_user.token_version
    )

    logger.warning(
        "[AUDIT] 用户 %s(uid=%s) 修改了自己的密码",
        current_user.phone, current_user.id,
    )
    return {
        "message": "密码修改成功",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


@router.get("/me", response_model=UserResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前登录用户信息（前端刷新页面后恢复登录态用）。

    必须注册在 /users/{user_id} 之前（本路由文件无 GET /{user_id}，无冲突；
    若未来新增需注意保持顺序），且只依赖普通登录态而非 admin 权限。
    """
    return current_user


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    """按 id 查用户，不存在时返回 404 中文提示。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在或已被删除")
    return user


async def _count_admins(db: AsyncSession) -> int:
    """统计当前超级管理员数量（用于保护最后一个管理员不被降级/删除）。"""
    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    )
    return result.scalar_one()


@router.post("", response_model=UserCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """创建新用户：手机号=登录账号，初始密码随机生成，角色=普通用户。

    响应中包含初始密码（仅返回一次），前端应提示用户首次登录后修改密码。
    """
    import secrets

    # 查重：手机号 unique 约束兜底，但这里主动检查以返回友好中文提示
    existing = await db.execute(select(User).where(User.phone == data.phone))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该手机号已注册，请更换手机号")

    # 随机初始密码（12 位字母数字，保证包含字母和数字满足改密强度要求）
    import string
    alphabet = string.ascii_letters + string.digits
    initial_password = ''.join(secrets.choice(alphabet) for _ in range(12))

    user = User(
        phone=data.phone,
        username=data.username,  # 可能为 None，前端显示时 fallback 到手机号
        email=None,
        hashed_password=hash_password(initial_password),
        role=UserRole.USER,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # 构造响应：完整密码仅返回一次，遮蔽版用于前端展示
    # 遮蔽规则：保留前 2 位和后 2 位，中间用 *** 代替（如 ab***21）
    if len(initial_password) <= 4:
        masked_password = initial_password[0] + "***" + initial_password[-1]
    else:
        masked_password = initial_password[:2] + "***" + initial_password[-2:]
    return UserCreateResponse(
        id=user.id,
        phone=user.phone,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
        initial_password=masked_password,
        full_password=initial_password,
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """用户列表（按创建时间倒序，新用户在前）。"""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """编辑用户：可修改手机号（登录账号）、用户名（显示名称）、重置密码、切换角色。

    保护规则：
    - 管理员不能修改自己的角色（防止手滑把自己降级后系统无人可管）
    - 不能降级最后一个超级管理员
    """
    user = await _get_user_or_404(db, user_id)

    # 修改手机号：先查重（unique 约束兜底，这里主动检查返回中文提示）
    # 用 model_fields_set 区分"不传"和"传 null"（PATCH 语义：传 null 表示清除）
    if "phone" in data.model_fields_set and data.phone != user.phone:
        if data.phone is None:
            raise HTTPException(status_code=400, detail="手机号不能为空")
        existing = await db.execute(select(User).where(User.phone == data.phone))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="该手机号已被其他账号使用")
        user.phone = data.phone

    # 修改用户名（显示名称）：允许设为 None（清除自定义名称，恢复显示手机号）
    if "username" in data.model_fields_set:
        user.username = data.username  # 可能是 None，表示清除自定义名称

    # 重置密码：仅当传入新密码时生效（审计日志记录操作人）
    # token_version 自增使该用户所有已签发 token 失效，防止旧 token 被攻击者继续使用
    if data.password is not None:
        user.hashed_password = hash_password(data.password)
        user.token_version += 1
        logger.warning(
            "[AUDIT] 管理员 %s(uid=%s) 重置了用户 %s(uid=%s) 的密码",
            admin.phone, admin.id, user.phone, user.id,
        )

    # 切换角色：校验保护规则后生效
    if data.role is not None and data.role != user.role:
        if user.id == admin.id:
            raise HTTPException(status_code=400, detail="不能修改自己的角色")
        if user.role == UserRole.ADMIN and await _count_admins(db) <= 1:
            raise HTTPException(status_code=400, detail="不能降级最后一个超级管理员")
        user.role = data.role

    await db.flush()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """删除用户：级联清理其全部数据（作业、AI 题目、会话、测评记录等）。

    保护规则：
    - 不能删除自己（当前登录的管理员）
    - 不能删除最后一个超级管理员
    """
    user = await _get_user_or_404(db, user_id)

    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    if user.role == UserRole.ADMIN and await _count_admins(db) <= 1:
        raise HTTPException(status_code=400, detail="不能删除最后一个超级管理员")

    # ORM 级联删除：assignments / ai_generated_questions 通过 relationship cascade 逐层清理；
    # conversations / personality / 各测评表外键自带 ON DELETE CASCADE，由数据库兜底
    logger.warning(
        "[AUDIT] 管理员 %s(uid=%s) 删除了用户 %s(uid=%s, phone=%s)",
        admin.phone, admin.id, user.username, user.id, user.phone,
    )
    await db.delete(user)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"删除失败，可能存在关联数据约束：{str(e)[:100]}")

    # 尽力清理该用户在存储中的文件（本地磁盘/MinIO），失败不阻断删除
    try:
        from app.services.file_upload import StorageService
        storage = StorageService()
        await storage.delete_user_storage(user.id)
    except Exception as e:
        logger.warning("[AUDIT] 清理用户 %d 存储文件失败: %s", user.id, e)
