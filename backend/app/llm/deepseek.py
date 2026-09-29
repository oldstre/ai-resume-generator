"""DeepSeek 大纲生成器：把 prompt + 硬约束 + 校验封装成一个 Protocol 实现。

实现第25课定义的 OutlineGenerator Protocol——
注意：这里不显式继承 Protocol，只要方法签名匹配就算实现（鸭子类型）。
"""
from __future__ import annotations

import json

from langchain_core.language_models.chat_models import BaseChatModel
from requests import api
from app.llm.tools import search_web
from app.llm.base import (
    ChiefPlan,
    ContentDraft,
    ContentGenerationInput,
    OutlineDraft,
    OutlineGenerationInput,
    ProofReport,
    SectionPlan,
)
from app.llm.client import StructuredChatClient
from app.llm.errors import (
    InvalidModelOutputError,
    InvalidOutlineOutputError,
    InvalidContentOutputError,
    LLMNotConfiguredError,
)
from app.llm.retriever import retrieve_samples
__all__ = ["DeepSeekOutlineGenerator", "InvalidOutlineOutputError", "LLMNotConfiguredError"]


# 简历常见标准段名——让 LLM 倾向使用这些名字，保证大纲段名一致性
_PREFERRED_TITLES = (
    "个人信息与求职意向",
    "职业概述",
    "专业技能",
    "项目经历",
    "工作经历",
    "教育经历",
    "工作经历与教育背景",
    "自我评价",
)


class DeepSeekOutlineGenerator:
    """大纲生成器：实现 OutlineGenerator Protocol。"""

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        api_key: str = "",
        chat: StructuredChatClient | None = None,
    ) -> None:
        # 依赖注入：允许传 chat（已构造的客户端）或 model（要内部构造）
        # 测试时可以传 mock chat，不用真的调 LLM
        if chat is not None:
            self._chat = chat
        elif model is not None:
            self._chat = StructuredChatClient(model=model, api_key=api_key)
        else:
            raise TypeError("需要 model 或 chat")

    async def generate(self, payload: OutlineGenerationInput) -> OutlineDraft:
        """实现 Protocol 方法：根据简历基本信息生成大纲。"""
        try:
            draft = await self._chat.complete(
                OutlineDraft,
                system=self._system_prompt(),
                user=self._user_prompt(payload),
                purpose="生成大纲",
            )
        except InvalidModelOutputError as error:
            # 异常转译：通用的 InvalidModelOutputError 转成业务的 InvalidOutlineOutputError
            # 业务层捕获时能精确处理（重试大纲生成 vs 重试内容生成）
            raise InvalidOutlineOutputError(
                "模型返回的大纲 JSON 不符合约定结构"
            ) from error

        # 双重保险：LLM 返回后用代码再校验业务规则
        self._validate_draft(draft, section_count=payload.section_count)
        return draft

    def _system_prompt(self) -> str:
        """system prompt：人设 + JSON 结构 + 硬约束。"""
        example = {
            "sections": [
                {"title": "教育经历", "content": "XX大学 计算机本科 2018-2022"},
                {"title": "工作经历", "content": "XX公司 Python后端 2022-至今"},
            ]
        }
        return (
            "你是资深 HR，擅长为求职者设计简历结构。"
            "必须只输出一个 JSON 对象，不要 Markdown，不要额外说明。\n"
            "JSON 结构必须为：\n"
            '{"sections":[{"title":"...","content":"..."}]}\n'
            f"示例：{json.dumps(example, ensure_ascii=False)}\n"
            "硬性约束：\n"
            "1. sections 数组长度必须精确等于用户给定的 section_count。\n"
            "2. 每段 title 必须是中文，且简明（≤30 字符）；"
            f"倾向使用标准段名：{ '、'.join(_PREFERRED_TITLES) }。\n"
            "3. content 必须是具体内容描述，禁止「相关经验」「详细介绍」这类空话；"
            "可写明关键信息（时间/学校/公司/技术栈）但不要编造个人具体数据。\n"
            "4. title 与 content 使用中文。\n"
            "5. 顺序建议：个人信息 → 职业概述 → 技能 → 项目 → 工作/教育 → 自我评价。"
        )

    def _user_prompt(self, payload: OutlineGenerationInput) -> str:
        """user prompt：具体任务 + 输入参数。"""
        body = {
            "title": payload.title,
            "applicant_name": payload.applicant_name,
            "target_position": payload.target_position,
            "tone": payload.tone,
            "section_count": payload.section_count,
            "content_density": payload.content_density,
        }
        # 内容密度提示——告诉 LLM 每段写多详细
        density_hint = {
            "concise": "请控制每段内容简洁，1-2 句话。",
            "medium": "每段内容适中，2-3 句话。",
            "detailed": "每段内容详尽，3-5 句话，包含具体技术栈或成就。",
        }.get(payload.content_density, "内容密度未指定。")
        return (
            f"请根据以下简历参数生成 {payload.section_count} 段大纲 JSON。\n"
            f"{density_hint}\n"
            f"{json.dumps(body, ensure_ascii=False)}"
        )

    def _validate_draft(
        self,
        draft: OutlineDraft,
        *,
        section_count: int,
    ) -> None:
        """LLM 返回后的业务规则校验——双重保险的第二层。

        with_structured_output 只能保证 JSON 结构对（字段名/类型），
        没法强制业务规则（如段数必须等于5）——这里补上。
        """
        if len(draft.sections) != section_count:
            raise InvalidOutlineOutputError(
                f"大纲段数不符：期望 {section_count} 段，实际 {len(draft.sections)} 段"
            )
        for index, section in enumerate(draft.sections, start=1):
            if not section.title.strip():
                raise InvalidOutlineOutputError(f"第 {index} 段 title 为空")
            if not section.content.strip():
                raise InvalidOutlineOutputError(f"第 {index} 段 content 为空")




class DeepSeekContentGenerator:
    """内容生成器基类：提供 _user_prompt / _example_for / _validate_draft 共用方法。

    子类 DeepSeekWriterAgent 重写 generate(payload, plan) + _system_prompt(payload, plan)，
    接收主编 plan 指引。父类本身不再有 generate() 方法——
    旧的单段同步 / 单段流式入口（generate_content / generate_content_streaming）
    已经被多段并发流程（generate_all_contents_streaming）取代。
    """

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        api_key: str = "",
        chat: StructuredChatClient | None = None,
    ) -> None:
        # 依赖注入：和 OutlineGenerator 完全一样
        if chat is not None:
            self._chat = chat
        elif model is not None:
            self._chat = StructuredChatClient(model=model, api_key=api_key)
        else:
            raise TypeError("需要 model 或 chat")

    def _system_prompt(self, payload: ContentGenerationInput) -> str:
        """system prompt：人设 + JSON 结构 + 硬约束 + 段类型范例。"""
        example = self._example_for(payload.section_title)

        return (
            "你是资深简历撰写专家，擅长为求职者撰写具体简历段落内容。"
            "如果需要了解目标岗位的最新技能要求或行业趋势，可以先调用 search_web 工具搜索，"
            "然后基于搜索结果生成更精准的简历内容。\n"
            "工具调用完成后，最终输出必须是一个 JSON 对象，不要 Markdown，不要额外说明。\n"
            "JSON 结构必须为：\n"
            '{"title":"段落标题","content":{...结构化内容...}}\n'
            f"本段是「{payload.section_title}」，参考范例：\n"
            f"{json.dumps(example, ensure_ascii=False)}\n"
            "硬性约束：\n"
            "1. title 必须是中文，简明（≤30 字符）。\n"
            "2. content 必须是非空 JSON 对象，字段值不得为空字符串或空数组。\n"
            "3. 不要编造具体公司名、学校名、年份——用占位符如「XX公司」「XX大学」。\n"
            "4. 内容密度遵循用户给定的 content_density。\n"
            "5. 若 issues 非空，必须针对每个问题做定向修复，不要从头重写。"
        )

    def _user_prompt(self, payload: ContentGenerationInput, samples: list | None = None) -> str:
        """user prompt：具体任务 + 输入参数 + RAG 参考材料。

        samples 是检索到的同岗位优秀样本，借鉴结构和用词，不照抄具体数据。
        """
        body = {
            "title": payload.title,
            "applicant_name": payload.applicant_name,
            "target_position": payload.target_position,
            "tone": payload.tone,
            "content_density": payload.content_density,
            "section_index": payload.section_index,
            "section_count": payload.section_count,
            "section_title": payload.section_title,
            "section_objective": payload.section_objective,
            "issues": payload.issues,
        }
        density_hint = {
            "concise": "请控制内容简洁，每段 1-2 条要点。",
            "medium": "内容适中，每段 2-3 条要点。",
            "detailed": "内容详尽，每段 3-5 条要点，包含具体技术栈或成就。",
        }.get(payload.content_density, "内容密度未指定。")

        # RAG 参考材料段——借鉴结构和用词，不照抄具体数据
        if samples:
            ref_text = json.dumps(samples, ensure_ascii=False, indent=2)
            ref_section = (
                "\n参考材料（同岗位优秀简历的同类段落，借鉴结构和用词，"
                "不要照抄具体公司/学校/数值）：\n"
                f"{ref_text}\n"
            )
        else:
            ref_section = ""

        return (
            f"请为简历「{payload.title}」的第 {payload.section_index + 1} 段「{payload.section_title}」生成正文内容。\n"
            f"该段在大纲里的写什么描述：{payload.section_objective}\n"
            f"{density_hint}\n"
            f"{json.dumps(body, ensure_ascii=False)}"
            f"{ref_section}"
        )

    def _example_for(self, section_title: str) -> dict:
        """根据段标题给 LLM 一个范例 JSON。
        让 LLM 按这个形状产出，但不强约束——content: dict。
        """
        # 关键词匹配：标题含什么字就给对应范例
        title = section_title
        if "技能" in title:
            return {
                "title": "专业技能",
                "content": {
                    "items": [
                        {"skill": "Python", "years": 3, "level": "熟练"},
                        {"skill": "FastAPI", "years": 2, "level": "掌握"},
                    ]
                },
            }
        if "项目" in title:
            return {
                "title": "项目经历",
                "content": {
                    "items": [
                        {
                            "name": "XX系统",
                            "role": "后端开发",
                            "tech": ["Python", "FastAPI", "PostgreSQL"],
                            "desc": "负责 XX 模块，实现 YY 功能。",
                        }
                    ]
                },
            }
        if "工作" in title or "经历" in title:
            return {
                "title": "工作经历",
                "content": {
                    "items": [
                        {
                            "company": "XX公司",
                            "period": "2022-至今",
                            "role": "Python 后端工程师",
                            "desc": "负责 XX 业务线，主导 YY 重构。",
                        }
                    ]
                },
            }
        if "教育" in title:
            return {
                "title": "教育经历",
                "content": {
                    "items": [
                        {
                            "school": "XX大学",
                            "major": "计算机科学与技术",
                            "period": "2018-2022",
                            "degree": "本科",
                        }
                    ]
                },
            }
        # 默认：自我评价 / 职业概述 / 其他
        return {
            "title": section_title,
            "content": {
                "paragraphs": ["一段总结性文字，3-5 句话。"]
            },
        }

    def _validate_draft(self, draft: ContentDraft) -> None:
        """LLM 返回后的业务规则校验——双重保险的第二层。"""
        if not draft.title.strip():
            raise InvalidContentOutputError("段落标题为空")
        if not draft.content:
            raise InvalidContentOutputError("段落内容为空")
        # content 必须是 dict 且至少有一个键
        if not isinstance(draft.content, dict) or len(draft.content) == 0:
            raise InvalidContentOutputError("段落内容不是有效的 JSON 对象")

class DeepSeekWriterAgent(DeepSeekContentGenerator):
    """撰写 agent：C 多智能体的"产出"角色。

    继承 DeepSeekContentGenerator 复用 _user_prompt / _example_for / _validate_draft，
    只重写 generate（多接 plan 参数）和 _system_prompt（塞主编指引）。

    和父类的区别：
    - generate(payload, plan) 多一个 plan 参数
    - _system_prompt 在父类基础上追加"主编指引"段
    """

    async def generate(self, payload: ContentGenerationInpu,plan:dict |None = None) -> ContentDraft:
        """按主编 plan 产出单段内容。plan=None 时退化为父类行为（兼容老端点）。"""
        samples = await retrieve_samples(payload.target_position, payload.section_title)
        try:
            draft = await self._chat.complete_with_tools(
                ContentDraft,
                system=self._system_prompt(payload,plan),
                user = self._user_prompt(payload,samples),
                purpose="撰写 agent 生成内容" ,
                tools = [search_web],
            )
        except InvalidModelOutputError as error:
            raise InvalidContentOutputError( "撰写 agent 返回的 JSON 不符合约定结构" ) from error
        self._validate_draft(draft)
        return draft

    def _system_prompt ( self, payload: ContentGenerationInput, plan: dict | None = None ) -> str : 
        """在父类 system prompt 基础上追加主编 plan 指引段。""" 
        # 复用父类的硬约束 + 范例（不重写一遍） 
        base_prompt = super ()._system_prompt(payload) 
        if not plan: 
            return base_prompt
        plan_text = ( "\n主编指引（必须遵循）：\n" 
        f"- 本段重点： {plan.get( 'focus' , '' )} \n" 
        f"- 语气提示： {plan.get( 'tone_hint' , '' )} \n" 
        f"- 避免内容： {plan.get( 'avoid' , [])} \n" ) 
        return base_prompt + plan_text


class DeepSeekProofReaderAgent:
    """校对 agent：C 多智能体的"反馈"角色。

    调 LLM 检查草稿质量，返回 ProofReport (passed + issues)。
    和 services/quality_check.py 的 check_content 区别：
    - check_content 是纯代码规则版（快，但只能查空字段/空数组）
    - 这个是 LLM 校对版（慢，但能查语义问题，给定向修改建议）

    自纠环里：writer 产出 → proofreader 检查 → 没过就把 issues 喂回 writer 重写。
    """
    def __init__(
        self,
        *,
        model:BaseChatModel | None = None,
        api_key :str = "",
        chat:StructuredChatClient |None = None
        ) -> None:
        # 依赖注入：同 DeepSeekContentGenerator，复用同一 chat 客户端
        if chat is not None:
            self._chat = chat
        elif  model is not None:
            self._chat = StructuredChatClient(model=model,api_key=api_key)
        else : 
            raise  TypeError( "需要 model 或 chat" )

    async def review(self,draft:ContentDraft,payload : ContentGenerationInput)->ProofReport:
        """检查草稿。返回 ProofReport（passed + issues）。"""
        try:
            report = await self._chat.complete(
                ProofReport,
                system=self._system_prompt(payload),
                user = self._user_prompt(draft,payload),
                purpose="校对 agent 检查内容"
            )
        except InvalidModelOutputError as error:
            raise InvalidContentOutputError( "校对 agent 返回的 JSON 不符合约定结构" ) from error
        return report

    def _system_prompt ( self, payload: ContentGenerationInput ) -> str : 
            """校对专家人设 + JSON 结构 + 硬约束。""" 
            return ( 
                "你是资深简历校对专家，擅长发现简历段落里的质量问题并给出定向修改建议。" 
                "必须只输出一个 JSON 对象，不要 Markdown，不要额外说明。\n" 
                "JSON 结构必须为：\n" 
                '{"passed": true/false, "issues": ["问题1：建议...", "问题2：建议..."]}\n' 
                "硬性约束：\n" 
                "1. passed=true 当且仅当内容符合岗位要求、字段非空、无矛盾、无编造。\n" 
                "2. issues 是字符串数组，每个问题一句话描述 + 修改建议。\n" 
                "3. 通过时 issues 必须是空数组 []。\n" 
                "4. 不要编造问题，发现真问题才报。\n" 
                "5. 段标题含「技能」时检查技能项是否合理；含「项目/工作」时检查时间线是否清晰；含「教育」时检查学校/专业是否完整。" 
                )
    
    def _user_prompt ( self, draft: ContentDraft, payload: ContentGenerationInput ) -> str : 
        """把草稿和段落目标塞给 LLM 检查。""" 
        body = { 
            "section_title" : payload.section_title, 
            "section_objective" : payload.section_objective, 
            "target_position" : payload.target_position, 
            "tone" : payload.tone, 
            "content_density" : payload.content_density, 
            "draft_title" : draft.title, 
            "draft_content" : draft.content,
        } 
        return ( 
            f"请检查以下简历段落内容是否符合要求。\n" 
            f"段落：{payload.section_title} \n" 
            f"目标：{payload.section_objective} \n" 
            f" {json.dumps(body, ensure_ascii= False )}" 
            )
class ChiefEditorAgent:
    """主编 agent：C 多智能体的"调度"角色。

    读大纲 + 简历基本信息，产出分稿计划（每段的 focus/tone_hint/avoid）。
    后续 5 个撰写 agent 各自拿到自己段的 plan 指引。

    和 DeepSeekContentGenerator 区别：
    - 输入是大纲 + 简历信息（不是单段 payload）
    - 输出是 ChiefPlan（不是 ContentDraft）
    - 用 complete()（不带工具，主编不需要联网搜索）
    """

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        api_key: str = "",
        chat: StructuredChatClient | None = None,
    ) -> None:
        # 依赖注入：同其他 Agent
        if chat is not None:
            self._chat = chat
        elif model is not None:
            self._chat = StructuredChatClient(model=model, api_key=api_key)
        else:
            raise TypeError("需要 model 或 chat")

    async def generate(self, outline_sections: list[dict], resume_basics: dict) -> ChiefPlan:
        """读大纲 + 简历信息，产出分稿计划。

        参数：
            outline_sections: 大纲原样 [{"title":..., "content":...}]
            resume_basics: 简历基本信息 dict（title/applicant_name/target_position 等）
        返回：ChiefPlan（包含 sections dict）
        """
        try:
            plan = await self._chat.complete(
                ChiefPlan,
                system=self._system_prompt(),
                user=self._user_prompt(outline_sections, resume_basics),
                purpose="主编制定分稿计划",
            )
        except InvalidModelOutputError as error:
            raise InvalidContentOutputError("主编返回的分稿计划不符合约定结构") from error
        return plan

    def _system_prompt(self) -> str:
        """主编人设 + JSON 结构 + 硬约束。"""
        return (
            "你是资深简历主编，擅长根据求职者的大纲和基本信息制定分稿计划。"
            "分稿计划要确保各段风格统一、内容互补、避免跨段重复。"
            "必须只输出一个 JSON 对象，不要 Markdown，不要额外说明。\n"
            "JSON 结构必须为：\n"
            '{"sections": {"0": {"focus":"...", "tone_hint":"...", "avoid":["..."]}, "1": {...}}}\n'
            "硬性约束：\n"
            "1. sections 字典的 key 是段号字符串（\"0\"/\"1\"/...），必须覆盖所有段。\n"
            "2. focus 是本段重点写什么（1-2 句话），不要空话。\n"
            "3. tone_hint 是本段语气提示（如\"专业严谨\"/\"成就导向\"/\"简洁有力\"）。\n"
            "4. avoid 是本段要避免的内容（如\"不要重复工作经历段的 XX 公司\"/\"不要列技能段已有的项\"）。\n"
            "5. 段间风格统一，重点互补——避免不同段写重复的公司/学校/技能。\n"
            "6. 用中文。"
        )

    def _user_prompt(self, outline_sections: list[dict], resume_basics: dict) -> str:
        """把大纲和简历基本信息塞给 LLM。"""
        body = {
            "resume_basics": resume_basics,
            "outline_sections": outline_sections,
        }
        return (
            f"请根据以下大纲和简历基本信息，制定 {len(outline_sections)} 段的分稿计划。\n"
            f"{json.dumps(body, ensure_ascii=False)}"
        )