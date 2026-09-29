import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_current_user
from app.core.db import get_session
from app.models.resume import Resume
from app.models.resume_content import ResumeContent
from app.models.user import User
from app.schemas.resume_content import ResumeContentPublic, ResumeContentUpdate
from app.workflows.content import generate_all_contents_streaming

from fastapi.responses import StreamingResponse


router = APIRouter(prefix="/resumes/{resume_id}/contents", tags=["contents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

async def _get_resume_or_404(
    resume_id: uuid.UUID,
    session: SessionDep,
    user_id: uuid.UUID,
) -> Resume:
    """按 id + user_id 查简历,不存在或不属于该用户都返回 404。"""
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id,  # 隔离
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简历不存在")
    return resume

@router.get("", response_model=list[ResumeContentPublic])
async def list_contents(
    resume_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> list[ResumeContent]:
    """列出一份简历的所有段内容，按 position 升序排序。"""
    await _get_resume_or_404(resume_id, session, current_user.id)

    result = await session.execute(
        select(ResumeContent)
        .where(ResumeContent.resume_id == resume_id)
        .order_by(ResumeContent.position.asc())
    )
    return list(result.scalars())

@router.patch("/{section_index}", response_model=ResumeContentPublic)
async def update_content(
    resume_id: uuid.UUID,
    section_index: int,
    payload: ResumeContentUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ResumeContent:
    """手动编辑某一段的内容（不走 LLM）。

    场景：用户觉得 LLM 写的某段不够好，手动改一下。
    """
    # 0. 校验简历归属(隔离)
    await _get_resume_or_404(resume_id, session, current_user.id)

    # 1. 查这条内容记录（resume_id + section_index 是幂等键）
    result = await session.execute(
        select(ResumeContent).where(
            ResumeContent.resume_id == resume_id,
            ResumeContent.section_index == section_index,
        )
    )
    content = result.scalar_one_or_none()
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该段内容尚未生成，无法编辑",
        )

    # 2. 只更新传了的字段——exclude_unset 区分"没传"和"传了 None"
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(content, key, value)

    # 3. 手动编辑也增版本号——revision 是乐观锁的基础
    content.revision += 1

    await session.commit()
    await session.refresh(content)
    return content

@router.post("/regenerate/stream", response_class=StreamingResponse)
async def regenerate_all_contents_stream(
    resume_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> StreamingResponse:
    """重新生成所有段（多段并发 SSE 流式版本）——C+G 融合方案。

    路径：POST /api/v1/resumes/{resume_id}/contents/regenerate/stream
    （在 contents router 的 prefix 下）

    返回 SSE 事件流：
    - status：主编开始 / 各段 writer 开始 / 各段完成
    - check：各段 proofreader 校对结果 + 跨段校验结果
    - done：每段最终内容
    - error：出错

    前端通过 section_index 字段区分各段进度。
    """
    # 0. 校验简历归属(隔离):防止 bob 调 alice 的简历的 regenerate
    await _get_resume_or_404(resume_id, session, current_user.id)

    return StreamingResponse(
        generate_all_contents_streaming(session, resume_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
