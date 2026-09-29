"""鉴权端点 E2E 测试。

用真实 JWT 签发/解析 + 真实 DB（测试库），
测完整的注册→登录→refresh→logout 流程。

面试点：
- auth 测试不 mock 鉴权逻辑——JWT 签名、密码哈希、token 轮转都跑真的
- 只 mock 外部依赖（Redis/checkpointer 不要）
- 用真实 bcrypt 哈希 + 真实 JWT 签发，验证安全管线完整
"""
import uuid

import pytest


# ──────────────────────────────────────────────
# 注册
# ──────────────────────────────────────────────

async def test_register_success(auth_client):
    """POST /auth/register → 201 + TokenPair 结构正确。"""
    email = f"register-{uuid.uuid4().hex[:8]}@test.com"
    resp = await auth_client.post("/auth/register", json={
        "email": email,
        "password": "testpassword123",
        "full_name": "Test User",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["user"]["email"] == email
    assert data["user"]["full_name"] == "Test User"
    assert data["user"]["is_active"] is True
    # 清理：用 refresh token 登出
    await auth_client.post("/auth/logout", json={"refresh_token": data["refresh_token"]})


async def test_register_duplicate_email(auth_client):
    """重复注册同一 email → 409。"""
    email = f"dup-{uuid.uuid4().hex[:8]}@test.com"
    # 第一次注册
    resp1 = await auth_client.post("/auth/register", json={
        "email": email, "password": "password123", "full_name": "User1",
    })
    assert resp1.status_code == 201
    # 第二次注册同一 email
    resp2 = await auth_client.post("/auth/register", json={
        "email": email, "password": "password456", "full_name": "User2",
    })
    assert resp2.status_code == 409
    # 清理
    await auth_client.post("/auth/logout", json={
        "refresh_token": resp1.json()["refresh_token"]
    })


async def test_register_password_too_short(auth_client):
    """密码 <8 → 422（Pydantic 校验）。"""
    resp = await auth_client.post("/auth/register", json={
        "email": "short@test.com", "password": "123", "full_name": "Short",
    })
    assert resp.status_code == 422


async def test_register_missing_full_name(auth_client):
    """缺 full_name → 422。"""
    resp = await auth_client.post("/auth/register", json={
        "email": "no name@test.com", "password": "password123",
    })
    assert resp.status_code == 422


# ──────────────────────────────────────────────
# 登录
# ──────────────────────────────────────────────

async def test_login_success(auth_client):
    """先注册再登录 → 200 + TokenPair。"""
    email = f"login-{uuid.uuid4().hex[:8]}@test.com"
    password = "testpassword123"
    # 注册
    reg = await auth_client.post("/auth/register", json={
        "email": email, "password": password, "full_name": "Login User",
    })
    assert reg.status_code == 201
    # 登录
    resp = await auth_client.post("/auth/login", json={
        "email": email, "password": password,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == email
    # 清理
    await auth_client.post("/auth/logout", json={"refresh_token": data["refresh_token"]})


async def test_login_wrong_password(auth_client):
    """密码错误 → 401。"""
    email = f"wrong-{uuid.uuid4().hex[:8]}@test.com"
    await auth_client.post("/auth/register", json={
        "email": email, "password": "correctpassword", "full_name": "Wrong Pwd",
    })
    resp = await auth_client.post("/auth/login", json={
        "email": email, "password": "wrongpassword",
    })
    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


async def test_login_nonexistent_user(auth_client):
    """不存在的用户 → 401（不泄露用户是否存在）。"""
    resp = await auth_client.post("/auth/login", json={
        "email": "nonexistent@test.com", "password": "whatever",
    })
    assert resp.status_code == 401


# ──────────────────────────────────────────────
# Refresh token 轮转
# ──────────────────────────────────────────────

async def test_refresh_token_rotation(auth_client):
    """登录拿 refresh → /auth/refresh → 新 token 对 → 旧 refresh 失效。"""
    email = f"refresh-{uuid.uuid4().hex[:8]}@test.com"
    reg = await auth_client.post("/auth/register", json={
        "email": email, "password": "password123", "full_name": "Refresh User",
    })
    old_refresh = reg.json()["refresh_token"]

    # 第一次 refresh → 成功，拿到新 token 对
    resp1 = await auth_client.post("/auth/refresh", json={
        "refresh_token": old_refresh,
    })
    assert resp1.status_code == 200
    new_tokens = resp1.json()
    # refresh token 必须轮转（随机生成，一定不同）
    assert new_tokens["refresh_token"] != old_refresh
    # access token 解析出的 user_id 应一致
    # (不比较 access_token 字符串本身，因为同秒生成的 JWT payload 相同会产出相同字符串)

    # 旧 refresh 再用 → 失败（token 轮转：旧 token 立即失效）
    resp2 = await auth_client.post("/auth/refresh", json={
        "refresh_token": old_refresh,
    })
    assert resp2.status_code == 401

    # 清理：用新 refresh 登出
    await auth_client.post("/auth/logout", json={
        "refresh_token": new_tokens["refresh_token"]
    })


# ──────────────────────────────────────────────
# Logout
# ──────────────────────────────────────────────

async def test_logout_revokes_refresh(auth_client):
    """登出后旧 refresh 不能再 refresh。"""
    email = f"logout-{uuid.uuid4().hex[:8]}@test.com"
    reg = await auth_client.post("/auth/register", json={
        "email": email, "password": "password123", "full_name": "Logout User",
    })
    refresh = reg.json()["refresh_token"]

    # 登出
    resp = await auth_client.post("/auth/logout", json={
        "refresh_token": refresh,
    })
    assert resp.status_code == 204

    # 登出后 refresh 失效
    resp2 = await auth_client.post("/auth/refresh", json={
        "refresh_token": refresh,
    })
    assert resp2.status_code == 401


# ──────────────────────────────────────────────
# 受保护端点鉴权
# ──────────────────────────────────────────────

async def test_protected_endpoint_without_token(auth_client):
    """无 token 访问 /resumes → 401。"""
    resp = await auth_client.get("/resumes")
    assert resp.status_code == 401


async def test_protected_endpoint_with_token(auth_client):
    """有 token → 200（用注册拿到的 access token）。"""
    email = f"protected-{uuid.uuid4().hex[:8]}@test.com"
    reg = await auth_client.post("/auth/register", json={
        "email": email, "password": "password123", "full_name": "Protected User",
    })
    access = reg.json()["access_token"]
    refresh = reg.json()["refresh_token"]

    resp = await auth_client.get(
        "/resumes",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 200

    # 清理
    await auth_client.post("/auth/logout", json={"refresh_token": refresh})
