import uuid 
from typing import Annotated

from sqlalchemy.orm import Session, session
from app.core.auth import get_current_user
from app.core.queue import get_arq_pool
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.models.resume import Resume
from app.models.resume_outline import ResumeOutline
from app.models.user import User
from app.schemas.resume_outline import (
    ResumeOutlinePublic,
    ResumeOutlineRevisionRequest,
    ResumeOutlineUpsert,
)
from app.workflows.outline import generate_outline
from app.llm.errors import InvalidOutlineOutputError, LLMNotConfiguredError


router = APIRouter(prefix="/resumes/{resume_id}/outline",tags=["outline"])
SessionDep = Annotated[AsyncSession,Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

async def _get_resume_or_404(
    resume_id:uuid.UUID,
    session:Session,
    user_id:uuid.UUID,
)->Resume:
    """按 id + user_id 查简历,不存在或不属于该用户都返回 404。"""
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == user_id,  # 隔离
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail= "简历不存在" )
    return resume


def _ensure_revision(outline:ResumeOutline,revision:int)->None:
    if outline.revision != revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
            detail= "大纲已被其他操作更新，请刷新后重试" ,)

def _ensure_draft(outline:ResumeOutline)->None:
    """确认前必须先确保大纲处于 draft 状态。"""
    if outline.status !="draft":
        detail = "请先取消确认" if outline.status == "confirmed" else "当前大纲不可编辑"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _get_outline_or_404(resume_id: uuid.UUID, session: SessionDep) -> ResumeOutline:
    """按 resume_id 查大纲，不存在返回 404。"""
    result = await session.execute(
        select(ResumeOutline).where(ResumeOutline.resume_id == resume_id)
    )
    outline = result.scalar_one_or_none()
    if outline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚未生成大纲")
    return outline

@router.get( "" , response_model=ResumeOutlinePublic )
async def get_outline(
    resume_id:uuid.UUID,
    session:SessionDep,
    current_user: CurrentUser,
)->ResumeOutline:
    """取一份简历的大纲。简历或大纲任一不存在都返回 404。"""
    await _get_resume_or_404(resume_id,session,current_user.id)

    return await _get_outline_or_404(resume_id, session)

@router.put( "" , response_model=ResumeOutlinePublic )
async def upsert_outline(
    resume_id:uuid.UUID,
    body:ResumeOutlineUpsert,
    session:SessionDep,
    current_user: CurrentUser,
)->ResumeOutline:
    """手动写大纲：不存在就创建，存在就整体替换 sections 并 revision +1。"""
    await _get_resume_or_404(resume_id,session,current_user.id)
    result = await session.execute(
        select(ResumeOutline).where(ResumeOutline.resume_id == resume_id)
    )
    outline = result.scalar_one_or_none()
    # model_dump(mode="json") 把 Pydantic 对象转成可存进 JSONB 的纯字典列表
    sections_data = [s.model_dump(mode="json") for s in body.sections]
    if outline is None:
        # 不存在 → 新建。status=draft 表示"可编辑，未确认"
        outline = ResumeOutline(
            resume_id = resume_id,
            status = "draft",
            sections = sections_data
        )
        session.add(outline)
    else:
            # 存在 → 整体替换 sections，revision +1
        outline.sections = sections_data
        outline.revision +=1
    await session.commit()
    await session.refresh(outline)
    return outline

@router.post( "/confirm" , response_model=ResumeOutlinePublic )
async def confirm_outline(
    resume_id:uuid.UUID,
    body:ResumeOutlineRevisionRequest,
    session:SessionDep,
    current_user: CurrentUser,
)->ResumeOutline:
    """确认大纲：draft → confirmed。必须传 revision 做乐观锁校验。"""
    resume = await _get_resume_or_404(resume_id,session,current_user.id)
    outline = await _get_outline_or_404(resume_id,session)
    _ensure_draft(outline)
    _ensure_revision(outline,body.revision)

    outline.status = "confirmed"
    outline.revision+=1
    # 同步更新简历主表状态：outline_ready 表示"大纲已就绪，可以生成内容"
    resume.status = "outline_ready"

    await session.commit()
    await session.refresh(outline)
    return outline

@router.post( "/unconfirm" , response_model=ResumeOutlinePublic )
async def unconfirm_outline(
    resume_id :uuid.UUID,
    body:ResumeOutlineRevisionRequest,
    session:SessionDep,
    current_user: CurrentUser,
)->ResumeOutline:
    """取消确认：confirmed → draft。同样需要 revision 校验。"""
    resume = await _get_resume_or_404(resume_id,session,current_user.id)
    outline = await _get_outline_or_404(resume_id,session)

    if outline.status != "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
            detail= "大纲尚未确认" ,)
    _ensure_revision(outline,body.revision)

    outline.status = "draft"
    outline.revision +=1
    resume.status = "draft"

    await session.commit()
    await session.refresh(outline)
    return outline


@router.post( "/generate" , status_code= 202 )
async def generate_outline_api(
    resume_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> dict:
    """触发生成大纲（异步版本）。

    立即提交 ARQ 任务，返回 job_id。前端用 job_id 轮询状态。
    worker 在另一个进程里慢慢调 LLM，不占 API worker。
    """
    # 1. 查简历（不存在或不属于当前用户都抛 404）
    await _get_resume_or_404(resume_id,session,current_user.id)

    # 2. 提交 ARQ 任务——相当于"服务员把小票丢传菜口"
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "generate_outline_task" ,# ← 函数名字符串（不是函数对象！）
        resume_id = str(resume_id) # ← 参数必须 JSON 可序列化，UUID 要转 str
    )

    if job is None:
        # enqueue_job 返回 None 表示队列已满（达到 max_jobs）
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail= "任务队列已满，请稍后重试" ,
        )
   # 3. 把 job_id 写到 outline 记录（前端能立刻查到状态）
    result = await session.execute(select(ResumeOutline).where(ResumeOutline.resume_id==resume_id))
    outline= result.scalar_one_or_none()
    if outline is None:
        # 不存在 → 建新记录，status=generating
        outline = ResumeOutline(
            resume_id = resume_id,
            status ="generating",
            sections = [],
            job_id = job.job_id
        )
        session.add(outline)
    else:
        # 已存在 → 复用记录，重新置 generating
        outline.status = "generating"
        outline.error = None
        outline.sections = []
        outline.job_id = job.job_id

    await session.commit()
    # 4. 立即返回 202 + job_id（不等 LLM）
    return {"job_id": job.job_id, "status": "generating"}


