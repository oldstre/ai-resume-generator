"""内容生成 workflow：多段并发生成 + SSE 流式推送。

唯一对外暴露的入口是 generate_all_contents_streaming，给
POST /api/v1/resumes/{resume_id}/contents/regenerate/stream 端点用。

机制：
  1. 查简历 + 大纲 + 校验
  2. 构造 chief_editor / writer / proofreader 三个 agent 实例
  3. 构造 build_multi_section_graph 图（主编 → 5 段并发子图 → 跨段校验 → all_done）
  4. asyncio.Queue 当传菜窗口，on_event 回调把节点事件塞进 queue
  5. asyncio.create_task 把 graph 跑在后台
  6. 主循环从 queue 拿事件 → yield SSE 字符串给前端
  7. graph 跑完塞个 SENTINEL 哨兵，主循环看到就结束

错误处理：
  所有异常（准备阶段 ValueError / LLM 异常 / 其他）都转成 SSE error 事件，
  前端只看 SSE 流就能知道结果，不会出现 500 状态码。
"""
import asyncio
import uuid
from typing import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.client import create_chat_model
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)
from app.llm.graph import build_multi_section_graph
from app.models import Resume, ResumeContent, ResumeOutline
from app.schemas.sse_event import (
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    format_sse,
)


async def _finalize_all_success(
    session: AsyncSession,
    resume_id: uuid.UUID,
    result: dict,
) -> None:
    """多段落批量落库：遍历 graph 结果的 sections，每段查/建 ResumeContent 并更新。

    跳过 draft 为 None 的段（生成失败的段保持 generating 状态，由错误流程处理）。
    """
    sections = result.get("sections", {})
    for k, sub_state in sections.items():
        section_index = int(k)
        draft = sub_state.get("draft")
        if draft is None:
            continue
        # 查现有 ResumeContent（幂等键 resume_id + section_index）
        result_q = await session.execute(
            select(ResumeContent).where(
                ResumeContent.resume_id == resume_id,
                ResumeContent.section_index == section_index,
            )
        )
        content = result_q.scalar_one_or_none()
        if content is None:
            content = ResumeContent(
                resume_id=resume_id,
                section_index=section_index,
                position=section_index,
                title=draft.title,
                status="ready",
            )
            session.add(content)
            content.revision = 1  # 新建第一版（default 在 flush 时才生效，这里显式设）
        else:
            content.revision += 1  # 更新，版本+1
        # 更新字段
        content.title = draft.title
        content.content = draft.content
        content.issues = sub_state.get("issues_history", [])
        content.status = "ready"
    await session.commit()


async def generate_all_contents_streaming(
    session: AsyncSession,
    resume_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    """多段并发生成的流式 SSE 生成器。

    给 POST /api/v1/resumes/{resume_id}/contents/regenerate/stream 端点用。
    yield 的字符串已是符合 SSE 协议的 'event: xxx\\ndata: {...}\\n\\n' 格式，
    StreamingResponse 直接吐给前端。
    """
    SENTINEL = object()
    queue: asyncio.Queue = asyncio.Queue()

    # ── 准备阶段：查简历 + 大纲 + 校验 ──
    try:
        result_q = await session.execute(select(Resume).where(Resume.id == resume_id))
        resume = result_q.scalar_one_or_none()
        if resume is None:
            yield format_sse(ErrorEvent(
                event="error",
                data=ErrorData(message=f"简历不存在: {resume_id}"),
            ))
            return
        result_q = await session.execute(
            select(ResumeOutline).where(ResumeOutline.resume_id == resume_id)
        )
        outline = result_q.scalar_one_or_none()
        if outline is None:
            yield format_sse(ErrorEvent(
                event="error",
                data=ErrorData(message=f"简历 {resume_id} 还没有大纲，无法生成内容"),
            ))
            return
        if outline.status != "confirmed":
            yield format_sse(ErrorEvent(
                event="error",
                data=ErrorData(message=f"简历 {resume_id} 的大纲尚未确认，状态: {outline.status}"),
            ))
            return
    except Exception as error:
        yield format_sse(ErrorEvent(
            event="error",
            data=ErrorData(message=f"准备阶段失败: {error}"),
        ))
        return

    # ── 构造 agents + 图 ──
    settings = get_settings()
    chat_model = create_chat_model(settings)
    chief_editor = ChiefEditorAgent(model=chat_model, api_key=settings.llm_api_key)
    writer = DeepSeekWriterAgent(model=chat_model, api_key=settings.llm_api_key)
    proofreader = DeepSeekProofReaderAgent(model=chat_model, api_key=settings.llm_api_key)

    async def on_event(event):
        """LangGraph 节点回调：把事件格式化后塞进 queue。"""
        await queue.put(format_sse(event))

    graph = build_multi_section_graph(
        chief_editor, writer, proofreader,
        max_retries=2,
        on_event=on_event,
    )

    # ── 构造 graph 入参 ──
    resume_basics = {
        "title": resume.title,
        "applicant_name": resume.applicant_name,
        "target_position": resume.target_position,
        "tone": resume.tone,
        "content_density": resume.content_density,
        "section_count": resume.section_count,
    }
    initial_state = {
        "chief_plan": {},
        "sections": {},
        "cross_issues": [],
        "section_count": resume.section_count,
        "round": 0,
        "max_rounds": 1,
        "resume_id": str(resume_id),
        "outline_sections": outline.sections,
        "resume_basics": resume_basics,
    }

    # ── 后台任务：跑图 + 落库 + 推 DoneEvent ──
    async def run_graph():
        try:
            result = await graph.ainvoke(initial_state)
            # 落库：所有段更新 ResumeContent
            await _finalize_all_success(session, resume_id, result)
            # 推 DoneEvent（每段一个，带 section_index）
            for k, sub_state in result.get("sections", {}).items():
                draft = sub_state.get("draft")
                if draft is None:
                    continue
                await queue.put(format_sse(DoneEvent(
                    event="done",
                    data=DoneData(
                        content=draft.content,
                        title=draft.title,
                        attempts=sub_state.get("attempts", 0),
                        issues_history=sub_state.get("issues_history", []),
                        section_index=int(k),
                    ),
                )))
        except Exception as error:
            await queue.put(format_sse(ErrorEvent(
                event="error",
                data=ErrorData(message=f"生成失败: {error}"),
            )))
        finally:
            await queue.put(SENTINEL)  # 无论成功失败，最后塞哨兵

    # ── 启动后台任务，主循环 yield 事件 ──
    task = asyncio.create_task(run_graph())
    try:
        while True:
            item = await queue.get()
            if item is SENTINEL:
                break
            yield item
    finally:
        # 客户端断开连接时取消后台任务，避免泄漏
        if not task.done():
            task.cancel()
