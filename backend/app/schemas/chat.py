"""对话式编辑的请求/响应 schema。"""
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    """对话式编辑请求体。"""
    message: str = Field(..., min_length=1, description="用户这一轮的输入")
    thread_id: str | None = Field(
        None,
        description="会话 ID;首次对话不传,后续轮原样回传上一次返回的 thread_id",
    )

class ChatResponse(BaseModel):
    """对话式编辑响应体。"""
    reply: str = Field(..., description="Agent 这一轮的回复文本")
    thread_id: str = Field(..., description="会话 ID,下次继续对话原样回传")