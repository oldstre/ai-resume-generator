"""鉴权依赖:从请求头解析 JWT,返回当前 User 对象。

所有受保护端点都用 Depends(get_current_user) 拿到当前用户。
这是 FastAPI 鉴权的核心模式:依赖注入 + OAuth2。
"""
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_token
from app.models.user import User

# OAuth2PasswordBearer 会自动从 Authorization: Bearer <token> 提取 token
# tokenUrl 指向登录端点,Swagger UI 的"Authorize"按钮用它知道去哪登录
# 注意:tokenUrl 只是文档提示,Swagger 不会真的发请求,它用这个 URL 构造表单
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """从 JWT 解析当前用户。

    流程:
    1. oauth2_scheme 从 Authorization 头提取 token(无 token 自动抛 401)
    2. decode_token 解析 JWT(签名错/过期抛 PyJWTError)
    3. 从 payload 取 user_id (sub claim)
    4. 校验 type == "access"(防止拿 refresh token 来调受保护端点)
    5. 查 DB
    6. 校验 user 存在 + is_active

    任何一步失败都抛统一 401,错误信息模糊(不泄露是哪步失败)。
    """
    # 统一的 401 异常,所有失败路径都用它,不泄露具体原因
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 1. 解析 token
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        # 签名错误、过期、格式错误等,统一抛 401
        raise credentials_exception

    # 2. 取 user_id (sub claim)
    user_id_str: str | None = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    # 3. 校验 type 必须是 access
    # 防止有人拿 refresh token 来调受保护端点(refresh token 是 opaque 字符串,
    # 不会被 decode_token 解析成功,但万一未来换 JWT refresh,这层校验是保险)
    if payload.get("type") != "access":
        raise credentials_exception

    # 4. 转 UUID(payload 可能被篡改,sub 可能不是合法 UUID)
    try:
        user_id = uuid.UUID(user_id_str)
    except (ValueError, AttributeError, TypeError):
        raise credentials_exception

    # 5. 查 DB
    user = await session.get(User, user_id)
    if user is None:
        raise credentials_exception

    # 6. 校验 active(用户被禁用后即使 token 没过期也拒绝)
    if not user.is_active:
        raise credentials_exception

    return user