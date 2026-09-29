# C+G 融合方案：LangGraph 多 Agent + MapReduce 并行

## Context（为什么做这个改动）

当前 AI 简历生成器的「内容生成」存在两个简化版短板：

1. **单段自纠环只有两节点**（[graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py) 的 `generate → check`）——讲不出"多智能体协作"故事。
2. **5 段是 ARQ worker 顺序逐段生成**（[worker/tasks.py#L96](file:///d:/development/ai-resume-generator/backend/app/worker/tasks.py#L96-L107) 的 `for section_index in range(...)`）——讲不出"任务编排 + 并发控制"故事。

用户从历史规划里选了 C+G 两个方向：
- **C. 多智能体协作**：升级成 主编 agent 调度 → 撰写 agent 产出 → 校对 agent 反馈
- **G. Agent 任务编排**：5 段并行生成 + 整体一致性校验节点

融合思路：一个 LangGraph 多节点图，主编 agent 调度 → Send API 派发 5 段并行 → 每段跑 撰写→校对 自纠环 → 整体一致性校验节点。

## 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 多 agent 模型 | 同一 DeepSeek + 不同 system prompt | 复用现有 `StructuredChatClient`，迁移成本低 |
| 并行机制 | LangGraph 1.2.11 的 Send API | 能讲任务编排，简历价值高 |
| 状态 schema | `dict[str, SectionGenState]` + 自定义 reducer | 多段并发需要 store list，section_index 做 key |
| SSE 扩展 | 现有事件加 `section_index: int \| None` 字段 | 默认 None=整体级，兼容现有单段端点 |
| 单段校对 | LLM 调用（不是纯代码），max_retries=2 | C 的"3 agent"必须 3 个 LLM；预算：5*2*2=20 次/简历 |
| 跨段一致性 | 纯代码规则版（不调 LLM） | 先跑通；后续可升级 |
| 一致性失败 | 标记 issues + END（不自动重排） | 简化第一版；自动重排是后续增强 |

## 架构图

```
                         ┌─────────────────────────────┐
START ──────────────────▶│  chief_editor (主编 Agent)    │
                         │  - 读大纲 + 简历信息            │
                         │  - 输出分稿计划 chief_plan      │
                         └──────────────┬──────────────┘
                                        │
                         conditional_edges: fan_out → [Send]*N
                                        │
       ┌──────────┬──────────┬─────────┴─────────┬──────────┬──────────┐
       ▼          ▼          ▼                   ▼          ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  section_subgraph_i  (SectionGenState，5 段并行)                  │
   │  START ─▶ writer ─▶ proofreader ─▶ should_continue              │
   │                                  │                              │
   │           ┌──────────────────────┼────────────────────┐         │
   │           ▼                       ▼                    ▼         │
   │       "revise"                 "end"                "end"       │
   │       (回 writer，              (通过)              (用完次数)    │
   │        带issues)                                               │
   └─────────────────────────────────────────────────────────────────┘
       │          │          │                   │          │
       └──────────┴──────────┴───────────────────┴──────────┘
                                        │
                          fan_in（Send 全部完成后自动汇合）
                                        ▼
                       ┌──────────────────────────────┐
                       │ cross_section_check          │
                       │ - 公司/学校重复              │
                       │ - 时间线矛盾                 │
                       │ - 技能冲突                   │
                       └──────────────┬───────────────┘
                                      │
                       ┌──────────────▼───────────────┐
                       │ all_done（落库 + 推 done）    │
                       └──────────────┬───────────────┘
                                      ▼
                                     END
```

## 实施步骤（8 阶段，每阶段独立可验证）

### 阶段 1：状态 schema + 自定义 reducer

**文件**：新建 [backend/app/llm/multi_section_state.py](file:///d:/development/ai-resume-generator/backend/app/llm/multi_section_state.py)

**关键代码思路**：
```python
from typing import Annotated, TypedDict

def merge_sections(left: dict, right: dict) -> dict:
    """Send 返回 {section_index_str: 整段 SectionGenState}，直接整段覆盖。"""
    out = {**left}
    out.update(right)
    return out

class SectionGenState(TypedDict):
    """单段子状态（在多段图里被 Send 派发，section_index 唯一标识）。"""
    section_index: int
    payload: ContentGenerationInput          # 复用现有
    plan: dict                                # 主编给的本段增强指引
    draft: ContentDraft | None
    issues: list[str]                         # 喂给 writer 的问题列表
    issues_history: list[dict]                # 硬约束：键名带复数 s
    attempts: int
    max_retries: int

class MultiSectionState(TypedDict):
    """多段图顶层状态。"""
    chief_plan: dict
    sections: Annotated[dict[str, SectionGenState], merge_sections]
    cross_issues: list[str]
    section_count: int
    round: int
    max_rounds: int
```

**坑**：TypedDict 不支持默认值，所有初始字段（`round=0`、`sections={}`、`chief_plan={}`）必须在 `graph.ainvoke({...})` 入参里塞齐。

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "from app.llm.multi_section_state import merge_sections; print(merge_sections({'0':{'a':1}}, {'1':{'a':2}}))"
```

---

### 阶段 2：单段子图升级（撰写/校对 Agent，C 的核心）

**文件**：[backend/app/llm/deepseek.py](file:///d:/development/ai-resume-generator/backend/app/llm/deepseek.py) + [backend/app/llm/graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py)

**子步骤**：

- **2.1** 在 `deepseek.py` 新增两个 Agent 类（**复用同一 `StructuredChatClient`**，只换 prompt）：
  - `DeepSeekWriterAgent`：system prompt 强化"按 plan 产出结构化 JSON"；`generate(payload, plan)` 返回 `ContentDraft`
  - `DeepSeekProofreaderAgent`：system prompt 强化"找问题，给定向修改建议"；`review(draft, payload)` 返回 `ProofReport(passed, issues)`——新增 Pydantic schema

- **2.2** 在 `graph.py` 加 `build_section_subgraph(writer, proofreader, on_event, section_index)` 工厂：
  - 节点 `writer_node` → `proofreader_node` → `should_continue`
  - 初始化用 `StateGraph(state_schema=SectionGenState)`（硬约束：必须 state_schema 参数）
  - max_retries=2（不是 3），控制 LLM 调用预算

- **2.3** 节点函数签名 `async def writer_node(state: SectionGenState) -> dict`，调 `writer.generate(payload, plan)`；`on_event` 推 `StatusEvent`（带 `section_index`）。

**坑**：
- 子图当主图节点时，子图 `on_event` 回调闭包捕获主图 queue，事件会冒到主图 SSE 流；但 `section_index` 必须在子图节点里塞进事件，不能依赖主图推断。
- 校对 LLM 调用要超时保护，沿用 `complete()` 已有的 `with_structured_output(json_mode)`。

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "from app.llm.graph import build_section_subgraph; print('ok')"
```

---

### 阶段 3：主编 Agent 节点

**文件**：[backend/app/llm/deepseek.py](file:///d:/development/ai-resume-generator/backend/app/llm/deepseek.py) + [backend/app/llm/graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py)

- **3.1** 在 `deepseek.py` 加 `ChiefEditorAgent`：
  - 输入：大纲 sections + 简历基本信息
  - 输出：`{"0": {"focus": "...", "tone_hint": "...", "avoid": [...]}, "1": {...}}` 的分稿计划
  - Prompt 强调"统一风格、段间公司/学校避免重复、各段重点互补"

- **3.2** 在 `graph.py` 加 `chief_editor_node(state)`：
  - 调 `chief_editor.generate(outline, resume_info)`
  - 返回 `{"chief_plan": plan, "round": state["round"] + 1}`

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "from app.llm.deepseek import ChiefEditorAgent; print('ok')"
```

---

### 阶段 4：Send API 并行 MapReduce（G 的核心）

**文件**：[backend/app/llm/graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py)

**子步骤**：

- **4.1** 引入 `from langgraph.types import Send`。写 `fan_out(state)` 路由函数：
  ```python
  def fan_out(state: MultiSectionState) -> list[Send]:
      """派发 5 段并行子图。每个 Send 启动一个 section_subgraph 实例。"""
      sends = []
      for i in range(state["section_count"]):
          plan_for_section = state["chief_plan"].get(str(i), {})
          sends.append(Send(
              "section_subgraph",    # 主图里注册的子图节点名
              {                       # Send 第二参数 = 子图入口 state（必须匹配 SectionGenState 字段）
                  "section_index": i,
                  "plan": plan_for_section,
                  "payload": ...,      # 从 outline 拼出来
                  "draft": None,
                  "issues": [],
                  "issues_history": [],
                  "attempts": 0,
                  "max_retries": 2,
              }
          ))
      return sends
  ```

- **4.2** 子图编译后注册到主图：`graph.add_node("section_subgraph", compiled_section_subgraph)`。LangGraph 1.2.11 支持把编译后的子图当节点。

- **4.3** `fan_in` 不需要显式节点——Send 机制天然等所有 Send 完成后才继续。直接 `graph.add_edge("section_subgraph", "cross_section_check")`。

**Send API 1.2.11 标准用法示例**（用户没用过，重点示范）：
```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

graph = StateGraph(state_schema=MultiSectionState)
graph.add_node("chief_editor", chief_editor_node)
graph.add_node("section_subgraph", compiled_section_subgraph)  # 子图当节点
graph.add_node("cross_section_check", cross_section_check_node)
graph.add_node("all_done", all_done_node)

graph.add_edge(START, "chief_editor")
graph.add_conditional_edges(
    "chief_editor",
    fan_out,                       # 路由函数返回 list[Send]
    ["section_subgraph"]           # 声明可能去向
)
graph.add_edge("section_subgraph", "cross_section_check")
graph.add_edge("cross_section_check", "all_done")
graph.add_edge("all_done", END)

compiled = graph.compile()
```

**坑**：
- `Send` 第一参数必须是主图里 `add_node(name, ...)` 注册的子图整体名，不是子图内部节点名。子图内部节点对外不可见。
- `Send` 第二参数 dict 的 key 必须是 `SectionGenState` 的合法字段，多传会报错。
- reducer `merge_sections` 必须纯函数，**不要 in-place 改 left**（LangGraph 内部缓存会乱）。

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "from langgraph.types import Send; print(Send('x', {'k':1}))"
```

---

### 阶段 5：整体一致性校验节点（G 的核心）

**文件**：新建 [backend/app/services/cross_section_check.py](file:///d:/development/ai-resume-generator/backend/app/services/cross_section_check.py) + [backend/app/llm/graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py)

- **5.1** 新建 `cross_section_check.py`，纯代码规则版：
  ```python
  def check_cross_section(sections: dict[str, SectionGenState]) -> list[str]:
      """返回问题列表，空=通过。"""
      issues = []
      # 规则 1：公司/学校跨段重复
      # 规则 2：工作段 period 字段时间线矛盾（重叠或缺口）
      # 规则 3：技能段 skill 名跟工作段 tech 字段对不上报冲突
      return issues
  ```

- **5.2** 在 `graph.py` 加 `cross_section_check_node(state)`：调上面的函数，推 `CrossCheckEvent`，返回 `{"cross_issues": issues}`。

- **5.3** 失败不自动重排（第一版简化），直接走 END，让用户看 cross_issues 自行决定重新触发。

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "from app.services.cross_section_check import check_cross_section; print(check_cross_section({}))"
```

---

### 阶段 6：并发瓶颈治理（关键约束）

**文件**：[backend/app/llm/retriever.py](file:///d:/development/ai-resume-generator/backend/app/llm/retriever.py) + [backend/app/llm/embedding.py](file:///d:/development/ai-resume-generator/backend/app/llm/embedding.py) + [backend/app/core/db.py](file:///d:/development/ai-resume-generator/backend/app/core/db.py)

- **6.1** 改 `retriever.py`：把 `asyncpg.connect` 换成模块级 `asyncpg.create_pool(dsn, min_size=1, max_size=3)` 单例（懒加载 + `asyncio.Lock` 防并发初始化）。函数内 `async with pool.acquire() as conn:`。再加 `asyncio.Semaphore(3)` 限流并发检索数。
- **6.2** 改 `embedding.py`：模块级 `Semaphore(2)` 包住 `dashscope.TextEmbedding.call`（必须包在 `asyncio.to_thread` 外层，包内层没用）。
- **6.3** 改 `core/db.py`：显式给 SQLAlchemy engine 配 `pool_size=10, max_overflow=5`（默认 5+0 不够 5 段并发 + 主流程）。

**坑**：`asyncpg.create_pool` 是协程，要在 `async def` 里 `await`，不能模块级直接 `pool = create_pool(...)`。用 `asyncio.Lock + 懒加载` 模式。

**验证命令**：
```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from app.llm.retriever import _get_pool; asyncio.run(_get_pool()); print('pool ok')"
```

---

### 阶段 7：SSE 多段事件 + 流式工作流 + 端点

**文件**：[backend/app/schemas/sse_event.py](file:///d:/development/ai-resume-generator/backend/app/schemas/sse_event.py) + [backend/app/workflows/content.py](file:///d:/development/ai-resume-generator/backend/app/workflows/content.py) + [backend/app/api/v1/contents.py](file:///d:/development/ai-resume-generator/backend/app/api/v1/contents.py)

- **7.1** 改 `sse_event.py`：
  - `StatusData`/`CheckData`/`DoneData` 都加 `section_index: int | None = None`（默认 None=整体级，兼容现有单段端点）
  - 新增 `SectionDoneEvent`（`event="section_done"`，data=`{section_index, content, title, attempts, issues_history}`）
  - 新增 `CrossCheckEvent`（`event="cross_check"`，data=`{issues, passed}`）
  - 新增 `AllDoneEvent`（`event="all_done"`，data=`{section_count, cross_issues}`）
  - 把这些加进 `SSEEvent` 的 `Union` 和 `__all__`

- **7.2** 在 `workflows/content.py` 加 `generate_all_contents_streaming(session, resume_id)`：
  - 参照现有 `generate_content_streaming` 的 Queue 模式
  - 内部跑阶段 1-5 编译出来的多 agent 图
  - `on_event` 把事件塞 Queue，主循环 `yield format_sse`
  - 落库策略：每段 `section_done` 事件触发时调 `_finalize_success` 落该段；全部 done 后落 `resume.status = "ready"`

- **7.3** 在 `api/v1/contents.py` 加端点 `POST /resumes/{id}/contents/generate/stream`：
  ```python
  @router.post("/generate/stream")
  async def generate_contents_stream(
      resume_id: uuid.UUID, session: SessionDep
  ) -> StreamingResponse:
      return StreamingResponse(
          generate_all_contents_streaming(session, resume_id),
          media_type="text/event-stream",
          headers={
              "Cache-Control": "no-cache",       # 硬约束 8
              "X-Accel-Buffering": "no",
              "Connection": "keep-alive",
          },
      )
  ```
  保留旧端点 `/regenerate/stream` 不动（兼容单段重生成）。

**坑**：
- StreamingResponse 必须带 4 个 SSE 响应头（硬约束 8）
- 流式生成器用 yield 不用 return（硬约束）
- 子图 `on_event` 闭包捕获主图 queue，事件会冒上来——但 section_index 必须在子图节点里塞好

**验证命令**：
```powershell
curl.exe -N -X POST "http://127.0.0.1:39801/api/v1/resumes/9e376ab0-ede2-4ae0-90b1-4a2b0a5603e9/contents/generate/stream"
```

---

### 阶段 8：前端多段并行进度 UI

**文件**：新建 `frontend/src/features/resumes/useMultiSectionStream.ts` + 新建 `frontend/src/components/MultiSectionProgressGrid.tsx` + 改 `frontend/src/pages/ResumeDetailPage.tsx`

- **8.1** 新建 `useMultiSectionStream` hook（参照 `useRegenerateContentStream` 在 [api.ts#L346](file:///d:/development/ai-resume-generator/frontend/src/features/resumes/api.ts#L346) 的 fetch + ReadableStream 分帧骨架）：
  - 端点换 `/contents/generate/stream`（不带 sectionIndex）
  - 进度状态从单对象改成 `Map<number, SectionProgress>` + `crossIssues: string[]` + `stage: 'idle'|'streaming'|'done'|'error'`
  - 事件扩展：`section_done` / `cross_check` / `all_done`

- **8.2** 新建 `MultiSectionProgressGrid` 组件：5 个卡片网格，每卡显示该段 stage、attempt、checkIssues；卡片间用 line 连接，已完成变绿。

- **8.3** 改 `ResumeDetailPage.tsx` 的 `GenerateContentsButton`（[L526](file:///d:/development/ai-resume-generator/frontend/src/pages/ResumeDetailPage.tsx#L526)）：触发 `useMultiSectionStream.start()` 而非 `useGenerateContents`（同步 ARQ 版）；流式期间显示 `MultiSectionProgressGrid` 替换原骨架。

---

## 端到端验证

1. **后端 import 检查**：
   ```powershell
   .\.venv\Scripts\python.exe -c "from app.llm.graph import build_multi_section_graph; print('ok')"
   ```

2. **单测 reducer + cross_check**：
   ```powershell
   .\.venv\Scripts\python.exe -c "from app.llm.multi_section_state import merge_sections; print(merge_sections({'0':{'a':1}}, {'1':{'a':2}}))"
   ```

3. **整体流式端点**：
   ```powershell
   curl.exe -N -X POST "http://127.0.0.1:39801/api/v1/resumes/9e376ab0-ede2-4ae0-90b1-4a2b0a5603e9/contents/generate/stream"
   ```
   预期事件流：`status(chief_planning)` → 5 个并发 `status(writing, section_index=0..4)` + `check` → 5 个 `section_done` → `cross_check` → `all_done`

4. **数据库验证**：
   ```sql
   SELECT section_index, status, revision FROM resume_contents WHERE resume_id = '9e376ab0-ede2-4ae0-90b1-4a2b0a5603e9' ORDER BY section_index;
   ```
   预期：5 行 status='ready'，revision >= 1

5. **LangSmith trace**：dashboard 看主图嵌套子图的 run tree，5 个 section_subgraph 应该并列展开

6. **前端 UI**：浏览器开 `http://127.0.0.1:39173`，进简历详情页，点"批量生成"，看 5 段卡片并发进度

## 关键文件清单

| 文件 | 改动 |
|---|---|
| [backend/app/llm/multi_section_state.py](file:///d:/development/ai-resume-generator/backend/app/llm/multi_section_state.py) | 新建：状态 schema + reducer |
| [backend/app/llm/deepseek.py](file:///d:/development/ai-resume-generator/backend/app/llm/deepseek.py) | 新增 ChiefEditorAgent / WriterAgent / ProofreaderAgent，复用同一 StructuredChatClient |
| [backend/app/llm/graph.py](file:///d:/development/ai-resume-generator/backend/app/llm/graph.py) | 新增 build_section_subgraph + build_multi_section_graph + chief_editor_node / cross_section_check_node / all_done_node |
| [backend/app/services/cross_section_check.py](file:///d:/development/ai-resume-generator/backend/app/services/cross_section_check.py) | 新建：跨段一致性校验（纯代码版） |
| [backend/app/llm/retriever.py](file:///d:/development/ai-resume-generator/backend/app/llm/retriever.py) | asyncpg pool + Semaphore(3) |
| [backend/app/llm/embedding.py](file:///d:/development/ai-resume-generator/backend/app/llm/embedding.py) | Semaphore(2) |
| [backend/app/core/db.py](file:///d:/development/ai-resume-generator/backend/app/core/db.py) | pool_size=10, max_overflow=5 |
| [backend/app/schemas/sse_event.py](file:///d:/development/ai-resume-generator/backend/app/schemas/sse_event.py) | 加 section_index + 3 个新事件 |
| [backend/app/workflows/content.py](file:///d:/development/ai-resume-generator/backend/app/workflows/content.py) | 新增 generate_all_contents_streaming |
| [backend/app/api/v1/contents.py](file:///d:/development/ai-resume-generator/backend/app/api/v1/contents.py) | 新增 POST /generate/stream 端点 |
| [frontend/src/features/resumes/api.ts](file:///d:/development/ai-resume-generator/frontend/src/features/resumes/api.ts) | 新增 useMultiSectionStream hook |
| [frontend/src/components/MultiSectionProgressGrid.tsx](file:///d:/development/ai-resume-generator/frontend/src/components/MultiSectionProgressGrid.tsx) | 新建：5 段并发进度卡片网格 |
| [frontend/src/pages/ResumeDetailPage.tsx](file:///d:/development/ai-resume-generator/frontend/src/pages/ResumeDetailPage.tsx#L526) | 改 GenerateContentsButton 触发流式 |

## 执行约束

- **后端代码用户自己敲**：我只给代码片段让用户写，不用 Write/Edit 工具直接改后端文件；给验证命令让用户跑
- **前端代码可用 Write/Edit 直接写**
- 中文回答 + 代码注释中文
- 字典键必须用 `issues_history`（复数 issues）
- SSE 事件用 Pydantic discriminated union，event 字段做判别
- 流式生成器用 yield 不用 return
- StreamingResponse 必须带 4 个 SSE 响应头
- LangGraph StateGraph 初始化必须用 `state_schema` 参数（非 input_schema）
