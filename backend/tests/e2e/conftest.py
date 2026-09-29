"""E2E 测试共享 fixtures。

策略：
1. 不用 main.py 的 app（它有 lifespan 初始化 Redis/checkpointer，测试环境没这些服务）
   → 创建精简 test_app：只挂路由，不跑 lifespan
2. override get_session → 用测试 DB 的 session factory
3. override get_current_user → 返回预置测试用户（跳过 JWT 解析，测端点逻辑不测鉴权本身）
4. streaming 测试额外 monkeypatch content.py 模块里的 agent 构造 → 隔离 LLM

面试点：E2E 测端点契约（SSE 事件序列、HTTP 状态码、DB 落库），
不测 LLM 质量。LLM 被 mock 掉，但 graph / SSE / DB 管线跑真的。
"""
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import api_router
from app.core.auth import get_current_user
from app.core.db import Base, get_session
from app.models import Resume, ResumeContent, ResumeOutline, User
from app.models.refresh_token import RefreshToken


# 测试 DB URL：默认用 dev 同实例的 resume_test 库
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://resume:resume@localhost:39432/resume",
)


# ──────────────────────────────────────────────
# 测试 DB engine + 建表
# ──────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """创建测试 DB engine，session 级别建表（如不存在），session 结束不 drop。

    用 dev 库（已有 Alembic 建好的表），不 drop 避免影响开发数据。
    每个测试自行清理创建的数据。
    """
    engine = create_async_engine(TEST_DB_URL, echo=False)
    # 确保表存在（dev 库已有，这是保险）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    """每个测试独立的 DB session。"""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ──────────────────────────────────────────────
# 测试用户
# ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """在测试 DB 里创建一个真实用户（auth 测试需要真实 DB 记录）。"""
    user = User(
        email=f"test-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="$2b$12$testhashplaceholderfortestsessionuseonly",
        full_name="Test User",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    yield user
    # 清理：删除该用户的 refresh tokens + 简历 + 用户本身
    await db_session.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    await db_session.execute(
        delete(ResumeContent).where(
            ResumeContent.resume_id.in_(
                select(Resume.id).where(Resume.user_id == user.id)
            )
        )
    )
    await db_session.execute(
        delete(ResumeOutline).where(
            ResumeOutline.resume_id.in_(
                select(Resume.id).where(Resume.user_id == user.id)
            )
        )
    )
    await db_session.execute(delete(Resume).where(Resume.user_id == user.id))
    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


# ──────────────────────────────────────────────
# Test app + httpx client
# ──────────────────────────────────────────────

def _create_test_app() -> FastAPI:
    """创建不带 lifespan 的测试 app。

    main.py 的 app 有 lifespan 初始化 Redis / checkpointer / ARQ，
    测试环境没有这些服务，所以创建精简 app：只挂路由。
    """
    app = FastAPI()
    app.include_router(api_router)
    return app


@pytest_asyncio.fixture
async def app_client(test_engine, test_user):
    """带鉴权 override 的 test app + httpx client。

    get_session → 用测试 DB session factory（直接用 test_engine，不用 db_session.get_bind()，
    因为异步 session 的 get_bind() 会触发 _get_sync_engine_or 错误）
    get_current_user → 直接返回 test_user（跳过 JWT 解析）
    """
    app = _create_test_app()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    async def override_get_current_user():
        return test_user

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = override_get_current_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(test_engine):
    """不 override get_current_user 的 test app client——给 auth 测试用。

    auth 测试需要真实的 JWT 签发/解析 + 真实 DB 查询。
    只 override get_session 指向测试 DB。
    """
    app = _create_test_app()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()
