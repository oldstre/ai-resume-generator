"""大纲生成 workflow：把"查简历 → 调 LLM → 存大纲"封装成一个可复用函数。

API 路由和 ARQ worker 都调这个函数——业务逻辑只维护一份。
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.base import OutlineGenerationInput
from app.llm.client import create_chat_model
from app.llm.deepseek import DeepSeekOutlineGenerator
from app.llm.errors import InvalidOutlineOutputError, LLMNotConfiguredError
from app.models.resume import Resume
from app.models.resume_outline import ResumeOutline


async def generate_outline(
    session: AsyncSession,
    resume_id: uuid.UUID,
) -> ResumeOutline:
    """生成简历大纲的完整 workflow。

    步骤：
        1. 查简历（不存在抛 ValueError）
        2. 查现有大纲：不存在建新的，存在则复用记录重新生成
        3. 状态置 generating，清空旧内容和 error
        4. 构造 LLM 输入 OutlineGenerationInput
        5. 调 LLM 生成 OutlineDraft
        6. 失败 → status=failed + 存 error，重新抛出
        7. 成功 → status=draft + 存 sections + revision +1
    """
    # 1. 查简历
    result = await session.execute(select(Resume).where(Resume.id == resume_id))
    resume = result.scalar_one_or_none()
    if resume is None:
        raise ValueError(f"简历不存在: {resume_id}")

    # 2. 查现有大纲
    result = await session.execute(
        select(ResumeOutline).where(ResumeOutline.resume_id == resume_id)
    )
    outline = result.scalar_one_or_none()
    if outline is None:
        # 不存在 → 建新记录
        outline = ResumeOutline(
            resume_id=resume_id,
            status="generating",
            sections=[],
        )
        session.add(outline)
    else:
        # 已存在 → 复用记录，重新生成（清空旧内容）
        outline.status = "generating"
        outline.error = None
        outline.sections = []
    await session.commit()
    await session.refresh(outline)

    # 3. 构造 LLM 输入
    payload = OutlineGenerationInput(
        title=resume.title,
        applicant_name=resume.applicant_name,
        target_position=resume.target_position,
        tone=resume.tone,
        section_count=resume.section_count,
        content_density=resume.content_density,
    )

    # 4. 调 LLM 生成大纲
    settings = get_settings()
    generator = DeepSeekOutlineGenerator(
        model=create_chat_model(settings),
        api_key=settings.llm_api_key,
    )

    try:
        draft = await generator.generate(payload)
    except (InvalidOutlineOutputError, LLMNotConfiguredError) as error:
        # 失败：存错误信息，状态变 failed
        outline.status = "failed"
        outline.error = str(error)
        await session.commit()
        await session.refresh(outline)
        raise  # 重新抛出，让上层（API/worker）处理

    # 5. 成功：存大纲，状态变 draft
    outline.sections = [s.model_dump(mode="json") for s in draft.sections]
    outline.status = "draft"
    outline.revision += 1
    await session.commit()
    await session.refresh(outline)
    return outline





