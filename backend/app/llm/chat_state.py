"""对话式编辑的图状态。

显式声明 messages 字段 + add_messages reducer,不依赖 MessagesState 继承——
TypedDict 继承时 Annotated[..., reducer] 元数据在某些 Python 版本下可能丢失,
导致 LangGraph 拿不到 reducer,input 的 messages 不被合并进 state(就是这次的 bug)。
"""
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

class ChatState(TypedDict):
    """ReAct 图状态:messages 是消息历史。

    add_messages reducer 会自动把每次节点返回的 {"messages": [...]} 追加到历史,
    而不是覆盖——这是 ReAct 多轮对话能"记住"上下文的关键。
    """
    messages: Annotated[list[AnyMessage], add_messages]