"""ARQ worker：后台异步生成简历大纲。

API 接到 POST /outline/generate 后，把任务丢到 Redis 队列就立即返回 202。
这个文件里的函数由独立的 arq worker 进程执行——不占 API worker。

注意：内容生成（正文）已经不再走 ARQ worker，全部走
POST /contents/regenerate/stream 多段并发 SSE 流式端点。
"""
import uuid
from typing import Any
from sqlalchemy import select
from app.core.db import async_session_factory
from app.workflows.outline import generate_outline
from app.models.resume_outline import ResumeOutline


async def generate_outline_task(
    ctx: dict[str, Any],
    resume_id: str,
) -> None:
    """ARQ worker 函数：后台调 LLM 生成简历大纲。

    参数：
        ctx: ARQ 注入的上下文（启动时塞进去的东西在这里，比如 LLM 客户端）
        resume_id: 简历 ID（必须是 str，因为通过 Redis 队列传递，不能是 UUID）
    """
    resume_uuid = uuid.UUID(resume_id)  # str → UUID
    try:
        async with async_session_factory() as session:
            await generate_outline(session, resume_uuid)
    except Exception as e:
         # 兜底异常处理：
         # - 业务异常（LLM 未配置/输出不符 schema）已经被 workflow 处理
         #   （status=failed + error 已写入数据库），这里不会再覆盖
         # - 非业务异常（网络/数据库/未知错误）workflow 没处理，这里补上
        await _mark_outline_failed(resume_uuid, e)
        # 不重新 raise：ARQ 会把 job 标记为 failed 并打印 Traceback。
        # 已经在数据库里写了 failed 状态，前端能查到——不需要 ARQ 再标记一遍。
        # 吞掉异常让 worker 日志干净。

async def _mark_outline_failed(resume_id: uuid.UUID, error: Exception) -> None:
    """兜底异常处理：确保 outline 记录被标记为 failed。

    只在 status 还是 generating 时才更新——
    如果已经是 failed，说明 workflow 已经处理过（错误信息更精确），不覆盖。
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(ResumeOutline).where(ResumeOutline.resume_id == resume_id)
        )
        outline = result.scalar_one_or_none()
        if outline is not None and outline.status == "generating":
            outline.status = "failed"
            outline.error = f"worker 异常:{error}"
            await session.commit()
