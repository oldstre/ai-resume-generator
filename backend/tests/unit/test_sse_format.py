"""SSE 事件格式化测试。

format_sse 把 Pydantic 事件对象序列化成 SSE 协议字符串。
前端 EventSource 靠这个格式解析事件。

面试点：SSE 协议要求每条消息以 \\n\\n 结尾，
event: 和 data: 各占一行。测格式正确性就是测前后端契约。
"""
import json

from app.schemas.sse_event import (
    CheckData,
    CheckEvent,
    DoneData,
    DoneEvent,
    ErrorData,
    ErrorEvent,
    StatusData,
    StatusEvent,
    ToolData,
    ToolDoneData,
    ToolDoneEvent,
    ToolEvent,
    format_sse,
)


def test_format_status_event():
    """StatusEvent → 输出含 "event: status" + JSON data。"""
    sse = format_sse(StatusEvent(
        event="status",
        data=StatusData(stage="generating", message="开始生成第 1 段"),
    ))
    assert "event: status" in sse
    assert sse.endswith("\n\n")
    # data 行是合法 JSON
    data_line = [line for line in sse.strip().split("\n") if line.startswith("data:")][0]
    data_json = json.loads(data_line.replace("data:", "", 1).strip())
    assert data_json["stage"] == "generating"
    assert data_json["message"] == "开始生成第 1 段"


def test_format_done_event():
    """DoneEvent → 含 content / title / attempts。"""
    sse = format_sse(DoneEvent(
        event="done",
        data=DoneData(
            content={"items": [{"skill": "Python"}]},
            title="专业技能",
            attempts=2,
            issues_history=[{"attempts": 1, "issues": ["太空泛"]}],
            section_index=1,
        ),
    ))
    assert "event: done" in sse
    data_line = [line for line in sse.strip().split("\n") if line.startswith("data:")][0]
    data_json = json.loads(data_line.replace("data:", "", 1).strip())
    assert data_json["title"] == "专业技能"
    assert data_json["attempts"] == 2
    assert data_json["section_index"] == 1


def test_format_error_event():
    """ErrorEvent → 含 message。"""
    sse = format_sse(ErrorEvent(
        event="error",
        data=ErrorData(message="简历不存在"),
    ))
    assert "event: error" in sse
    data_line = [line for line in sse.strip().split("\n") if line.startswith("data:")][0]
    data_json = json.loads(data_line.replace("data:", "", 1).strip())
    assert data_json["message"] == "简历不存在"


def test_format_check_event():
    """CheckEvent → 含 passed / issues。"""
    sse = format_sse(CheckEvent(
        event="check",
        data=CheckData(passed=False, issues=["内容太空泛"], section_index=1),
    ))
    assert "event: check" in sse
    data_line = [line for line in sse.strip().split("\n") if line.startswith("data:")][0]
    data_json = json.loads(data_line.replace("data:", "", 1).strip())
    assert data_json["passed"] is False
    assert data_json["issues"] == ["内容太空泛"]


def test_format_tool_event():
    """ToolEvent → 含 name / args。"""
    sse = format_sse(ToolEvent(
        event="tool",
        data=ToolData(name="search_web", args={"query": "Python 技能要求"}),
    ))
    assert "event: tool" in sse
    data_line = [line for line in sse.strip().split("\n") if line.startswith("data:")][0]
    data_json = json.loads(data_line.replace("data:", "", 1).strip())
    assert data_json["name"] == "search_web"


def test_format_tool_done_event():
    """ToolDoneEvent → 含 name。"""
    sse = format_sse(ToolDoneEvent(
        event="tool_done",
        data=ToolDoneData(name="search_web"),
    ))
    assert "event: tool_done" in sse


def test_sse_ends_with_double_newline():
    """所有 SSE 消息必须以 \\n\\n 结尾（SSE 协议要求）。"""
    events = [
        StatusEvent(event="status", data=StatusData(stage="generating", message="test")),
        CheckEvent(event="check", data=CheckData(passed=True, issues=[])),
        DoneEvent(event="done", data=DoneData(content={}, title="t", attempts=1, issues_history=[])),
        ErrorEvent(event="error", data=ErrorData(message="err")),
    ]
    for event in events:
        assert format_sse(event).endswith("\n\n"), f"{type(event).__name__} 不以 \\n\\n 结尾"


def test_all_event_types_serializable():
    """6 种事件都能 model_dump + json.dumps（Pydantic 序列化健康检查）。"""
    events = [
        StatusEvent(event="status", data=StatusData(stage="done", message="完成")),
        ToolEvent(event="tool", data=ToolData(name="t", args={})),
        ToolDoneEvent(event="tool_done", data=ToolDoneData(name="t")),
        CheckEvent(event="check", data=CheckData(passed=True, issues=[])),
        DoneEvent(event="done", data=DoneData(content={}, title="t", attempts=1, issues_history=[])),
        ErrorEvent(event="error", data=ErrorData(message="e")),
    ]
    for event in events:
        dumped = event.model_dump()
        json_str = json.dumps(dumped, ensure_ascii=False)
        assert "event" in json_str
        assert "data" in json_str
