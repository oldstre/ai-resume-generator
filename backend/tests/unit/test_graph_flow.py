"""Graph 状态流转测试。

用 mock agent 构建**真实** graph，ainvoke 后断言 state 字段。
这测的是 LangGraph 的节点编排 + 条件路由 + 自纠环 + reducer 合并，
不测 LLM 质量（LLM 被 mock 了）。

面试点：
- mock agent（AsyncMock）→ 构建 graph → ainvoke → 断言 state
- 自纠环测试：proofreader 第 1 次不过 → 验证 writer 被调 2 次
- 并发 Send 测试：5 段子图 → merge_sections reducer 合并 5 份结果
"""
import pytest

from app.llm.base import ContentGenerationInput
from app.llm.graph import (
    build_multi_section_graph,
    build_section_subgraph,
    make_cross_check_node,
)


# ──────────────────────────────────────────────
# 辅助：构造 SectionGenState
# ──────────────────────────────────────────────

def _make_sub_state(section_index: int = 0, max_retries: int = 2) -> dict:
    """构造单段子图的初始 state。"""
    payload = ContentGenerationInput(
        title="测试简历", applicant_name="张三",
        target_position="Python 后端", tone="专业",
        content_density="medium",
        section_index=section_index, section_count=5,
        section_title="专业技能",
        section_objective="列出 Python/FastAPI 等技能",
    )
    return {
        "section_index": section_index,
        "payload": payload,
        "plan": {},
        "draft": None,
        "issues": [],
        "issues_history": [],
        "attempts": 0,
        "max_retries": max_retries,
    }


# ──────────────────────────────────────────────
# 单段子图测试
# ──────────────────────────────────────────────

async def test_subgraph_pass_first_try(mock_writer_pass, mock_proofreader_pass):
    """writer 产出 → proofreader 过 → issues 为空 → END。"""
    subgraph = build_section_subgraph(mock_writer_pass, mock_proofreader_pass)
    result = await subgraph.ainvoke(_make_sub_state())

    assert result["issues"] == []  # 通过 = 空列表
    assert result["attempts"] == 1  # 只跑了 1 轮
    assert result["draft"] is not None  # 有草稿
    mock_writer_pass.generate.assert_awaited_once()
    mock_proofreader_pass.review.assert_awaited_once()


async def test_subgraph_revise_then_pass(
    mock_writer_pass, mock_proofreader_fail_then_pass
):
    """第 1 次 proofreader 不过 → 回 writer → 第 2 次过 → END。"""
    subgraph = build_section_subgraph(mock_writer_pass, mock_proofreader_fail_then_pass)
    result = await subgraph.ainvoke(_make_sub_state())

    assert result["issues"] == []  # 第 2 次过了
    assert result["attempts"] == 2  # 跑了 2 轮
    # writer 被调 2 次（第 1 次生成 + 第 2 次修改）
    assert mock_writer_pass.generate.await_count == 2
    # proofreader 被调 2 次
    assert mock_proofreader_fail_then_pass.review.await_count == 2
    # issues_history 记录了第 1 次的问题
    assert len(result["issues_history"]) == 1
    assert result["issues_history"][0]["attempts"] == 1


async def test_subgraph_exhaust_retries(
    mock_writer_pass, mock_proofreader_always_fail
):
    """一直不过 → attempts 达到 max_retries → END（issues 非空）。"""
    subgraph = build_section_subgraph(mock_writer_pass, mock_proofreader_always_fail)
    result = await subgraph.ainvoke(_make_sub_state(max_retries=2))

    # max_retries=2：writer 跑 2 次（attempts 1→2），第 2 次后 attempts=2 >= max_retries → END
    # should_continue: issues 非空 + attempts >= max_retries → "end"
    assert result["issues"] != []  # 仍有问题
    assert mock_writer_pass.generate.await_count == 2  # 跑 2 轮后停
    # issues_history 记录了每次失败
    assert len(result["issues_history"]) == 2


async def test_subgraph_issues_history_accumulates(
    mock_writer_pass, mock_proofreader_fail_then_pass
):
    """验证 issues_history 累积每轮 issues（含 attempts 标记）。"""
    subgraph = build_section_subgraph(mock_writer_pass, mock_proofreader_fail_then_pass)
    result = await subgraph.ainvoke(_make_sub_state())

    history = result["issues_history"]
    assert len(history) == 1  # 只第 1 轮失败
    assert history[0]["attempts"] == 1
    assert len(history[0]["issues"]) == 1
    assert "首次检查" in history[0]["issues"][0]


# ──────────────────────────────────────────────
# 主图测试
# ──────────────────────────────────────────────

def _make_main_state(sample_outline_sections, sample_resume_basics) -> dict:
    """构造主图初始 state。"""
    return {
        "chief_plan": {},
        "sections": {},
        "cross_issues": [],
        "section_count": 5,
        "round": 0,
        "max_rounds": 1,
        "resume_id": "00000000-0000-0000-0000-000000000001",
        "outline_sections": sample_outline_sections,
        "resume_basics": sample_resume_basics,
    }


async def test_main_graph_chief_then_fanout(
    mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
    sample_outline_sections, sample_resume_basics,
):
    """chief_editor 调 1 次 → 5 个 writer 各调 1 次 → sections 有 5 个 key。"""
    graph = build_multi_section_graph(
        mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
        max_retries=2,
    )
    result = await graph.ainvoke(_make_main_state(sample_outline_sections, sample_resume_basics))

    # chief_editor 调了 1 次
    mock_chief_editor.generate.assert_awaited_once()
    # 5 段各调了 1 次 writer（一次过版）
    assert mock_writer_pass.generate.await_count == 5
    # sections 有 5 个 key
    assert len(result["sections"]) == 5
    for i in range(5):
        key = str(i)
        assert key in result["sections"]
        assert result["sections"][key]["draft"] is not None
        assert result["sections"][key]["issues"] == []


async def test_main_graph_cross_check_no_conflict(
    mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
    sample_outline_sections, sample_resume_basics,
):
    """5 段无重复关键字 → cross_issues 为空。"""
    graph = build_multi_section_graph(
        mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
        max_retries=2,
    )
    result = await graph.ainvoke(_make_main_state(sample_outline_sections, sample_resume_basics))

    assert result["cross_issues"] == []


async def test_main_graph_cross_check_finds_duplicate(
    mock_chief_editor, mock_proofreader_pass,
    sample_outline_sections, sample_resume_basics,
):
    """2 段有相同 company → cross_issues 非空。"""
    from unittest.mock import AsyncMock
    from app.llm.deepseek import DeepSeekWriterAgent
    from app.llm.base import ContentDraft

    # 构造 writer mock：段 2 和段 3 都有相同的 company "XX公司"
    writer = AsyncMock(spec=DeepSeekWriterAgent)
    drafts = {
        0: ContentDraft(title="个人信息", content={"name": "张三"}),
        1: ContentDraft(title="技能", content={"items": [{"skill": "Python"}]}),
        2: ContentDraft(title="项目", content={"items": [
            {"name": "系统A", "company": "XX公司", "desc": "负责后端"},
        ]}),
        3: ContentDraft(title="工作", content={"items": [
            {"company": "XX公司", "period": "2022-至今", "role": "工程师"},
        ]}),
        4: ContentDraft(title="教育", content={"items": [
            {"school": "YY大学", "major": "计算机"},
        ]}),
    }

    async def _generate(payload, plan=None):
        return drafts[payload.section_index]
    writer.generate.side_effect = _generate

    graph = build_multi_section_graph(
        mock_chief_editor, writer, mock_proofreader_pass,
        max_retries=2,
    )
    result = await graph.ainvoke(_make_main_state(sample_outline_sections, sample_resume_basics))

    assert len(result["cross_issues"]) > 0
    assert any("XX公司" in issue for issue in result["cross_issues"])


async def test_main_graph_on_event_callback(
    mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
    sample_outline_sections, sample_resume_basics,
):
    """验证 SSE 事件回调被触发（status / check / done）。"""
    events = []

    async def on_event(event):
        events.append(event)

    graph = build_multi_section_graph(
        mock_chief_editor, mock_writer_pass, mock_proofreader_pass,
        max_retries=2, on_event=on_event,
    )
    await graph.ainvoke(_make_main_state(sample_outline_sections, sample_resume_basics))

    # 应该有 status 事件（主编分析、段撰写）和 check 事件（校对结果）
    event_types = [type(e).__name__ for e in events]
    assert "StatusEvent" in event_types
    assert "CheckEvent" in event_types
    # 至少有主编 1 个 status + 5 段各 1 个 status + 5 段各 1 个 check = 11+
    assert len(events) >= 11
