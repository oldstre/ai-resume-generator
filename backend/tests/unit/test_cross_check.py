"""cross_check_node 单元测试。

cross_check 是纯代码规则（不调 LLM），检查段间是否有重复的
company / school / skill 字段值。

面试点：纯逻辑节点不需要 mock——直接构造 state 调节点函数，
比 graph 级测试快且定位精准。
"""
import pytest

from app.llm.base import ContentDraft
from app.llm.graph import make_cross_check_node


def _make_section_state(draft: ContentDraft | None, attempts: int = 1) -> dict:
    """构造一个段落的子状态。"""
    return {
        "draft": draft,
        "attempts": attempts,
        "issues": [],
        "issues_history": [],
    }


async def test_no_duplicates():
    """5 段各有不同 company/school → cross_issues 为空。"""
    sections = {
        "0": _make_section_state(ContentDraft(title="个人信息", content={"name": "张三"})),
        "1": _make_section_state(ContentDraft(title="技能", content={"items": [{"skill": "Python"}]})),
        "2": _make_section_state(ContentDraft(title="项目", content={"items": [{"name": "系统A", "company": "公司A"}]})),
        "3": _make_section_state(ContentDraft(title="工作", content={"items": [{"company": "公司B"}]})),
        "4": _make_section_state(ContentDraft(title="教育", content={"items": [{"school": "大学A"}]})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    assert result["cross_issues"] == []


async def test_duplicate_company():
    """段 2 和段 3 都有 "XX公司" → cross_issues 非空。"""
    sections = {
        "2": _make_section_state(ContentDraft(title="项目", content={"items": [{"company": "XX公司"}]})),
        "3": _make_section_state(ContentDraft(title="工作", content={"items": [{"company": "XX公司"}]})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    assert len(result["cross_issues"]) == 1
    assert "XX公司" in result["cross_issues"][0]
    assert "段2" in result["cross_issues"][0]
    assert "段3" in result["cross_issues"][0]


async def test_duplicate_school():
    """两段都有相同 school → 被检出。"""
    sections = {
        "0": _make_section_state(ContentDraft(title="教育1", content={"school": "XX大学"})),
        "1": _make_section_state(ContentDraft(title="教育2", content={"items": [{"school": "XX大学"}]})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    assert len(result["cross_issues"]) >= 1
    assert any("XX大学" in issue for issue in result["cross_issues"])


async def test_multiple_duplicates():
    """多对重复 → 每个 pair 都报。"""
    sections = {
        "0": _make_section_state(ContentDraft(title="A", content={"company": "公司X", "school": "大学Y"})),
        "1": _make_section_state(ContentDraft(title="B", content={"company": "公司X"})),
        "2": _make_section_state(ContentDraft(title="C", content={"school": "大学Y"})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    # 段0与段1重复公司X，段0与段2重复大学Y → 至少 2 条
    assert len(result["cross_issues"]) >= 2


async def test_empty_draft_skipped():
    """draft=None 的段被跳过，不报 false positive。"""
    sections = {
        "0": _make_section_state(None),  # 无草稿
        "1": _make_section_state(ContentDraft(title="B", content={"company": "XX公司"})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    assert result["cross_issues"] == []


async def test_short_keyword_ignored():
    """长度 <=2 的关键字段值被忽略（避免误报 'IT' 之类的短词）。"""
    sections = {
        "0": _make_section_state(ContentDraft(title="A", content={"company": "IT"})),
        "1": _make_section_state(ContentDraft(title="B", content={"company": "IT"})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node()
    result = await node(state)
    # "IT" 长度 2，被 len > 2 过滤掉
    assert result["cross_issues"] == []


async def test_on_event_called():
    """有 on_event 回调时，cross_check 推 CheckEvent。"""
    events = []

    async def on_event(event):
        events.append(event)

    sections = {
        "0": _make_section_state(ContentDraft(title="A", content={"company": "XX公司"})),
        "1": _make_section_state(ContentDraft(title="B", content={"company": "XX公司"})),
    }
    state = {"sections": sections, "cross_issues": []}
    node = make_cross_check_node(on_event=on_event)
    await node(state)

    assert len(events) == 1
    # CheckEvent 有 event="check" 和 data.passed
    assert events[0].event == "check"
    assert events[0].data.passed is False
