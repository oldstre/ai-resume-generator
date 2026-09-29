"""鉴权工具层:密码哈希 + JWT 签发/解析 + refresh token 生成/哈希。

设计原则:
- access token 用 JWT(无状态,签发后不存 DB,只解析验签)
- refresh token 用 opaque 随机串(有状态,存 SHA-256 hash 到 DB,可立即 revoke)
- 密码用 bcrypt(慢哈希,防暴力破解);token 用 sha256(快哈希,token 高熵不怕彩虹表)
"""
from datetime import datetime, timedelta, timezone
from typing import Any
import secrets
import hashlib

import jwt  # PyJWT,import 名就是 jwt


import bcrypt
from app.core.config import get_settings

settings = get_settings()

# ── 密码哈希 ──

# ── 密码哈希 ──

def hash_password(plain: str) -> str:
    """哈希密码(bcrypt)。

    bcrypt 4.x 不兼容 passlib 1.7.x(passlib 读 bcrypt.__about__.__version__
    但 4.x 已删该模块),所以直接用 bcrypt 库,绕过 passlib。

    bcrypt 特性:每次 hash 同一密码结果不同(因为内置随机盐),
    所以不能 "哈希后比较字符串",必须用 verify_password 校验。

    限制:bcrypt 密码最长 72 字节,超过会抛 ValueError。
    注册端点应限制密码长度 <= 72 字节(或更严格如 <= 64)。
    """
    pwd_bytes = plain.encode("utf-8")
    salt = bcrypt.gensalt()  # 默认 cost=12,够安全(每次 hash ~200ms)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")  # bytes → str 存 DB

def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码跟 DB 里的哈希是否匹配。

    任何异常(哈希格式无效、密码超长等)都返回 False,不抛错。
    防止异常细节泄露用户是否存在(时序攻击防护)。
    """
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False

# ── Access Token(JWT) ──

def create_access_token(subject: str, extra: dict | None = None) -> str:
    """签发 access token。

    Args:
        subject: 通常传 user_id 字符串,放进 JWT 的 sub claim
        extra: 任意额外 claim(如 role),可选

    Returns:
        JWT 字符串(三段式 header.payload.signature)

    有效期短(15 分钟),过期后前端用 refresh token 换新。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,           # JWT 标准 claim:subject
        "iat": now,               # issued at:签发时间
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",         # 自定义 claim:区分 access / refresh
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def decode_token(token: str) -> dict[str, Any]:
    """解析并校验 JWT。

    返回 payload dict(含 sub / iat / exp / type 等 claim)。
    签名错误/过期会抛 jwt.PyJWTError 子类异常,调用方需捕获处理 401。
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )

# ── Refresh Token(opaque 随机串,不是 JWT) ──

def generate_refresh_token_string() -> str:
    """生成 refresh token 明文字符串(交给客户端的那一份)。

    用 secrets.token_urlsafe(32) 生成 43 字符 url-safe 随机串,
    熵足够高(256 bit),不可预测,不可枚举。

    注意:这不是 JWT!不解析,只 hash 后查 DB 校验。
    """
    return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
    """SHA-256 哈希 token,用于 DB 存储/查询。

    不用 bcrypt 的原因:
    - bcrypt 是慢哈希(故意慢,防暴力破解),用于密码
    - token 是高熵随机串(256 bit),不怕彩虹表,不需要慢哈希
    - refresh 校验频繁,慢哈希会让每次 refresh 慢 100ms+
    - sha256 快哈希(微秒级),够安全够快

    返回 64 字符 hex 字符串。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()