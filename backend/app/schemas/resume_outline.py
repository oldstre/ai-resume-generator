




from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, Field,ConfigDict


OutlineStatus = Literal["generating","draft","confirmed","failed"]

class ResumeSectionDraft(BaseModel):
    """大纲里的一段：标题 + 这段要写什么。"""
    title:str = Field(min_length=1,max_length=100)
    content : str = Field(min_length=1,max_length=500)

class ResumeOutlinePublic(BaseModel):
    """大纲出参。"""
    model_config = ConfigDict(from_attributes= True )

    id:uuid.UUID
    resume_id:uuid.UUID
    status:OutlineStatus
    sections:list[ResumeSectionDraft]
    revision:int
    job_id : str | None
    error:str | None
    created_at : datetime
    updated_at : datetime

class ResumeOutlineUpsert(BaseModel):
    """手动写大纲入参。"""
    sections: list[ResumeSectionDraft] = Field(min_length=1, max_length=10)



class ResumeOutlineRevisionRequest(BaseModel):
    """客户端传 revision 用于乐观锁校验。"""
    revision: int = Field(ge=1)
