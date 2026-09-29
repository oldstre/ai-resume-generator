from io import BytesIO
from urllib.parse import quote
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_session
from app.models.resume import Resume
from app.models.resume_content import ResumeContent
from app.models.user import User
from app.schemas.resumes import ResumeCreate, ResumePublic, ResumeUpdate
from app.services.pdf_exporter import export_resume_to_pdf

import uuid

router = APIRouter(prefix= "/resumes",tags = ["resumes"])

# 类型别名+依赖注入：路由函数参数里直接用，比每次写 Depends(get_session) 简洁
SessionDep = Annotated[AsyncSession,Depends(get_session)]
# 当前用户依赖别名:跟 SessionDep 风格一致,端点签名写 current_user: CurrentUser
CurrentUser = Annotated[User, Depends(get_current_user)]

#- `response_model=ResumePublic` ：返回值用这个 schema 序列化,- `status_code=201` ：HTTP 201 Created（创建成功的标准码）
@router.post("",response_model = ResumePublic,status_code= status.HTTP_201_CREATED)
async def create_resume(
    body:ResumeCreate,
    session:SessionDep,
    current_user: CurrentUser,
)->Resume:
    # 关键:user_id 由服务端从 token 填,不信任客户端传(ResumeCreate schema 里也没这字段)
    resume =Resume(**body.model_dump(), user_id=current_user.id)

    session.add(resume)#把对象加到会话（还没入库）
    await session.commit()#提交事务，真正写库
    await session.refresh(resume)# 从库重新读一次，把 server_default 生成的值同步到对象
    return resume

@router.get("",response_model = list[ResumePublic])
async def list_resumes(
    session:SessionDep,
    current_user: CurrentUser,
)->list[Resume]:
    # 只返回当前用户的简历(WHERE user_id = current_user.id)
    result = await session.execute(
        select(Resume)
        .where(Resume.user_id == current_user.id)
        .order_by(Resume.updated_at.desc())
    )
    return list(result.scalars())

@router.get( "/{resume_id}" , response_model=ResumePublic )
async def get_resume(
    resume_id:uuid.UUID,
    session:SessionDep,
    current_user: CurrentUser,
)->Resume:
    """按 id 查单个简历。找不到或不属于当前用户都返回 404(不泄露存在性)。"""
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,  # 隔离:不是你的就当不存在
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail = "简历不存在",
        )
    return resume

@router.patch( "/{resume_id}" , response_model=ResumePublic )
async def update_resume(
    resume_id: uuid.UUID,
    body:ResumeUpdate,
    session:SessionDep,
    current_user: CurrentUser,
)->Resume:
    """部分更新简历：只改客户端传了的字段。"""
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,  # 隔离
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail= "简历不存在" )
    # exclude_unset=True：只导出客户端实际传了的字段，避免把没传的字段覆盖成 None
    data= body.model_dump(exclude_unset=True)
    for field,value in data.items():
        # setattr(obj, name, value) 等价于 obj.<name> = value，但可以用变量名动态设置
        setattr(resume,field,value)

    await session.commit()
    await session.refresh(resume)
    return resume

@router.delete( "/{resume_id}" , status_code=status.HTTP_204_NO_CONTENT )
async def remove_resume(
    resume_id:uuid.UUID,
    session:SessionDep,
    current_user: CurrentUser,
)->None:
    """删除简历。关联的大纲会被级联删除（cascade="all, delete-orphan"）。"""
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,  # 隔离
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail= "简历不存在" )

    await session.delete(resume) # ORM 会自动级联删除关联的 ResumeOutline
    await session.commit()


@router.get("/{resume_id}/pdf")
async def export_resume_pdf(
    resume_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
):
    """导出简历为 PDF 文件。

    流程：查简历 → 查所有段内容 → 检查全部 ready → 渲染 PDF → 返回文件流。
    """
    # 1. 查简历（不存在或不属于当前用户都抛 404）
    result = await session.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,  # 隔离
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="简历不存在")

    # 2. 查所有段内容（按 position 排序）
    result = await session.execute(
        select(ResumeContent)
        .where(ResumeContent.resume_id == resume_id)
        .order_by(ResumeContent.position.asc())
    )
    contents = list(result.scalars())

    if not contents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="尚未生成内容，无法导出",
        )

    # 3. 检查所有段都是 ready（有未就绪的段不能导出）
    not_ready = [c for c in contents if c.status != "ready"]
    if not_ready:
        titles = "、".join(c.title for c in not_ready)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"以下段落尚未就绪: {titles}",
        )

    # 4. 渲染 PDF
    try:
        pdf_bytes = export_resume_to_pdf(resume, contents)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF 渲染失败: {e}",
        ) from e

    # 5. 返回文件流（浏览器会触发下载）
    filename = quote(f"{resume.title}.pdf")
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )