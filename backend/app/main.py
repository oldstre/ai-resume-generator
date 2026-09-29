from contextlib import asynccontextmanager
from app.core.checkpoint import close_checkpointer, setup_checkpointer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.queue import close_arq_pool
from app.core.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(_app: FastAPI):
# 启动时预热 Redis 连接池；退出时关闭，避免连接泄漏+ 异步开 checkpointer 连接池建表
    get_redis()
    await setup_checkpointer()
    yield
    await close_arq_pool()
    await close_redis()
    await close_checkpointer()


def create_app()->FastAPI:
    settings = get_settings()
    app = FastAPI(title = "AI Resume Generator API",lifespan= lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins = settings.cors_origins,
        allow_credentials = True,
        allow_methods= ["*"],
        allow_headers = ["*"]
    )
    app.include_router(api_router) 
    return app


app = create_app()
