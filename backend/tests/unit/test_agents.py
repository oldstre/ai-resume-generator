"""Agent 类单元测试。

用 FakeChatClient 构造真实 agent 实例（不 mock agent 类本身），
测 agent 内部逻辑：prompt 拼接、schema 校验、异常转译。

面试点：mock 业务封装层（StructuredChatClient）而非 SDK 层（ChatOpenAI），
因为 agent 依赖的是封装层接口。FakeChatClient 还记录 call_log，
能断言 system prompt 内容（验证 plan 是否被正确拼进 prompt）。
"""
import pytest

from app.llm.base import (
    ChiefPlan,
    ContentDraft,
    OutlineDraft,
    ProofReport,
    SectionPlan,
)
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekOutlineGenerator,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)
from app.llm.errors import (
    InvalidContentOutputError,
    InvalidModelOutputError,
    InvalidOutlineOutputError,
)
from app.llm.client import StructuredChatClient
from app.schemas.resume_outline import ResumeSectionDraft

from .conftest import FakeChatClient


# ──────────────────────────────────────────────
# 大纲生成器测试
# ──────────────────────────────────────────────

async def test_outline_generator_valid():
    """FakeChatClient 返回合法 OutlineDraft → agent 正确返回。"""
    from app.llm.base import OutlineGenerationInput

    draft = OutlineDraft(sections=[
        ResumeSectionDraft(title="教育经历", content="XX大学 计算机本科"),
        ResumeSectionDraft(title="工作经历", content="XX公司 Python 后端"),
        ResumeSectionDraft(title="专业技能", content="Python/FastAPI/PostgreSQL"),
    ])
    chat = FakeChatClient({"生成大纲": draft})
    agent = DeepSeekOutlineGenerator(chat=chat, api_key="fake-key")

    payload = OutlineGenerationInput(
        title="测试简历", applicant_name="张三",
        target_position="Python 后端", tone="专业",
        section_count=3,
    )
    result = await agent.generate(payload)

    assert len(result.sections) == 3
    assert result.sections[0].title == "教育经历"
    # 验证 call_log 记录了调用
    assert len(chat.call_log) == 1
    assert chat.call_log[0]["purpose"] == "生成大纲"


async def test_outline_generator_wrong_section_count(make_payload):
    """FakeChatClient 返回段数不符 → 抛 InvalidOutlineOutputError。"""
    # 期望 5 段，但 FakeChatClient 返回 2 段
    draft = OutlineDraft(sections=[
        ResumeSectionDraft(title="教育", content="XX大学"),
        ResumeSectionDraft(title="工作", content="XX公司"),
    ])
    chat = FakeChatClient({"生成大纲": draft})
    agent = DeepSeekOutlineGenerator(chat=chat, api_key="fake-key")

    from app.llm.base import OutlineGenerationInput
    payload = OutlineGenerationInput(
        title="测试简历", applicant_name="张三",
        target_position="Python 后端", tone="专业",
        section_count=5,
    )
    with pytest.raises(InvalidOutlineOutputError, match="段数不符"):
        await agent.generate(payload)


async def test_outline_generator_empty_title():
    """FakeChatClient 返回空白 title → 抛 InvalidOutlineOutputError。

    用空格而非空串：Pydantic min_length=1 允许空格通过，
    但 agent 的 _validate_draft 用 strip() 检查会抓到。
    """
    draft = OutlineDraft(sections=[
        ResumeSectionDraft(title="   ", content="XX大学"),
        ResumeSectionDraft(title="工作", content="XX公司"),
        ResumeSectionDraft(title="技能", content="Python"),
    ])
    chat = FakeChatClient({"生成大纲": draft})
    agent = DeepSeekOutlineGenerator(chat=chat, api_key="fake-key")

    from app.llm.base import OutlineGenerationInput
    payload = OutlineGenerationInput(
        title="测试简历", applicant_name="张三",
        target_position="Python 后端", tone="专业",
        section_count=3,
    )
    with pytest.raises(InvalidOutlineOutputError, match="title 为空"):
        await agent.generate(payload)


# ──────────────────────────────────────────────
# 撰写 Agent 测试
# ──────────────────────────────────────────────

async def test_writer_agent_with_plan(make_payload, sample_chief_plan):
    """传 plan → system prompt 含主编指引内容（检查 call_log）。"""
    draft = ContentDraft(title="专业技能", content={"items": [{"skill": "Python"}]})
    chat = FakeChatClient({"撰写 agent 生成内容": draft})
    agent = DeepSeekWriterAgent(chat=chat, api_key="fake-key")

    payload = make_payload(section_index=1)  # 技能段
    plan = sample_chief_plan.sections["1"]

    result = await agent.generate(payload, plan.model_dump())

    assert result.title == "专业技能"
    # 验证 plan 内容被拼进了 system prompt
    assert len(chat.call_log) == 1
    system_prompt = chat.call_log[0]["system"]
    assert "主编指引" in system_prompt
    assert plan.focus in system_prompt
    assert plan.tone_hint in system_prompt


async def test_writer_agent_without_plan(make_payload):
    """plan=None → system prompt 不含主编指引段。"""
    draft = ContentDraft(title="专业技能", content={"items": [{"skill": "Python"}]})
    chat = FakeChatClient({"撰写 agent 生成内容": draft})
    agent = DeepSeekWriterAgent(chat=chat, api_key="fake-key")

    result = await agent.generate(make_payload(section_index=1), plan=None)

    assert result.title == "专业技能"
    system_prompt = chat.call_log[0]["system"]
    assert "主编指引" not in system_prompt


async def test_writer_agent_llm_error_translates(make_payload):
    """FakeChatClient 抛 InvalidModelOutputError → agent 转译为 InvalidContentOutputError。"""
    chat = FakeChatClient({
        "撰写 agent 生成内容": InvalidModelOutputError("模拟 LLM 返回格式错")
    })
    agent = DeepSeekWriterAgent(chat=chat, api_key="fake-key")

    with pytest.raises(InvalidContentOutputError):
        await agent.generate(make_payload(section_index=0), plan=None)


async def test_writer_agent_empty_content_validation(make_payload):
    """LLM 返回空 content → agent._validate_draft 抛 InvalidContentOutputError。"""
    draft = ContentDraft(title="技能", content={})
    chat = FakeChatClient({"撰写 agent 生成内容": draft})
    agent = DeepSeekWriterAgent(chat=chat, api_key="fake-key")

    with pytest.raises(InvalidContentOutputError, match="内容为空"):
        await agent.generate(make_payload(section_index=0), plan=None)


# ──────────────────────────────────────────────
# 校对 Agent 测试
# ──────────────────────────────────────────────

async def test_proofreader_pass(make_payload):
    """FakeChatClient 返回 passed=True → review() 返回正确 ProofReport。"""
    report = ProofReport(passed=True, issues=[])
    chat = FakeChatClient({"校对 agent 检查内容": report})
    agent = DeepSeekProofReaderAgent(chat=chat, api_key="fake-key")

    draft = ContentDraft(title="技能", content={"items": [{"skill": "Python"}]})
    result = await agent.review(draft, make_payload(section_index=0))

    assert result.passed is True
    assert result.issues == []


async def test_proofreader_finds_issues(make_payload):
    """FakeChatClient 返回 issues → review() 正确传递问题列表。"""
    report = ProofReport(
        passed=False,
        issues=["技能项太少，建议补充 3-5 项", "缺少熟练度标注"],
    )
    chat = FakeChatClient({"校对 agent 检查内容": report})
    agent = DeepSeekProofReaderAgent(chat=chat, api_key="fake-key")

    draft = ContentDraft(title="技能", content={"items": [{"skill": "Python"}]})
    result = await agent.review(draft, make_payload(section_index=0))

    assert result.passed is False
    assert len(result.issues) == 2


async def test_proofreader_llm_error_translates(make_payload):
    """FakeChatClient 抛 InvalidModelOutputError → review() 转译为 InvalidContentOutputError。"""
    chat = FakeChatClient({
        "校对 agent 检查内容": InvalidModelOutputError("模拟格式错")
    })
    agent = DeepSeekProofReaderAgent(chat=chat, api_key="fake-key")

    draft = ContentDraft(title="技能", content={"items": [{"skill": "Python"}]})
    with pytest.raises(InvalidContentOutputError):
        await agent.review(draft, make_payload(section_index=0))


# ──────────────────────────────────────────────
# 主编 Agent 测试
# ──────────────────────────────────────────────

async def test_chief_editor_generates_plan(sample_outline_sections, sample_resume_basics):
    """FakeChatClient 返回 ChiefPlan → generate() 返回正确结构。"""
    plan = ChiefPlan(sections={
        "0": SectionPlan(focus="基本信息", tone_hint="简洁", avoid=[]),
        "1": SectionPlan(focus="技能列表", tone_hint="专业", avoid=["不要重复"]),
    })
    chat = FakeChatClient({"主编制定分稿计划": plan})
    agent = ChiefEditorAgent(chat=chat, api_key="fake-key")

    result = await agent.generate(sample_outline_sections, sample_resume_basics)

    assert "0" in result.sections
    assert "1" in result.sections
    assert result.sections["0"].focus == "基本信息"
    # 验证 system prompt 含主编人设
    assert "资深简历主编" in chat.call_log[0]["system"]


async def test_chief_editor_llm_error_translates(sample_outline_sections, sample_resume_basics):
    """FakeChatClient 抛 InvalidModelOutputError → generate() 转译为 InvalidContentOutputError。"""
    chat = FakeChatClient({
        "主编制定分稿计划": InvalidModelOutputError("模拟格式错")
    })
    agent = ChiefEditorAgent(chat=chat, api_key="fake-key")

    with pytest.raises(InvalidContentOutputError):
        await agent.generate(sample_outline_sections, sample_resume_basics)


# ──────────────────────────────────────────────
# API Key 校验测试
# ──────────────────────────────────────────────

async def test_agent_no_api_key_raises(make_payload):
    """空 API Key → agent 调用时抛 LLMNotConfiguredError。"""
    from app.llm.errors import LLMNotConfiguredError

    # FakeChatClient 不会被调到——agent 在调 chat 之前就校验了 API Key
    # 但我们的 FakeChatClient 没做 API Key 校验（那是 StructuredChatClient 的职责）
    # 所以这里用真实的 StructuredChatClient + mock model
    from unittest.mock import AsyncMock
    mock_model = AsyncMock()
    chat = StructuredChatClient(model=mock_model, api_key="")
    agent = DeepSeekOutlineGenerator(chat=chat, api_key="")

    from app.llm.base import OutlineGenerationInput
    payload = OutlineGenerationInput(
        title="测试", applicant_name="张三",
        target_position="Python", tone="专业",
        section_count=3,
    )
    with pytest.raises(LLMNotConfiguredError):
        await agent.generate(payload)
