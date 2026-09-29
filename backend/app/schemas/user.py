"""用户/鉴权相关 Pydantic schemas。"""
from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── 请求 schemas ──

class UserCreate(BaseModel):
    """注册请求体。"""
    email: EmailStr
    # bcrypt 限 72 字节,我们限 64 留余量。min 8 是基本密码强度要求
    password: str = Field(..., min_length=8, max_length=64)
    full_name: str = Field(..., min_length=1, max_length=100)

class UserLogin(BaseModel):
    """登录请求体。"""
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    """refresh 请求体。"""
    refresh_token: str

class LogoutRequest(BaseModel):
    """登出请求体。"""
    refresh_token: str

# ── 响应 schemas ──

class UserRead(BaseModel):
    """返回给前端的用户信息(绝不包含 hashed_password)。"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime

class TokenPair(BaseModel):
    """登录/注册成功后返回的 token 对。"""
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    user: UserRead
