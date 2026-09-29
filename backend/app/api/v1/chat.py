"""对话式编辑端点:POST /resumes/{id}/chat

用户用自然语言改简历,Agent 通过工具改真实 DB,
LangGraph Checkpointer 按 thread_id 持久化会话。
"""



from sys import prefix
import uuid
from typing import Annotated

from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_current_user
from app.core.db import get_session
from app.models.resume import Resume
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.workflows.chat import run_chat


router = APIRouter(prefix="/resumes/{resume_id}/chat",tags=["chat"])

SessionDep = Annotated[AsyncSession,Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

async def _get_resume_or_404(
    resume_id:uuid.UUID,
    session:SessionDep,
    user_id:uuid.UUID,
)->Resume:
    """按 id + user_id 查简历,不存在或不属于该用户都返回 404。

    和 contents.py 里的同名 helper 一模一样——这里复制不导入,
    避免跨路由文件互相耦合(后续可挪到公共 helper 模块)。
    """
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

@router.post( "" , response_model=ChatResponse )
async def chat_with_resume(
    resume_id :uuid.UUID,
    payload:ChatRequest,
    session:SessionDep,
    current_user: CurrentUser,
) -> ChatResponse:
    """对一份简历发起一轮对话式编辑。

    - 首次对话不传 thread_id,返回新 thread_id
    - 后续对话把上次返回的 thread_id 原样回传,Agent 记得上下文
    """
    # 1. 校验简历存在且属于当前用户(workflow 内部也会查,但这里提前 404 更友好)
    await _get_resume_or_404(resume_id,session,current_user.id)

    # 2. 调工作流
    try:
        result = await run_chat(
            resume_id=resume_id,
            message=payload.message,
            thread_id=payload.thread_id,
        )
    except ValueError as e:
        # workflow 抛 ValueError 主要是"简历不存在"——理论上前面 _get_resume_or_404 挡住了 
        # # 这里兜底防御,正常不会走到
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail= str (e))

     #  `**result` 是 Python 的 字典解包语法 ——把字典展开成关键字参数。所以: # 等价于 ChatResponse(reply= "好的,我已经把第2段精简了..." , thread_id= "abc-123-..." )
    return ChatResponse(**result)