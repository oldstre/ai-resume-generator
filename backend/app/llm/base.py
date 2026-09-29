"""LLM 抽象层：定义生成器接口和输入输出 Schema。

这一层不涉及具体模型（DeepSeek/通义千问等），只定义"职责"。
后续换模型时只需新写一个实现类，业务代码不变。


┌─────────────────────────────────────────────┐
│ Input  = 函数的"入参类型"                  │
│          告诉 LLM："我给你这些材料"         │
│          用 Pydantic Field 约束长度/范围     │
│          → 传进来的数据自动校验              │
├─────────────────────────────────────────────┤
│ Draft  = 函数的"返回类型"                  │
│          告诉 LLM："你应该吐出这种形状"     │
│          用 Pydantic 类定义字段             │
│          → LLM 返回的 JSON 自动校验是不是这个形状 │
│          → 不是就抛 InvalidModelOutputError │
├─────────────────────────────────────────────┤
│ Generator = 函数本身的"接口签名"           │
│          async def generate(payload) -> Draft│
│          只定义"有这个方法"，不写实现       │
└─────────────────────────────────────────────┘
"""


from pydantic import BaseModel, Field
from typing import Protocol
from app.schemas.resume_outline import ResumeSectionDraft
from app.schemas.resumes import ContentDensity


class OutlineGenerationInput(BaseModel):
    """传给 LLM 生成大纲的输入。"""
    title : str = Field(min_length=1,max_length=200)
    applicant_name:str = Field(min_length=1,max_length=100)
    target_position : str = Field (min_length=1,max_length=100)
    tone:str =Field(min_length=1,max_length=32)
    section_count :int = Field(ge=3,le=12)
    content_density :ContentDensity = "medium"


class OutlineDraft(BaseModel):
    """LLM 返回的大纲草稿。"""
    #直接复用第22课写的`ResumeSectionDraft` ——不要重新定义一个"看起来一样"的 schema。复用保证一致性，prompt 里给 LLM 看的 schema 和数据库存的结构完全一致。
    sections:list[ResumeSectionDraft] =Field(min_length=1,max_length=12)

class OutlineGenerator(Protocol):
    """大纲生成器接口。
        以及说清楚protocol是什么？
        普通抽象基类（ABC)，实现类必须要显示继承，Protocol的话，实现类不需要继承，只要有同名方法签名就行

    
    """
    async def generate(self,payload:OutlineGenerationInput)->OutlineDraft:
        """根据简历基本信息生成大纲草稿。"""   
        ...  #Protocol 方法体写`...` （Ellipsis）是 Python 约定——表示"方法体为空，只定义签名"。`pass` 也行，但社区更倾向用`...` 。
 

class ContentGenerationInput(BaseModel):
    """传给 LLM 生成简历单段内容的输入。"""
# 简历整体上下文：让 LLM 知道为谁、什么职位、什么语气写
    title : str = Field(min_length=1,max_length=200)
    applicant_name:str  = Field(min_length=1,max_length=100)
    target_position : str = Field(min_length=1,max_length=100)
    tone :str = Field(min_length=1,max_length=32)
    content_density :ContentDensity = "medium"

# 这一段在大纲里的定位
    section_index: int = Field(ge=0) # 第几段（0-based）
    section_count:int = Field(ge=1) # 总段数，让 LLM 知道整体规模
    section_title : str = Field(min_length=1,max_length=100) # 大纲里这段标题
    section_objective : str = Field(min_length=1,max_length=500) # 大纲里这段的"写什么"描述

    # 自纠环用：上一轮生成时记录的问题，让 LLM 这次定向改 
    issues: list [ str ] = Field(default_factory= list )

class ContentDraft(BaseModel):
    """LLM 返回的单段内容草稿。"""
    # 段落标题：让 LLM 可以润色大纲给的标题（比如 "技能" -> "核心技能"）
    title : str = Field(min_length=1,max_length=100)
    # 段落正文：结构化 JSONB 内容 
    # # 不同段类型 schema 不同，这里不强约束——交给 prompt 约束 
    # # 比如： 
    # #   技能段   -> [{"skill":"Python","years":3,"level":"熟练"}, ...] 
    # #   项目段   -> [{"name":"X项目","role":"后端","tech":["Python","FastAPI"],"desc":"..."}] 
    # #   工作段   -> [{"company":"A公司","period":"2023-2025","role":"工程师","desc":"..."}]
    content :dict


class ContentGenerator(Protocol):
    """单段内容生成器接口。"""
    async def generate(self, payload: ContentGenerationInput) -> ContentDraft:
        """根据简历基本信息+大纲某一段生成该段最终内容。"""




class ProofReport(BaseModel):
    """校对 agent 的输出：质检报告。

    和 services/quality_check.py 的 QualityReport 区别：
    - QualityReport 是纯代码规则检查的输出
    - ProofReport 是 LLM 校对 agent 的输出
    两者字段语义一致，但来源不同——C 多智能体的校对 agent 用这个。
    """
    passed:bool # True=通过 False=有问题
    issues:list[str]  # 没过：问题列表（每条一句话带修改建议）；过了：空数组


class SectionPlan(BaseModel):
    """主编给单段的指引。

    撰写 agent 会把这三个字段塞进 system prompt，按指引产出内容。
    """

    focus: str =Field(min_length=1,max_length=200) # 本段重点写什么（1-2 句话）
    tone_hint: str = Field(min_length=1,max_length=100) # 语气提示（如"专业严谨"/"成就导向"）
    avoid:list[str] = Field(default_factory=list)# 避免内容（防跨段重复，如"不要重复工作段的 XX 公司"）

class ChiefPlan(BaseModel):
    """主编的分稿计划：段号字符串 → 段指引。

    用 dict[str, SectionPlan] 是为了让 LLM 输出 JSON 时按段号组织，
    撰写 agent 用 plan["0"] 拿段 0 的指引。
    """
    sections:dict[str,SectionPlan] = Field(min_length=1) 
