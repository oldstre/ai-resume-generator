




from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict


ResumeContentStatus =Literal["pending","generating","ready","failed"]

class ResumeContentPublic(BaseModel):
    """段内容出参：API 返回给前端时用这个 schema 序列化。
    "ORM 模型 → API 出参"
    """
    model_config = ConfigDict(from_attributes=True)#允许 Pydantic 从 ORM 对象的属性（如`content.id` ）自动构造 Pydantic 实例

    id:uuid.UUID
    resume_id :uuid.UUID
    section_index : int 
    position :int
    title:str 
    status :ResumeContentStatus
    content:dict |None
    issues:list[dict]
    error:str | None
    revision: int
    created_at:datetime
    updated_at:datetime


class ResumeContentUpdate(BaseModel):
    """段内容入参：PATCH 编辑用。

    所有字段都可选——前端只传想改的字段，没传的不动。
    """
    content: dict | None = None   # 结构化正文
    title: str | None = None       # 段标题（比如把"技能"改成"核心技能"）
