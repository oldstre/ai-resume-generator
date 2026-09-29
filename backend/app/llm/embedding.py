"""阿里通义 embedding 客户端封装。

和 tools.py 的 search_web 一个套路：
- 同步 SDK（dashscope）用 asyncio.to_thread 包成异步
- 懒加载：第一次调用才初始化 key
- 异常捕获 + 日志

提供两个函数：
- embed_text(text)   -> list[float]        单条向量化（检索时用）
- embed_texts(texts) -> list[list[float]]  批量向量化（入库时用）
"""

import asyncio
import logging
from typing import Sequence

from app.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["embed_text","embed_texts"]

# 懒加载标记：第一次调用才把 key 喂给 dashscope SDK
_initialized = False

def _ensure_init()-> None:
    """懒加载：第一次调用时把 key 配进 dashscope SDK。"""
    global _initialized
    if not _initialized:
        import dashscope
        settings = get_settings()
        if not settings.dashscope_api_key:
            raise RuntimeError( "DASHSCOPE_API_KEY 未配置" )
        dashscope.api_key = settings.dashscope_api_key
        _initialized = True

async def embed_text(text:str) -> list[float]:
    """单条文本 → 向量。B7 检索时用：把用户岗位+段标题向量化去查样本。"""
    return (await embed_texts([text]))[0]

# 模块级限流信号量：dashscope embedding 并发上限 2
_embedding_sem = asyncio.Semaphore(2)

async def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """批量文本 → 向量列表。入库时用：一次把多份样本向量化。

    dashscope text-embedding-v2 单次最多 25 条 input，这里按 25 分批。
    并发上限 2（_embedding_sem 控制），避免撞 dashscope QPS 限额。
    """
    _ensure_init()
    settings = get_settings()
    import dashscope

    all_embeddings: list[list[float]] = []
    BATCH = 25
    for i in range(0, len(texts), BATCH):
        batch = list(texts[i:i + BATCH])
        # 限流：acquire 后才调 dashscope，5 段并发时最多 2 个同时打 API
        async with _embedding_sem:
            resp = await asyncio.to_thread(
                dashscope.TextEmbedding.call,
                model=settings.embedding_model,
                input=batch,
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"embedding 调用失败: code={resp.code} msg={resp.message}"
            )
        for item in resp.output["embeddings"]:
            all_embeddings.append(item["embedding"])
    return all_embeddings
