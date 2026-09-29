"""RAG 检索：从 pgvector 检索同岗位优秀简历样本，喂给 LLM 参考。

流程：
1. 把段标题向量化（查询向量）
2. ORDER BY embedding <=> query_vector LIMIT k（余弦距离最近邻）
3. 返回样本列表

降级：检索失败返回空列表，不阻塞内容生成（RAG 是增强，不是必需）

阶段 6 改造：用 asyncpg 连接池（_pool 全局懒加载），
避免 5 段并发时每次 retrieve_samples 都新建/关闭连接的震荡。
"""
import json
import logging
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector
from app.core.config import get_settings
from app.llm.embedding import embed_text

logger = logging.getLogger(__name__)
__all__ = ["retrieve_samples", "close_pool"]

# 全局连接池：懒加载，第一次 retrieve_samples 时初始化
_pool: asyncpg.Pool | None = None

async def _get_pool() -> asyncpg.Pool:
    """懒加载全局连接池。5 段并发共用一个池，复用连接。"""
    global _pool
    if _pool is None:
        settings = get_settings()
        dsn = settings.pgvector_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,        # 常驻 2 个连接
            max_size=8,       # 最多 8 个并发连接（够 5 段 + buffer）
            command_timeout=10,  # 单条 SQL 超时 10s
        )
    return _pool

async def close_pool() -> None:
    """应用关闭时调，释放连接池。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

async def retrieve_samples(
    target_position: str,
    section_title: str,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """检索同岗位的相似段样本。

    Args:
        target_position: 目标岗位（日志记录用，不再做 WHERE 过滤）
        section_title:   段标题（向量化后做近邻检索）
        top_k:           返回几条，None 时用 config.rag_top_k

    Returns:
        [{"section_title": ..., "content": {...}}, ...]
        检索失败返回空列表（降级，不阻塞生成）
    """
    print(f">>> [RAG] retrieve 被调用: position={target_position!r} section={section_title!r}")
    settings = get_settings()
    k = top_k or settings.rag_top_k
    try:
        # 1. 段标题向量化
        query_vec = await embed_text(section_title)

        # 2. 从连接池拿连接（不再每次新建）
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await register_vector(conn)
            rows = await conn.fetch(
                """
                SELECT target_position, section_title, content
                FROM resume_sample
                ORDER BY embedding <=> $1
                LIMIT $2
                """,
                query_vec,
                k,
            )

        # content 是 JSONB，asyncpg 默认返回 str，要 json.loads 还原
        samples: list[dict[str, Any]] = []
        for row in rows:
            content = row["content"]
            if isinstance(content, str):
                content = json.loads(content)
            samples.append({
                "section_title": row["section_title"],
                "target_position": row["target_position"],
                "content": content,
            })
        positions = [s["target_position"] for s in samples]
        logger.info(
            "RAG 检索命中: 查询段=%s 命中 %d 条, 样本岗位=%s",
            section_title, len(samples), positions,
        )
        print(f">>> [RAG] 检索成功命中 {len(samples)} 条: {[s['section_title'] for s in samples]}")
        return samples
    except Exception as error:
        # 降级：检索失败不阻塞生成
        logger.warning(
            "RAG 检索失败，降级返回空列表: %s: %s",
            type(error).__name__, error,
        )
        return []