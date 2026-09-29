"""多段内容生成的状态 schema + 自定义 reducer。

C+G 融合方案的基础设施：
- MultiSectionState：顶层档案柜（主编 plan + 5 段并发子状态 + 跨段校验结果）
- SectionGenState：单段子档案（每个 Send 派出去的子图用它）
- merge_sections：自定义 reducer，按 section_index 整份覆盖

类比：
  状态 = 档案柜
  reducer = 档案管理员
  Send 派 5 个子任务 = 5 个员工同时往柜里塞各自那段档案
  reducer 说："按段号整份覆盖，不动别的段"
"""


from typing import Annotated, TypedDict

from app.llm.base import ContentDraft, ContentGenerationInput


def merge_sections(left:dict,right : dict)->dict:
    """自定义 reducer：按段号整份覆盖。

    LangGraph 调用时机：每个 Send 派出去的子图跑完返回
    {"sections": {段号字符串: 整份 SectionGenState}} 时，
    LangGraph 拿这个返回值调 reducer(left=当前 sections, right=本次返回 sections)。

    left：当前档案柜里 sections 字段的值
    right：本次 Send 返回的 sections 字段值（一般只有一个 key）

    返回：合并后的新 dict（不要 in-place 改 left，LangGraph 内部缓存会乱）

    策略：右整份覆盖左——同段号最后写赢，不同段号互不影响。
    """
    out = {**left} #1.复制left
    out.update(right) #2.用right覆盖
    return out #3.返回新字典


class SectionGenState(TypedDict):
    """单段子状态：每个 Send 派出去的子图用它。

    和现有 ContentGenState 的区别：多了 section_index（段号）和 plan（主编指引）。

    注意：TypedDict 不支持默认值，所有默认字段（attempts/issues/issues_history/draft）
    必须在 graph.ainvoke({...}) 入参里塞齐。
    """
# ── 段标识 ──
    section_index : int # 段号（0-based，子图节点用它给 SSE 事件打标签）

# ── 输入：简历基本信息 + 大纲某一段信息 ──
    payload: ContentGenerationInput # 复用现有 schema（在 base.py）
    plan:dict  # 主编 agent 给的本段增强指引（focus/tone_hint/avoid）

# ── 中间状态：自纠环每轮可能变化 ──
    draft:ContentDraft| None # LLM 最新生成的草稿
    issues: list[str] # 喂给 writer agent 的问题列表（空=通过）
    issues_history:list[dict] # 硬约束：键名带复数 s（避免 issue_history 拼错
    attempts:int # 当前尝试次数
    max_retries:int  # 最大重试次数（C+G 方案固定 2，控制 LLM 调用预算）



class MultiSectionState(TypedDict):
    """多段图顶层状态：主编 + 5 段并发 + 整体校验都在这里流转。

    Annotated[dict, merge_sections] 告诉 LangGraph：
    "sections 字段每次更新时都调 merge_sections(旧值, 新值) 合并"
    这是 LangGraph 自定义 reducer 的标准写法。
    """
    # ── 主编 agent 产出 ──
    chief_plan :dict # {"0": {"focus":..., "tone_hint":..., "avoid":[...]}, "1": {...}}

    # ── 5 段并发子状态：用 reducer 合并 ──
    sections:Annotated[dict[str,SectionGenState],merge_sections]

# ── 整体一致性校验结果 ──
    cross_issues: list[str] # 跨段校验发现的问题，空=通过

# ── 元信息 ──
    section_count :int # 总段数（fan_out 用它派发 N 个 Send）
    round:int # 当前第几轮（第一版固定 1，预留重排扩展）
    max_rounds:int # 最大轮数（第一版固定 1，不自动重排）

# ── 落库用：简历 ID + 大纲 sections + 简历基本信息（chief_editor 和 all_done 节点用）──
    resume_id :str # UUID 字符串形式（落库时转 UUID）
    outline_sections:list[dict] # 大纲原样 [{"title":..., "content":...}]
    resume_basics: dict # 简历基本信息 {"title":..., "applicant_name":..., "target_position":..., "tone":..., "content_density":..., "section_count":...}`
