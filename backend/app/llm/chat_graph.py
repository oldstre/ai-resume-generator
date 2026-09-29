"""对话式编辑的 ReAct 图。

图结构:
    START → agent_node → should_continue ──(无 tool_calls)──→ END
                         │
                         └──(有 tool_calls)──→ tool_node → agent_node(循环)

agent_node:     调 LLM(bind_tools),返回 AIMessage(可能含 tool_calls)
tool_node:      执行所有 tool_calls,返回 ToolMessage 列表(LangGraph 预置)
should_continue:条件边,看上条 AIMessage 有没有 tool_calls

checkpointer 由调用方传入(workflows/chat.py 负责建 PostgresSaver),
图工厂只管组装,不管持久化——职责分离。
"""

from app.llm.chat_state import ChatState # ← 新增
from langgraph.graph import END, START, StateGraph
from app.llm.chat_tools import get_section, list_sections, update_section
from app.llm.client import create_chat_model
from langgraph.prebuilt import ToolNode
# Agent 能调用的全部工具
_TOOLS = [list_sections,get_section,update_section]


def build_chat_graph(checkpointer):
    """构建对话式编辑图。

    参数:
        checkpointer: LangGraph Checkpointer 实例(由调用方传入)。
            生产用 AsyncPostgresSaver,测试可传 MemorySaver。
            图工厂不关心具体类型,只要实现 BaseCheckpointSaver 协议。

    返回:
        编译后的 CompiledGraph。
        调用时传 config={"configurable": {"thread_id": "..."}},
        checkpointer 会自动按 thread_id 隔离会话状态。
    """
    # 1. 创建模型 + 绑定工具:bind_tools 让 LLM "看到" 工具箱
    model = create_chat_model()
    model_with_tools = model.bind_tools(_TOOLS)

# 2. agent 节点:调 LLM,返回 AIMessage
    async def agent_node(state:ChatState)->dict:
        """LLM 决策节点:看消息历史,决定回话还是调工具。

        system prompt 由 workflows/chat.py 注入到 state["messages"][0],
        这里直接把整个 messages 丢给模型(含 system + 历史 + 新 user 消息)。
        """
        response = await model_with_tools.ainvoke(state["messages"])
        # 返回新消息,add_messages reducer 会追加到历史(不覆盖)
        return {"messages":[response]}

# 3. tool 节点:用 LangGraph 预置的 ToolNode 
# #    ToolNode 自动读最后一条 AIMessage 的 tool_calls,逐个执行,
#  #    把结果包成 ToolMessage 返回——不用手写循环
    tool_node = ToolNode(_TOOLS)

# 4. 条件边函数:看最后一条消息有没有 tool_calls
    def should_continue(state:ChatState)->str:
        """有 tool_calls → 返回 "tools"(去 tool_node);没有 → 返回 END。"""
        last_message = state["messages"][-1]
        if hasattr(last_message,"tool_calls") and last_message.tool_calls:
            return "tools"
        return END
    
    graph = StateGraph(ChatState)
    graph.add_node("agent",agent_node)
    graph.add_node("tools",tool_node)
    graph.add_edge(START,"agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools":"tools",# 路径映射:should_continue 返回值 → 目标节点
            END:END
        }
    )
    graph.add_edge("tools","agent") # 工具执行完回到 agent 继续决策

    return graph.compile(checkpointer=checkpointer)
