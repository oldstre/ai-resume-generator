"""LLM 输出回归测试（Fixture 录制模式）。

面试点：
这不是"测 LLM 是否变聪明了"（那不可能），而是"测 agent 代码能否正确解析 LLM 的历史输出格式"。
模式：
  1. record_fixtures.py（手动跑，需真实 API Key）调真实 LLM 生成输出，存 JSON
  2. 本测试加载 fixture JSON → 构造 Pydantic 模型 → 喂给 FakeChatClient → 验证 agent 能正确解析
  3. 如果 prompt 改动导致输出结构变化 → fixture 过期 → 重新录制

防的是：prompt 改了一个字段名，LLM 返回的 JSON 结构变了，但 agent 解析逻辑没跟上 →
线上静默 break，用户拿到空内容或报错。有了 fixture 回归，CI 就能提前发现。
"""
import json
from pathlib import Path

import pytest

from app.llm.base import (
    ChiefPlan,
    ContentDraft,
    ContentGenerationInput,
    ProofReport,
)
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)
from tests.unit.conftest import FakeChatClient

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "llm_outputs"


def _load_fixture(name: str) -> dict:
    """加载 fixture JSON 文件为 dict。"""
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# ChiefPlan 回归测试
# ──────────────────────────────────────────────

async def test_chief_plan_fixture_parses():
    """加载 chief_plan.json fixture → 构造 ChiefPlan → 验证 5 段指引结构完整。

    如果有人改了 SectionPlan 的字段名（focus → focus_point），
    model_validate 会失败 → 这个测试先于线上 break 报错。
    """
    raw = _load_fixture("chief_plan.json")
    plan = ChiefPlan.model_validate(raw)

    assert len(plan.sections) == 5
    for i in range(5):
        section = plan.sections[str(i)]
        assert section.focus  # 非空
        assert section.tone_hint  # 非空
        assert isinstance(section.avoid, list)


async def test_chief_plan_fixture_through_agent(sample_outline_sections, sample_resume_basics):
    """用 fixture 构造 FakeChatClient → chief_editor agent 能正确解析返回。

    这测的是 agent.generate() 的完整路径：prompt 拼接 → chat.complete → 解析返回。
    """
    raw = _load_fixture("chief_plan.json")
    plan_obj = ChiefPlan.model_validate(raw)

    chat = FakeChatClient({"主编制定分稿计划": plan_obj})
    agent = ChiefEditorAgent(chat=chat, api_key="fake-key")

    plan = await agent.generate(sample_outline_sections, sample_resume_basics)

    assert isinstance(plan, ChiefPlan)
    assert len(plan.sections) == 5
    # 验证 plan 内容和 fixture 一致（不是 mock 数据）
    assert "Python" in plan.sections["1"].focus or "技术" in plan.sections["1"].focus


# ──────────────────────────────────────────────
# ContentDraft 回归测试
# ──────────────────────────────────────────────

async def test_writer_draft_skill_fixture_parses():
    """加载 writer_draft_skill.json → 构造 ContentDraft → 验证技能段结构。"""
    raw = _load_fixture("writer_draft_skill.json")
    draft = ContentDraft.model_validate(raw)

    assert draft.title == "专业技能"
    assert "items" in draft.content
    assert len(draft.content["items"]) >= 3
    for item in draft.content["items"]:
        assert "skill" in item
        assert "years" in item


async def test_writer_draft_project_fixture_parses():
    """加载 writer_draft_project.json → 构造 ContentDraft → 验证项目段结构。"""
    raw = _load_fixture("writer_draft_project.json")
    draft = ContentDraft.model_validate(raw)

    assert draft.title == "项目经历"
    assert "items" in draft.content
    for item in draft.content["items"]:
        assert "name" in item
        assert "tech" in item
        assert "result" in item


async def test_writer_draft_skill_fixture_through_agent(make_payload):
    """用 fixture 构造 FakeChatClient → writer agent 能正确解析技能段。"""
    raw = _load_fixture("writer_draft_skill.json")
    draft_obj = ContentDraft.model_validate(raw)

    chat = FakeChatClient({"撰写 agent 生成内容": draft_obj})
    agent = DeepSeekWriterAgent(chat=chat, api_key="fake-key")

    payload = make_payload(section_index=1)  # 技能段
    draft = await agent.generate(payload)

    assert isinstance(draft, ContentDraft)
    assert draft.title == "专业技能"
    assert len(draft.content["items"]) >= 3


# ──────────────────────────────────────────────
# ProofReport 回归测试
# ──────────────────────────────────────────────

async def test_proof_report_pass_fixture_parses():
    """加载 proof_report_pass.json → 构造 ProofReport → passed=True, issues=[]。"""
    raw = _load_fixture("proof_report_pass.json")
    report = ProofReport.model_validate(raw)

    assert report.passed is True
    assert report.issues == []


async def test_proof_report_fail_fixture_parses():
    """加载 proof_report_fail.json → 构造 ProofReport → passed=False, issues 非空。"""
    raw = _load_fixture("proof_report_fail.json")
    report = ProofReport.model_validate(raw)

    assert report.passed is False
    assert len(report.issues) >= 1
    for issue in report.issues:
        assert isinstance(issue, str)
        assert len(issue) > 5  # 每条问题有实质内容


async def test_proof_report_fail_fixture_through_agent(make_payload, sample_content_drafts):
    """用 fixture 构造 FakeChatClient → proofreader agent 能正确解析失败报告。"""
    raw = _load_fixture("proof_report_fail.json")
    report_obj = ProofReport.model_validate(raw)

    chat = FakeChatClient({"校对 agent 检查内容": report_obj})
    agent = DeepSeekProofReaderAgent(chat=chat, api_key="fake-key")

    payload = make_payload(section_index=1)
    draft = sample_content_drafts["1"]
    report = await agent.review(draft, payload)

    assert isinstance(report, ProofReport)
    assert report.passed is False
    assert len(report.issues) >= 1


# ──────────────────────────────────────────────
# Fixture 完整性元测试
# ──────────────────────────────────────────────

def test_all_fixtures_exist():
    """确保所有预期的 fixture 文件都存在——防止有人删了 fixture 但测试还引用。"""
    expected = [
        "chief_plan.json",
        "writer_draft_skill.json",
        "writer_draft_project.json",
        "proof_report_pass.json",
        "proof_report_fail.json",
    ]
    for name in expected:
        path = FIXTURES_DIR / name
        assert path.exists(), f"Fixture 文件缺失: {name}"


def test_all_fixtures_are_valid_json():
    """确保所有 fixture 文件是合法 JSON——防止手编 JSON 时语法错误。"""
    for path in FIXTURES_DIR.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            json.load(f)  # 不抛异常就算通过
