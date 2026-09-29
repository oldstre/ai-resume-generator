"""对话式编辑工作流:用户用自然语言改简历,Agent 通过工具改真实 DB。

流程:
1. 查简历全文(基本信息 + 所有段内容)
2. 拼系统 prompt 注入到消息历史
3. 调 chat_graph(checkpointer 按 thread_id 持久化会话)
4. 拿最后一条 AIMessage 的 content 返回

thread_id 规则:
- 调用方没传 → 生成新 UUID,视为新会话,首次注入 system prompt
- 调用方传了 → 继续会话;但防御性检查 thread 是否真的有历史,
  没有则按新会话处理(注入 system),避免 thread_id 失效丢 system
"""

import json
import uuid
from app.core.checkpoint import get_checkpointer
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select
from app.llm.chat_graph import build_chat_graph
from app.core.db import async_session_factory
from app.models import ResumeContent,Resume


async def _load_resume_snapshot(resume_id :uuid.UUID)->tuple[Resume,list[ResumeContent]]:
    """一次性查简历主表 + 所有段内容,供拼 system prompt 用。

    返回 (resume, contents_sorted_by_position)。
    用独立的 async_session_factory session,不依赖 FastAPI 请求作用域——
    因为工作流可能在不同上下文调用(端点、后台任务、测试)。
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Resume).where(Resume.id ==resume_id)
        )
        resume = result.scalar_one_or_none()
        if resume is None:
            raise ValueError(f"简历 {resume_id} 不存在")
        result = await session.execute(
            select(ResumeContent).where(ResumeContent.resume_id == resume_id).order_by(ResumeContent.position.asc())

        )
        contents = list(result.scalars())
    return resume,contents


def _build_system_prompt ( resume: Resume, contents: list [ResumeContent] ) -> str : 
    
    """拼 system prompt:告诉 LLM 它是谁、简历长啥样、能用什么工具。
    把简历全文塞进 system,LLM 不调工具也能回答"现在简历啥样"类问题;
    但要改内容时必须走 update_section 工具(不能在对话里直接吐新内容)。
    """ 
    sections_text = "\n" .join( 
        f"--- 段 {c.section_index} ( {c.title} ) ---\n" 
        f"状态: {c.status} | revision: {c.revision} \n" 
        f"内容:\n {json.dumps(c.content, ensure_ascii= False , indent= 2 ) if c.content else '(空)' } " 
        for c in contents
    ) or "(还没有任何段内容)" 
    return f"""你是简历编辑助手,用户通过自然语言对话修改简历。
            # 当前简历
            - ID: {resume. id } 
            - 求职者: {resume.applicant_name} 
            - 目标职位: {resume.target_position} 
            - 语气: {resume.tone} 
            - 段落数: {resume.section_count} 
            - 内容密度: {resume.content_density} 
            # 当前所有段内容(完整 JSON) {sections_text} 
            # 工具使用规则
            1. 用户问"简历现在怎么样""第2段写了什么"→ 可以直接根据上面的 system 内容回答,不必调工具
            2. 用户要"改某段""精简第3段"等修改操作 → 必须先调 get_section 看清楚结构,再调 update_section 写入
            3. update_section 的 new_content 必须是 JSON 字符串,结构跟原 content 完全一致(同样的字段名和嵌套)
            4. 改完后用一句话告诉用户改了什么,不要重复粘贴整段内容

            # 约束
            - 只改用户明确要求的部分,不要"顺手"改别的段
            - 拿不准时先问用户,不要瞎改
            """


async def run_chat(
    resume_id:uuid.UUID,
    message:str,
    thread_id:str|None=None,
)->dict:
    """跑一轮对话,返回 {reply, thread_id}。

    参数:
        resume_id: 简历 ID(决定查哪份简历的内容注入 system)
        message: 用户这一轮说的话
        thread_id: 会话 ID。None 表示新会话;传值表示继续上次会话

    返回:
        {"reply": str, "thread_id": str}
        reply 是 Agent 最后一条 AIMessage 的文本;thread_id 透传给调用方,
        下次继续对话时原样回传。
    """
    # 1. 决定 thread_id:没传就生成新的
    is_new_thread = thread_id is None
    if is_new_thread:
        thread_id = str(uuid.uuid4())
    
    config = {"configurable":{"thread_id":thread_id}}

    # 2. 构图(checkpointer 单例由调用方传入) 
    checkpointer = get_checkpointer()
    graph = build_chat_graph(checkpointer)

    # 3. 防御性检查:用户传的 thread_id 可能已失效(被清库或瞎编) 
    #    即使 is_new_thread=False,也要看 checkpointer 里有没有这个 thread 的状态
    if not is_new_thread:
        snapshot= await graph.aget_state(config)
        # snapshot.values 是 state 字典;新 thread 没历史时是 {}(空 dict)
        if not snapshot.values:
            is_new_thread = True
    # 4. 拼输入消息
    if is_new_thread:
        # 新会话:先查简历全文,注入 system prompt
        resume,contents = await _load_resume_snapshot(resume_id)
        system_promt = _build_system_prompt(resume,contents)
        input_messages = [
            SystemMessage(content = system_promt),
            HumanMessage(content=message)
        ]
    else:
        # 继续会话:checkpointer 已记住历史(含 system),只传新 user 消息
        input_messages = [HumanMessage(content=message)]

    # 5. 调图,checkpointer 自动按 thread_id 持久化这次对话
    result = await graph.ainvoke({"messages":input_messages},config=config)

    # 6. 拿最后一条 AIMessage 的 content 作为回复 
    #    ReAct 图循环:agent → tools → agent → ... → agent(无 tool_calls)→ END 
    #    所以 messages 末尾理论上一定是 AIMessage,用 reversed + isinstance 双保险

    last_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m,AIMessage)),None
    )
    reply = last_ai.content if last_ai else "(Agent 未返回回复)"
    return {"reply" : reply,"thread_id":thread_id}



