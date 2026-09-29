"""单元测试共享 fixtures。

核心组件 FakeChatClient——StructuredChatClient 的测试替身。
设计思路（面试点）：

不 mock SDK 层（ChatOpenAI），而是 mock 业务封装层（StructuredChatClient）。
原因：agent 依赖的是封装层接口（complete / complete_with_tools），
不是 SDK 接口。这样换 LLM SDK 不影响测试，mock 粒度也更贴近业务语义。

三层 mock 粒度：
  1. FakeChatClient → 测 agent 内部逻辑（prompt 拼接 / schema 校验 / 异常转译）
  2. AsyncMock(spec=Agent) → 测 graph 状态流转（节点编排 / 自纠环 / reducer）
  3. monkeypatch 模块属性 → 测 E2E 管线（SSE 事件序列 / 落库）
"""
from unittest.mock import AsyncMock

import pytest

from app.llm.base import (
    ChiefPlan,
    ContentDraft,
    ContentGenerationInput,
    ProofReport,
    SectionPlan,
)
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)


# ──────────────────────────────────────────────
# FakeChatClient：StructuredChatClient 的测试替身
# ──────────────────────────────────────────────

class FakeChatClient:
    """按 purpose 返回预置 Pydantic 对象，记录调用参数供断言。

    用法：
        chat = FakeChatClient({
            "生成大纲": OutlineDraft(sections=[...]),
            "校对 agent 检查内容": ProofReport(passed=True, issues=[]),
        })
        agent = DeepSeekOutlineGenerator(chat=chat, api_key="fake")
        result = await agent.generate(payload)
        # 断言 prompt 内容
        assert "资深 HR" in chat.call_log[0]["system"]
    """

    def __init__(self, responses: dict[str, object] | None = None):
        self._responses = responses or {}
        self.call_log: list[dict[str, str]] = []

    async def complete(self, schema, *, system, user, purpose):
        self.call_log.append({"purpose": purpose, "system": system, "user": user})
        resp = self._responses.get(purpose)
        if resp is None:
            raise KeyError(
                f"FakeChatClient 没有为 purpose='{purpose}' 配置返回值。"
                f"已配置的 purpose: {list(self._responses.keys())}"
            )
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def complete_with_tools(self, schema, *, system, user, purpose, tools):
        # 测 agent 逻辑时不测工具调用循环（那是 client.py 的职责）
        return await self.complete(schema, system=system, user=user, purpose=purpose)


# ──────────────────────────────────────────────
# 通用 fixture 数据
# ──────────────────────────────────────────────

@pytest.fixture
def sample_resume_basics() -> dict:
    """简历基本信息，给 chief_editor 和 fan_out 用。"""
    return {
        "title": "Python 后端工程师简历",
        "applicant_name": "张三",
        "target_position": "Python 后端工程师",
        "tone": "专业严谨",
        "content_density": "medium",
        "section_count": 5,
    }


@pytest.fixture
def sample_outline_sections() -> list[dict]:
    """5 段大纲，给 chief_editor 和 fan_out 用。"""
    return [
        {"title": "个人信息与求职意向", "content": "张三，求职 Python 后端工程师"},
        {"title": "专业技能", "content": "Python / FastAPI / PostgreSQL / Redis"},
        {"title": "项目经历", "content": "XX 系统，负责后端架构设计"},
        {"title": "工作经历", "content": "XX公司 Python 后端工程师 2022-至今"},
        {"title": "教育经历", "content": "XX大学 计算机本科 2018-2022"},
    ]


@pytest.fixture
def sample_chief_plan() -> ChiefPlan:
    """主编分稿计划：5 段各段指引。"""
    return ChiefPlan(sections={
        "0": SectionPlan(focus="基本信息和求职意向", tone_hint="简洁明了", avoid=[]),
        "1": SectionPlan(focus="技能列表和技术深度", tone_hint="专业严谨", avoid=["不要列项目细节"]),
        "2": SectionPlan(focus="项目亮点和技术挑战", tone_hint="成就导向", avoid=["不要重复工作经历"]),
        "3": SectionPlan(focus="工作职责和成长轨迹", tone_hint="专业务实", avoid=["不要重复项目段的公司"]),
        "4": SectionPlan(focus="学历和专业背景", tone_hint="简洁客观", avoid=[]),
    })


@pytest.fixture
def sample_content_drafts() -> dict[str, ContentDraft]:
    """5 段内容草稿，key 是段号字符串。"""
    return {
        "0": ContentDraft(title="个人信息与求职意向", content={"name": "张三", "position": "Python 后端工程师"}),
        "1": ContentDraft(title="专业技能", content={"items": [
            {"skill": "Python", "years": 3, "level": "熟练"},
            {"skill": "FastAPI", "years": 2, "level": "掌握"},
        ]}),
        "2": ContentDraft(title="项目经历", content={"items": [
            {"name": "XX系统", "role": "后端开发", "tech": ["Python", "FastAPI"], "desc": "负责架构设计"},
        ]}),
        "3": ContentDraft(title="工作经历", content={"items": [
            {"company": "YY公司", "period": "2022-至今", "role": "Python 后端工程师", "desc": "负责业务线开发"},
        ]}),
        "4": ContentDraft(title="教育经历", content={"items": [
            {"school": "ZZ大学", "major": "计算机科学与技术", "period": "2018-2022", "degree": "本科"},
        ]}),
    }


@pytest.fixture
def make_payload(sample_resume_basics, sample_outline_sections):
    """构造单段 ContentGenerationInput 的工厂。"""
    def _make(section_index: int = 0) -> ContentGenerationInput:
        section = sample_outline_sections[section_index]
        return ContentGenerationInput(
            title=sample_resume_basics["title"],
            applicant_name=sample_resume_basics["applicant_name"],
            target_position=sample_resume_basics["target_position"],
            tone=sample_resume_basics["tone"],
            content_density=sample_resume_basics["content_density"],
            section_index=section_index,
            section_count=sample_resume_basics["section_count"],
            section_title=section["title"],
            section_objective=section["content"],
        )
    return _make


# ──────────────────────────────────────────────
# Mock agent fixtures（给 graph 测试用）
# ──────────────────────────────────────────────

@pytest.fixture
def mock_chief_editor(sample_chief_plan):
    """主编 agent mock：generate 返回分稿计划。"""
    agent = AsyncMock(spec=ChiefEditorAgent)
    agent.generate.return_value = sample_chief_plan
    return agent


@pytest.fixture
def mock_writer_pass(sample_content_drafts):
    """撰写 agent mock：每次返回固定草稿，一次过版。"""
    agent = AsyncMock(spec=DeepSeekWriterAgent)

    async def _generate(payload, plan=None):
        idx = str(payload.section_index)
        return sample_content_drafts.get(
            idx, ContentDraft(title="默认", content={"text": "默认内容"})
        )
    agent.generate.side_effect = _generate
    return agent


@pytest.fixture
def mock_proofreader_pass():
    """校对 agent mock：总是返回 passed=True。"""
    agent = AsyncMock(spec=DeepSeekProofReaderAgent)
    agent.review.return_value = ProofReport(passed=True, issues=[])
    return agent


@pytest.fixture
def mock_proofreader_always_fail():
    """校对 agent mock：总是返回 passed=False，测自纠环用完重试次数。"""
    agent = AsyncMock(spec=DeepSeekProofReaderAgent)
    agent.review.return_value = ProofReport(
        passed=False, issues=["内容不够具体，需要补充技术细节"]
    )
    return agent


@pytest.fixture
def mock_proofreader_fail_then_pass():
    """校对 agent mock：第 1 次不过，第 2 次过——测自纠环修正逻辑。"""
    agent = AsyncMock(spec=DeepSeekProofReaderAgent)
    agent.review.side_effect = [
        ProofReport(passed=False, issues=["首次检查：内容太空泛"]),
        ProofReport(passed=True, issues=[]),
    ]
    return agent
