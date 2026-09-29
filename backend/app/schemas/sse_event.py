"""SSE（Server-Sent Events）事件 schema。

定义后端推给前端的"进度事件"——
前端 EventSource 收到这些事件，根据 event 类型更新 UI。

┌──────────────────────────────────────────────────────┐
│  SSE 协议格式：                                       │
│  event: status\n                                     │
│  data: {"stage":"generating","message":"..."}\n\n    │
│                                                       │
│  event 字段 = 事件类型（前端按这个分发处理）          │
│  data 字段 = JSON 字符串（事件数据）                  │
│  末尾的 \n\n = 一个事件结束的标志                     │
└──────────────────────────────────────────────────────┘
但是！我们这个项目直接套`astream` 行不通，原因在 deepseek.py 和 client.py 里：

1. LLM 输出是结构化 JSON ——`{"title":"职业` 这种半截 JSON 前端没法用
2. 工具调用循环 ——LLM 可能先调`search_web` ，再吐 JSON
3. 状态图自纠环 ——`generate → check → regenerate` ，可能跑 3 轮
所以 不能 简单把`astream` 套上去。

改造方案：流"进度事件"
我们的策略是 不破坏现有架构 ，只是把"黑盒过程"打开给前端看。后端把流程拆成一系列"事件"，用 SSE 推给前端：

event: status     data: {"stage":"generating","message":"开始生成第 1 段"}
event: tool       data: {"name":"search_web","query":"Python 工程师 技能要求"}
event: tool_done  data: {"name":"search_web"}
event: check      data: {"passed":false,"issues":["..."]}
event: status     data: {"stage":"regenerating","message":"第 2 次尝试"}
event: check      data: {"passed":true}
event: done       data: {"content":{...完整 JSON...}}

前端拿到`done` 才把完整内容塞进 UI，前面的事件都只是"过程展示"。

事件流顺序示例：
    status(generating) → tool(search_web) → tool_done →
    check(failed) → status(regenerating) → check(passed) → done
"""

__all__ = [ "SSEEvent" , "format_sse" ]
import json
from typing import Annotated, Literal , Union
from pydantic import BaseModel,Field

# ── 各事件的数据 schema ──
class StatusData(BaseModel):
    """流程状态变化（开始生成、重试中、完成、失败）。"""
    stage:Literal["generating","regenerating","done","failed"]
    message:str
    attempt : int =1
    section_index: int | None = None # 新增：默认 None=整体级，子图节点用它打段号标签

class ToolData(BaseModel):
    """LLM 决定调工具时触发。"""
    name: str # 工具名，如 "search_web"
    args: dict # 工具参数，如 {"query": "..."}

class ToolDoneData ( BaseModel ): 
    """工具执行完成。""" 
    name: str


class CheckData ( BaseModel ): 
    """质量检查结果。""" 
    passed: bool 
    issues: list [ str ] # 没过：问题列表；过了：空列表
    section_index: int | None = None # 新增

class DoneData ( BaseModel ): 
    """最终完成事件：携带完整内容 JSON。""" 
    content: dict # ResumeContent 的最终 content 字段 
    title: str 
    attempts: int # 总共尝试了几轮 
    issues_history: list [ dict ] # 自纠历史
    section_index: int | None = None # 新增

class ErrorData ( BaseModel ): 
    """出错事件。""" 
    message: str

# ── discriminated union：把 6 种事件包成一个类型 ── 
# # 用 Annotated + Field(discriminator="event") 告诉 Pydantic： 
# # "看到 event 字段是 status，就按 StatusEvent 解析 data"
# 联合类型：SSEEvent 可以是 6 种事件中的任一种 

class StatusEvent ( BaseModel ):
    event: Literal [ "status" ]
    data: StatusData
class ToolEvent ( BaseModel ):
    event: Literal [ "tool" ]
    data: ToolData 
class ToolDoneEvent ( BaseModel ):
    event: Literal [ "tool_done" ]
    data: ToolDoneData 
class CheckEvent ( BaseModel ):
    event: Literal [ "check" ]
    data: CheckData 
class DoneEvent ( BaseModel ):
    event: Literal [ "done" ]
    data: DoneData 
class ErrorEvent ( BaseModel ):
    event: Literal [ "error" ]
    data: ErrorData

#Union:意思：这个值 是 6 种事件中的某一种 。
#`Field(discriminator="event")` ——补充说明的内容,意思是区分这六种时，看event字段
#整体可以加快pydantic的匹配
SSEEvent = Annotated[
    Union [StatusEvent, ToolEvent, ToolDoneEvent, CheckEvent, DoneEvent, ErrorEvent],
    Field(discriminator= "event" ),
]

def format_sse(event:SSEEvent)->str:
    """把事件对象序列化成 SSE 协议字符串。

    输入：StatusEvent / ToolEvent / ... 之一
    输出：'event: status\\ndata: {"stage":"...","message":"..."}\\n\\n'

    前端 EventSource 收到这种格式会自动解析：
    - event 字段 → e.type
    - data 字段 → e.data（前端再 JSON.parse 一次）
    """
    payload = event.model_dump()
    event_type = payload[ "event" ]
    data_json = json.dumps(payload[ "data" ], ensure_ascii= False ) 
    return f"event: {event_type} \ndata: {data_json} \n\n"