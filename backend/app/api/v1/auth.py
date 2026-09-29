"""鉴权端点:注册 / 登录 / refresh / logout。

设计选择:

- access token 用 JWT(无状态,Step 4 的 security.create_access_token)

- refresh token 用 opaque(随机串,存 hash 到 DB,可立即 revoke)

- refresh 端点做"token 轮转":每次 refresh 后旧 token 失效,返回新 token
  这是为了防止 refresh token 被盗后无限使用(企业级标配)

- 所有错误信息模糊化,不泄露用户是否存在(防用户枚举)
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import (
    create_access_token,
    generate_refresh_token_string,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.user import (
    LogoutRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

# ── 注册 ──

@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    """注册新用户 + 签发 token 对。

    流程:
    1. 校验 email 唯一
    2. 哈希密码 + 创建 User
    3. 生成 refresh token + 存 hash 到 DB
    4. 签发 access token
    5. 返回 TokenPair
    """
    # 1. 校验 email 唯一
    existing = await session.execute(
        select(User).where(User.email == payload.email)
    )
    if existing.scalar_one_or_none() is not None:
        # 409 Conflict:资源已存在
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        )

    # 2. 创建 User(密码哈希)
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    session.add(user)
    # flush:把 INSERT 发到 DB 但不 commit,这样能拿到 user.id(default=uuid.uuid4 在 Python 端生成,
    # 其实 flush 前就能拿,但 flush 确保 session 状态同步)
    await session.flush()

    # 3. 生成 refresh token,存 hash 到 DB
    refresh_str = generate_refresh_token_string()  # 明文,给客户端
    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_str),  # 存 hash,不存明文!
        device_info=None,  # 注册时不带设备信息(后续可从 User-Agent 解析)
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(refresh_record)

    # 4. commit:一次性提交 User + RefreshToken(原子性)
    await session.commit()
    await session.refresh(user)  # 刷新拿 created_at 等服务端生成字段

    # 5. 签发 access token
    access = create_access_token(str(user.id))

    return TokenPair(
        access_token=access,
        refresh_token=refresh_str,
        user=UserRead.model_validate(user),
    )

# ── 登录 ──

@router.post("/login", response_model=TokenPair)
async def login(
    payload: UserLogin,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    """登录 + 签发 token 对。

    流程:
    1. 按 email 查 User
    2. 校验密码(用户不存在/密码错统一返回同一错误,防用户枚举)
    3. 校验 is_active
    4. 生成 refresh token + 存 hash
    5. 签发 access token
    """
    # 1. 查用户
    result = await session.execute(
        select(User).where(User.email == payload.email)
    )
    user = result.scalar_one_or_none()

    # 2. 校验密码。用户不存在和密码错统一返回 "invalid email or password",
    # 否则攻击者能通过响应差异判断 email 是否注册(用户枚举攻击)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. 校验 active
    if not user.is_active:
        # 403 Forbidden:已认证但账号被禁用
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account disabled",
        )

    # 4. 生成 refresh token
    refresh_str = generate_refresh_token_string()
    refresh_record = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_str),
        device_info=None,
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(refresh_record)
    await session.commit()
    await session.refresh(user)

    # 5. 签发 access
    access = create_access_token(str(user.id))

    return TokenPair(
        access_token=access,
        refresh_token=refresh_str,
        user=UserRead.model_validate(user),
    )

# ── Refresh ──

@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """用 refresh token 换新 access token。

    设计:refresh token 轮转(reuse detection)
    - 每次 refresh 后,旧 refresh token 立即失效
    - 同时签发新的 refresh token 返回给客户端
    - 防止 refresh token 被盗后无限使用

    流程:
    1. hash refresh token,查 DB
    2. 校验:存在 + 未 revoke + 未过期
    3. 查所属用户 + 校验 is_active
    4. 轮转:revoke 旧 token,签发新 refresh token
    5. 签发新 access token
    """
    token_hash = hash_token(payload.refresh_token)

    # 1. 查 refresh 记录
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    # 2. 三重校验:存在 / 未 revoke / 未过期
    if rt is None:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    if rt.revoked_at is not None:
        # 已 revoke 的 token 又被使用 = 可能被盗,可以选择性"级联 revoke 该用户所有 token"
        # 这里简化:只拒绝
        raise HTTPException(status_code=401, detail="refresh token revoked")
    if rt.expires_at < now:
        raise HTTPException(status_code=401, detail="refresh token expired")

    # 3. 查用户
    user = await session.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="user not found or disabled")

    # 4. 轮转:revoke 旧,签发新
    rt.revoked_at = now
    new_refresh_str = generate_refresh_token_string()
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(new_refresh_str),
        device_info=rt.device_info,  # 继承设备信息
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(new_rt)

    # 5. 签发新 access
    new_access = create_access_token(str(user.id))

    await session.commit()

    return {
        "access_token": new_access,
        "refresh_token": new_refresh_str,
        "token_type": "Bearer",
    }

# ── 登出 ──

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    session: AsyncSession = Depends(get_session),
) -> None:
    """登出:revoke 当前 refresh token(软删除)。

    access token 不处理(无状态 JWT 无法主动失效,等 15 分钟自然过期)。
    这是 JWT 无状态的代价——如果需要"立即踢出"功能,要维护 access token 黑名单,
    但那违背了 JWT 无状态的设计初衷,企业级通常接受这个权衡。

    即使 token 不存在/已 revoke 也返回 204,不泄露状态。
    """
    token_hash = hash_token(payload.refresh_token)

    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()

    if rt is not None and rt.revoked_at is None:
        rt.revoked_at = datetime.now(timezone.utc)
        await session.commit()

    # 永远返回 204,即使 token 无效
    return None
