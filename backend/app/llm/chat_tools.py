"""对话式编辑的 Agent 工具集。

三个工具覆盖简历编辑的核心场景:
- list_sections:看全貌(概览所有段)
- get_section:看细节(读某段完整内容)
- update_section:改内容(更新某段)

工具内通过全局 async_session_factory 拿 DB session,
不依赖 FastAPI 的 Depends(LangGraph 工具没有依赖注入机制)。
"""
import json
import uuid

from langchain_core.tools import tool
from sqlalchemy import select

from app.core.db import async_session_factory
from app.models import ResumeContent


@tool
async def list_sections(resume_id:str)->str:
    """列出简历所有段落的概览。

    用于了解简历整体结构:有几段、每段标题、状态、内容摘要。
    当用户问"简历现在怎么样"或要全局把握时调用。

    Args:
        resume_id: 简历 ID(UUID 字符串形式)
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(ResumeContent).where(ResumeContent.resume_id == uuid.UUID(resume_id)).order_by(ResumeContent.position.asc())
        )
        sections = result.scalars().all()

    if not sections:
        return "该简历还没有任何段落内容。"

    lines = []
    for s in sections:
        # 内容摘要:取前 60 字符让 LLM 判断要不要细看
        if s.content:
            content_str = json.dumps(s.content,ensure_ascii=False)
            preview = content_str[:60] + ("..." if len(content_str)>60 else "")

        else:
            preview = "(空)"
        lines.append(
            f"[段 {s.section_index} ] 标题: {s.title} | 状态: {s.status} | 摘要: {preview} "
        )
    return "\n".join(lines)

@tool
async def get_section(resume_id:str,section_index:int) ->str:
    """查看简历某一段的完整内容。

    当需要精读某段、判断如何修改时调用。返回完整 JSON 内容。

    Args:
        resume_id: 简历 ID(UUID 字符串形式)
        section_index: 段落索引(0-based,第一段是 0)
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(ResumeContent).where(
                ResumeContent.resume_id ==uuid.UUID(resume_id),
                ResumeContent.section_index == section_index
            )
        )
        section = result.scalar_one_or_none()
    
    if section is None:
       return f"找不到段索引 {section_index} 的内容。"  
    
    content_str = (
        json.dumps(section.content,ensure_ascii=False,indent=2)
        if section.content else "(空)"
    )
    return ( f"段索引: {section.section_index} \n" f"标题: {section.title} \n" f"状态: {section.status} \n" f"内容:\n {content_str} " )


@tool 
async def update_section ( resume_id: str , section_index: int , new_content: str ) -> str : 
    """更新简历某一段的内容。

    new_content 必须是 JSON 字符串,结构要和原内容保持一致(同样的字段名和嵌套)。
    例如技能段:'{"items": [{"skill": "Python", "years": 3, "level": "熟练"}]}'
    更新前建议先调 get_section 看原结构,再照着改。

    Args:
        resume_id: 简历 ID(UUID 字符串形式)
        section_index: 段落索引(0-based)
        new_content: 新内容(JSON 字符串,结构需与原 content 字段一致)
    """ # 1. 解析 JSON:LLM 产出的可能不合法,要给清晰错误让它重试 
    try :
        content_dict = json.loads(new_content) 
    except json.JSONDecodeError as e: 
        return f"new_content 不是合法 JSON: {e} 。请确保是合法 JSON 字符串。" 
    if not isinstance (content_dict, dict ): 
        return "new_content 解析后必须是 JSON 对象(字典),不能是数组或标量。" 
    # 2. 写 DB 
    async with async_session_factory() as session:
        result = await session.execute(
            select(ResumeContent).where(
                ResumeContent.resume_id == uuid.UUID(resume_id),
                ResumeContent.section_index == section_index,
            )
        )
        section = result.scalar_one_or_none() 
        if section is None : 
            return f"找不到段索引 {section_index} 的内容,无法更新。" 
        section.content = content_dict
        section.status = "ready" 
        section.revision += 1 # 乐观锁版本号 +1,和手动编辑端点保持一致 
        await session.commit() # expire_on_commit=False,commit 后属性仍在对象上,可安全访问 
        title = section.title
        revision = section.revision 
        return f"段 {section_index} ( {title} )已更新成功。当前 revision= {revision} "