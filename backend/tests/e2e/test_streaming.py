"""流式生成端点 E2E 测试。

monkeypatch content.py 模块里的 agent 构造 → 隔离 LLM，
但 graph / SSE / DB 管线跑真的——测完整的事件序列和落库。

面试点：
- E2E 测管线不测 LLM：monkeypatch agent 类 → 返回 mock 实例
- SSE 解析：逐行读 event:/data: 重建事件列表
- 落库验证：流结束后查 DB 确认 ResumeContent 被写入
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.llm.base import ChiefPlan, ContentDraft, ProofReport, SectionPlan
from app.models import Resume, ResumeContent, ResumeOutline


# ──────────────────────────────────────────────
# 辅助：SSE 解析 + 测试数据
# ──────────────────────────────────────────────

def parse_sse_events(raw_text: str) -> list[dict]:
    """从原始 SSE 文本解析出事件列表。

    SSE 格式：event: xxx\ndata: {...}\n\n
    """
    events = []
    for block in raw_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line.replace("event:", "").strip()
            elif line.startswith("data:"):
                data_str = line.replace("data:", "", 1).strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = data_str
        if event_type:
            events.append({"event": event_type, "data": data})
    return events


def _make_mock_agents():
    """构造 mock agent 三件套，给 content.py 的 monkeypatch 用。"""
    mock_chief = AsyncMock()
    mock_chief.generate.return_value = ChiefPlan(sections={
        str(i): SectionPlan(focus=f"段{i}重点", tone_hint="专业", avoid=[])
        for i in range(5)
    })

    mock_writer = AsyncMock()
    drafts = {
        0: ContentDraft(title="个人信息与求职意向", content={"name": "张三"}),
        1: ContentDraft(title="专业技能", content={"items": [{"skill": "Python"}]}),
        2: ContentDraft(title="项目经历", content={"items": [{"name": "XX系统"}]}),
        3: ContentDraft(title="工作经历", content={"items": [{"company": "YY公司"}]}),
        4: ContentDraft(title="教育经历", content={"items": [{"school": "ZZ大学"}]}),
    }

    async def _writer_generate(payload, plan=None):
        return drafts[payload.section_index]
    mock_writer.generate.side_effect = _writer_generate

    mock_proofreader = AsyncMock()
    mock_proofreader.review.return_value = ProofReport(passed=True, issues=[])

    return mock_chief, mock_writer, mock_proofreader


async def _create_resume_with_outline(db_session, user_id) -> Resume:
    """在测试 DB 创建简历 + 已确认大纲。"""
    resume = Resume(
        user_id=user_id,
        title="测试简历",
        applicant_name="张三",
        target_position="Python 后端工程师",
        tone="专业严谨",
        section_count=5,
        content_density="medium",
        status="outline_ready",
    )
    db_session.add(resume)
    await db_session.flush()

    outline = ResumeOutline(
        resume_id=resume.id,
        status="confirmed",
        sections=[
            {"title": "个人信息与求职意向", "content": "张三求职Python后端"},
            {"title": "专业技能", "content": "Python/FastAPI/PostgreSQL"},
            {"title": "项目经历", "content": "XX系统后端架构"},
            {"title": "工作经历", "content": "YY公司Python工程师"},
            {"title": "教育经历", "content": "ZZ大学计算机本科"},
        ],
    )
    db_session.add(outline)
    await db_session.commit()
    await db_session.refresh(resume)
    return resume


# ──────────────────────────────────────────────
# 测试场景
# ──────────────────────────────────────────────

async def test_stream_success(app_client, db_session, test_user, monkeypatch):
    """简历 + 大纲存在 → SSE 流含 status/check/done → DB 落库。"""
    resume = await _create_resume_with_outline(db_session, test_user.id)

    # monkeypatch content.py 里的 agent 构造
    mock_chief, mock_writer, mock_proofreader = _make_mock_agents()
    monkeypatch.setattr("app.workflows.content.create_chat_model", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("app.workflows.content.ChiefEditorAgent", lambda **kw: mock_chief)
    monkeypatch.setattr("app.workflows.content.DeepSeekWriterAgent", lambda **kw: mock_writer)
    monkeypatch.setattr("app.workflows.content.DeepSeekProofReaderAgent", lambda **kw: mock_proofreader)

    # 调流式端点
    resp = await app_client.post(
        f"/resumes/{resume.id}/contents/regenerate/stream",
        headers={"Accept": "text/event-stream"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = parse_sse_events(resp.text)
    event_types = [e["event"] for e in events]

    # 应该有 status 事件（主编分析、段撰写）
    assert "status" in event_types
    # 应该有 check 事件（校对结果）
    assert "check" in event_types
    # 应该有 done 事件（5 段各一个）
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 5

    # 验证落库：5 段 ResumeContent 都写进了 DB
    result = await db_session.execute(
        select(ResumeContent)
        .where(ResumeContent.resume_id == resume.id)
        .order_by(ResumeContent.section_index.asc())
    )
    contents = result.scalars().all()
    assert len(contents) == 5
    for c in contents:
        assert c.status == "ready"
        assert c.content is not None


async def test_stream_resume_not_found(app_client, test_user):
    """不存在的 resume_id → 404（端点在 StreamingResponse 之前做归属校验）。

    端点 _get_resume_or_404 在返回 StreamingResponse 之前执行，
    简历不存在或不属于该用户直接返回 404，不会进入 SSE 流。
    这是正确行为：防止无权限用户消耗 LLM 资源。
    """
    fake_id = uuid.uuid4()
    resp = await app_client.post(
        f"/resumes/{fake_id}/contents/regenerate/stream",
    )
    assert resp.status_code == 404


async def test_stream_no_outline(app_client, db_session, test_user):
    """简历存在但无大纲 → SSE error 事件。"""
    resume = Resume(
        user_id=test_user.id,
        title="无大纲简历",
        applicant_name="李四",
        target_position="前端工程师",
        tone="简洁",
        section_count=3,
        content_density="medium",
        status="draft",
    )
    db_session.add(resume)
    await db_session.commit()
    await db_session.refresh(resume)

    resp = await app_client.post(
        f"/resumes/{resume.id}/contents/regenerate/stream",
    )
    assert resp.status_code == 200

    events = parse_sse_events(resp.text)
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) >= 1
    assert "大纲" in error_events[0]["data"]["message"]


async def test_stream_outline_not_confirmed(app_client, db_session, test_user):
    """大纲状态非 confirmed → SSE error 事件。"""
    resume = Resume(
        user_id=test_user.id,
        title="未确认大纲简历",
        applicant_name="王五",
        target_position="测试工程师",
        tone="专业",
        section_count=3,
        content_density="medium",
        status="draft",
    )
    db_session.add(resume)
    await db_session.flush()

    outline = ResumeOutline(
        resume_id=resume.id,
        status="generating",  # 未确认
        sections=[{"title": "技能", "content": "测试技能"}],
    )
    db_session.add(outline)
    await db_session.commit()
    await db_session.refresh(resume)

    resp = await app_client.post(
        f"/resumes/{resume.id}/contents/regenerate/stream",
    )
    assert resp.status_code == 200

    events = parse_sse_events(resp.text)
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) >= 1
    assert "确认" in error_events[0]["data"]["message"]
