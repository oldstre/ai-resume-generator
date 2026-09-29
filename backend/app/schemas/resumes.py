""" 
    基础概念：
     ## Pydantic 是啥
Pydantic 是数据格式校验器 。在三个地方用它：

位置 干啥 类比 
入参（请求体） 校验前端传来的 JSON字段对不对 前台接单时检查单子填全没 
出参（响应体） 把 ORM 对象转成 JSON返回 出餐前摆盘标准化 
配置（`.env` ） 把环境变量变成 Python对象 看配置本

Pydantic vs SQLAlchemy 区别 ：

    Pydantic        SQLAlchemy 
用途 数据传输格式    数据存储结构 
类基类 `BaseModel`  `Base` 
类型注解 `: str`   `Mapped[str]`
 触发时机 每次请求    启动时建表

简历项目里：

- `models/resume.py` 的`Resume(Base)` → 描述 数据库表 长啥样
- `schemas/resume.py` 的`ResumeCreate(BaseModel)` → 描述 前端传来的 JSON 长啥样
- `schemas/resume.py` 的`ResumePublic(BaseModel)` → 描述 返回给前端的 JSON 长啥样   

""" 


from __future__ import annotations

from fileinput import FileInput
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Tone = Literal["professional", "plain", "punchy"]
ResumeStatus = Literal["draft", "outline_ready", "generating", "ready"]
ContentDensity = Literal["concise", "medium", "detailed"]

# 简历段落数：教育/工作/技能/项目经历/自我评价等，常见 4-8 段
MIN_SECTION_COUNT = 3
MAX_SECTION_COUNT = 12

class ResumeCreate(BaseModel):
    """创建简历的入参。"""

    title: str = Field(min_length=1, max_length=200)
    applicant_name: str = Field(min_length=1, max_length=100)
    target_position: str = Field(min_length=1, max_length=100)
    tone: Tone = "professional"
    section_count: int = Field(default=5, ge=MIN_SECTION_COUNT, le=MAX_SECTION_COUNT)
    template_id: str = Field(default="modern", max_length=50)
    content_density: ContentDensity = "medium"

class ResumePublic(BaseModel):
    """简历出参：返回给前端的完整字段。"""
    #from_attributes=True：让 Pydantic 能“直接吃”ORM 对象
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    applicant_name: str
    target_position: str
    tone: Tone
    section_count: int
    template_id: str
    template_overrides: dict[str, Any] = Field(default_factory=dict)
    content_density: ContentDensity
    status: ResumeStatus
    created_at: datetime
    updated_at: datetime


class ResumeUpdate(BaseModel):
    """修改简历的入参。所有字段都 Optional，支持部分更新。
    - 类型`T | None` ：Pydantic 把字段标成"可以是指定类型，也可以是 None"
    - `default=None` ：未传字段默认是 None
    类型注解和默认值的关系 ：`tone: Tone | None = None` 表示"tone 字段可以传 'professional'/'plain'/'punchy'，也可以不传（默认 None）"
    """
    # 关键：Field(default=None, ...) 让字段不传时默认是 None， 
    # 配合 model_dump(exclude_unset=True) 才能区分"没传"和"传了 None"
    title :str |None = Field(default=None,min_length=1,max_length=200)
    applicant_name :str |None = Field(default=None,min_length=1,max_length=100)
    target_position :str |None = Field(default=None,min_length=1,max_length=100)
    tone:Tone |None = None
    section_count :int |None = Field(default=None,ge=MIN_SECTION_COUNT,le=MAX_SECTION_COUNT)
    template_id :str | None = Field(default=None,max_length=50)
    template_override:dict[str,Any]|None = None
    content_density: ContentDensity | None = None

