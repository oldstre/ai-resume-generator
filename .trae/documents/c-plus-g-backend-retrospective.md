# C+G 阶段后端复盘总结

> **复盘范围**：只复盘后端（阶段 1-7，阶段 8 是前端 UI 不在范围内）
> **复盘时间**：2026-09-27
> **复盘目的**：理清多智能体协作 + Agent 任务编排（C+G）方案的设计思路、踩过的坑、最终产物，方便后续维护时快速回忆。

***

## 目录

- [1. 背景：为什么要升级到 C+G 方案](#1-背景为什么要升级到-cg-方案)
- [2. 架构总览](#2-架构总览)
- [3. 分阶段复盘](#3-分阶段复盘)
  - [阶段 1：状态 schema + 自定义 reducer](#阶段-1状态-schema--自定义-reducer)
  - [阶段 2：单段子图升级（writer + proofreader + 自纠环）](#阶段-2单段子图升级writer--proofreader--自纠环)
  - [阶段 3：主编 Agent（ChiefEditorAgent）](#阶段-3主编-agentchieveditoragent)
  - [阶段 4：Send API 并行 MapReduce](#阶段-4send-api-并行-mapreduce)
  - [阶段 5：跨段一致性校验](#阶段-5跨段一致性校验)
  - [阶段 6：并发瓶颈治理（retriever pool + embedding Semaphore）](#阶段-6并发瓶颈治理retriever-pool--embedding-semaphore)
  - [阶段 7：SSE 多段事件 + 流式端点](#阶段-7sse-多段事件--流式端点)
- [4. 核心设计模式（通用经验）](#4-核心设计模式通用经验)
- [5. 经验教训（硬约束清单）](#5-经验教训硬约束清单)
- [6. 最终产物](#6-最终产物)

***

## 1. 背景：为什么要升级到 C+G 方案

### 1.1 旧方案有什么痛点

升级前，内容生成是这样跑的（V1：单段串行 + ARQ 批量）：

```
POST /resumes/{id}/contents/generate  →  ARQ worker 后台跑  →  5 段串行生成
前端 2 秒轮询 GET /contents 看状态
```

四个核心痛点：

1. **串行耗时累加**：5 段内容一段一段生成，总耗时 = 5 段耗时之和（大概 5 × 30 秒 = 2.5 分钟）
2. **一个 LLM 干所有事**：撰写 + 自我检查 + 跨段协调，全靠一个 system prompt。LLM 角色混乱，输出质量不稳定
3. **校验是纯代码规则**（`quality_check.py` 的 `check_content`）：只能查"字段为空""数组为空"这种表层问题，查不出"工作经历段写的公司和技能段写的公司重复"这种语义问题
4. **前端只能轮询**：用户点了"生成"按钮就转圈圈，看不到 AI 在做什么，要等几十秒才知道结果

### 1.2 C+G 是什么

- **C = Collaboration（多智能体协作）**：把一个 LLM 拆成多个角色（主编 / 撰写 / 校对），各司其职
- **G = Graph（任务编排）**：用 LangGraph 状态图管理节点之间的依赖关系——谁先跑、谁并发、谁后跑

### 1.3 升级目标

| 维度   | V1 旧方案           | C+G 新方案                                      |
| ---- | ---------------- | -------------------------------------------- |
| 耗时   | 5 段串行累加 \~2.5 分钟 | 5 段并发，等于最慢那段 \~30 秒                          |
| 角色   | 1 个 LLM 包揽       | 主编 + 撰写 + 校对 + 跨段校验分工                        |
| 校验   | 纯代码规则（查空字段）      | LLM 语义校对（查内容矛盾/编造）+ 跨段重复检查                   |
| 进度推送 | 前端 2 秒轮询         | SSE 流式实时推送（每段每个事件都推）                         |
| 自纠   | 一遍过              | writer → proofreader → 没过回 writer 重写（最多 2 次） |

***

## 2. 架构总览

### 2.1 三层图结构

```
主图 build_multi_section_graph
─────────────────────────────────────────────────────────
START → chief_editor → fan_out（派发 5 个并发 Send）
                          ↓
                   section_subgraph（× 5 并发）
                          ↓
                   cross_check（跨段校验）
                          ↓
                     all_done → END
─────────────────────────────────────────────────────────

子图 build_section_subgraph（5 段各跑一份实例）
─────────────────────────────────────────────────────────
START → writer → proofreader → should_continue?
                                  ├─ "end"（通过/用完次数）→ END
                                  └─ "revise"（没过）→ 回 writer 重写
─────────────────────────────────────────────────────────
```

### 2.2 四个 Agent 角色

| Agent                      | 角色   | 输入                   | 输出                                     | 调 LLM?                     |
| -------------------------- | ---- | -------------------- | -------------------------------------- | -------------------------- |
| `ChiefEditorAgent`         | 调度   | 大纲 + 简历基本信息          | `ChiefPlan`（每段 focus/tone\_hint/avoid） | 是（不带工具）                    |
| `DeepSeekWriterAgent`      | 产出   | 单段 payload + 主编 plan | `ContentDraft`                         | 是（带 `search_web` 工具 + RAG） |
| `DeepSeekProofReaderAgent` | 反馈   | draft + payload      | `ProofReport`（passed + issues）         | 是（不带工具）                    |
| `cross_check` 节点           | 跨段校验 | 5 段 draft            | `cross_issues` 列表                      | 否（纯代码规则）                   |

### 2.3 状态流转

类比：**状态 = 档案柜，reducer = 档案管理员**

```
MultiSectionState（顶层档案柜）
├─ chief_plan          ← 主编产出
├─ sections: dict      ← 5 段子档案，靠 merge_sections reducer 合并
│   ├─ "0": SectionGenState
│   ├─ "1": SectionGenState
│   └─ ...
├─ cross_issues        ← 跨段校验产出
└─ 元信息（section_count / round / resume_id / outline_sections / resume_basics）

SectionGenState（单段子档案）
├─ section_index       ← 段号（0-based）
├─ payload             ← 简历信息 + 该段大纲信息
├─ plan                ← 主编给的本段指引
├─ draft               ← writer 最新产出
├─ issues              ← proofreader 给的问题列表（空 = 通过）
├─ issues_history      ← 自纠历史（硬约束：键名复数 s）
└─ attempts / max_retries
```

***

## 3. 分阶段复盘

### 阶段 1：状态 schema + 自定义 reducer

**文件**：`backend/app/llm/multi_section_state.py`

#### 设计动机

5 段并发时，每个子图跑完都要把结果塞回主图的 `sections` 字段。如果用默认合并方式（LangGraph 默认是字典浅合并），5 个 Send 同时写会出现：A 写完段 0，B 同时写完段 1，最后只剩最后一个写的——前面的被覆盖。

需要的是：**按段号整份覆盖**，不动别的段。

#### 关键代码

```python
def merge_sections(left: dict, right: dict) -> dict:
    """自定义 reducer：按段号整份覆盖。
    left：当前档案柜里 sections 字段的值
    right：本次 Send 返回的 sections 字段值（一般只有一个 key）
    """
    out = {**left}        # 1. 复制 left
    out.update(right)     # 2. 用 right 覆盖
    return out            # 3. 返回新字典
```

在 `MultiSectionState` 里这样注解：

```python
class MultiSectionState(TypedDict):
    sections: Annotated[dict[str, SectionGenState], merge_sections]
```

`Annotated[类型, reducer]` 是 LangGraph 的标准写法，告诉框架："这个字段每次更新时都调 `merge_sections(旧值, 新值)` 合并"。

#### 踩过的坑

**坑 1：in-place 修改导致内部缓存错乱**

最初写成：

```python
def merge_sections(left, right):
    left.update(right)   # ❌ 直接改 left
    return left
```

LangGraph 内部缓存会保留 left 的引用，下次 reducer 调用时 left 已经被改过了，导致状态不一致。**必须返回新对象**：`out = {**left}; out.update(right); return out`。

**坑 2：TypedDict 不支持默认值**

`SectionGenState` 里的 `attempts`、`issues`、`issues_history`、`draft` 这些字段有"默认值"需求（初始都是空/None），但 TypedDict 不像 Pydantic BaseModel 能写默认值。**所有默认字段必须在** **`graph.ainvoke({...})`** **入参里塞齐**——这就是为什么阶段 4 的 `fan_out` 函数构造 sub\_state 时要把这些字段全写出来的原因。

**坑 3：键名拼写一致性**

`issues_history` 这个键名（复数 `s`）一旦定下来，**所有地方都必须用复数 s**。子图节点写、reducer 读、落库存、前端解析、SSE DoneData 字段——只要有一处写成 `issue_history`（单数），就会静默运行时错误（dict 拿不到值不报 KeyError，返回 None，下游默默传错数据）。这个坑后来被记进项目硬约束。

***

### 阶段 2：单段子图升级（writer + proofreader + 自纠环）

**文件**：`backend/app/llm/graph.py` 的 `build_section_subgraph`、`backend/app/llm/deepseek.py` 的 `DeepSeekWriterAgent` 和 `DeepSeekProofReaderAgent`

#### 设计动机

V1 是"一遍过"：生成 → 校验 → 完成。问题是没有自纠——校验不通过就只能整段重新生成。

C+G 加自纠环：

```
START → writer → proofreader → should_continue?
                                ├─ "end"（通过 / 用完次数）→ END
                                └─ "revise"（没过）→ 回 writer 重写
```

`max_retries=2` 控制预算：最多 2 次自纠机会（第 1 次生成 + 2 次重写），再不行就接受当前草稿。

#### LangGraph 节点函数签名的限制

LangGraph 节点函数签名必须是 `(state) -> dict`——返回的是状态增量，框架拿这个增量调 reducer 合并到主 state。

这就有个问题：节点函数里要调 Agent 实例，但签名不允许传 Agent 进来。**解法是闭包**：

```python
def build_section_subgraph(writer, proofreader, on_event=None):
    """工厂函数：闭包注入 writer / proofreader / on_event。"""
    async def writer_node(state: SectionGenState) -> dict:
        # writer 在闭包里可见，state 也通过参数传入
        draft = await writer.generate(payload, state.get("plan", {}))
        return {"draft": draft, "attempts": state["attempts"] + 1}

    # ... proofreader_node 类似
    subgraph = StateGraph(state_schema=SectionGenState)
    subgraph.add_node("writer", writer_node)
    # ...
    return subgraph.compile()
```

工厂函数 `build_section_subgraph` 接收 Agent 实例，内部定义节点函数（闭包捕获 Agent），最后编译子图返回。**同一个编译子图可以被 5 个 Send 并发实例复用**，每个实例拿自己的 state 跑。

#### WriterAgent 继承复用

旧的 `DeepSeekContentGenerator` 已经有 `_user_prompt` / `_example_for` / `_validate_draft` 这些方法，子类 `DeepSeekWriterAgent` 不重写它们，只重写两个：

- `generate(payload, plan)`：多接一个 `plan` 参数（主编指引）
- `_system_prompt(payload, plan)`：在父类基础上追加"主编指引"段

```python
class DeepSeekWriterAgent(DeepSeekContentGenerator):
    async def generate(self, payload, plan=None) -> ContentDraft:
        samples = await retrieve_samples(...)  # RAG 检索
        draft = await self._chat.complete_with_tools(
            ContentDraft, ..., tools=[search_web],
        )
        self._validate_draft(draft)
        return draft

    def _system_prompt(self, payload, plan=None) -> str:
        base_prompt = super()._system_prompt(payload)
        if not plan:
            return base_prompt
        return base_prompt + f"\n主编指引（必须遵循）：\n- 本段重点：{plan.get('focus', '')}\n..."
```

清理旧代码后，父类 `DeepSeekContentGenerator` 不再有 `generate()` 方法——旧的同步单段生成入口（`generate_content` / `generate_content_streaming`）已删，父类只保留共用辅助方法。

#### 踩过的坑

**坑 1：section\_index 不能闭包写死**

最初 `build_section_subgraph` 写成 `build_section_subgraph(writer, proofreader, section_index, on_event)`，把 `section_index` 当参数传进来。这样**只能生成一份子图实例**——5 段都要用同一个编译子图，段号写死就跑不了 5 段并发。

**解法**：`section_index` 从 `state["section_index"]` 动态拿，闭包不写死。`fan_out` 派发时每个 Send 的 sub\_state 里塞自己的 `section_index`，子图节点函数从 state 取。

**坑 2：子图作为节点时返回值对不上主图字段**

把编译子图直接 `graph.add_node("section_subgraph", compiled_subgraph)` 当主图节点用——子图 final state 的字段（`draft` / `attempts` / `issues` ...）和主图的 `sections` 字段对不上，LangGraph 会**默默忽略**这些字段，主图 state 不更新。

**解法**：包一层包装节点：

```python
async def section_subgraph_node(state: SectionGenState) -> dict:
    sub_result = await section_subgraph.ainvoke(state)
    section_index = sub_result["section_index"]
    # 把子图 final state 包装成主图 sections 字段格式
    return {"sections": {str(section_index): sub_result}}
```

包装节点调子图，把子图整份 state 塞进 `{"sections": {str(idx): state}}`，LangGraph 调 `merge_sections` reducer 按 `section_index_str` 合并 5 个 Send 的结果。这是阶段 4 才完整解决的，阶段 2 先埋下这个坑。

***

### 阶段 3：主编 Agent（ChiefEditorAgent）

**文件**：`backend/app/llm/deepseek.py` 的 `ChiefEditorAgent`、`backend/app/llm/graph.py` 的 `make_chief_editor_node`

#### 设计动机

5 段并发撰写前，如果没有任何协调，每个 writer 各自为政：

- 5 段都可能写"XX 公司"
- 技能段列了 Python，工作经历段又写了一遍
- 风格不统一（有的偏严谨有的偏成就导向）

主编先扫一遍大纲，给每段定 `focus`（重点写什么）、`tone_hint`（语气提示）、`avoid`（避免什么），writer 按这个指引写。

#### 输出 schema：ChiefPlan

```python
class ChiefPlan(BaseModel):
    sections: dict[str, SectionPlan]  # key 是段号字符串 "0"/"1"/...

class SectionPlan(BaseModel):
    focus: str          # 本段重点写什么
    tone_hint: str      # 语气提示（专业严谨/成就导向/简洁有力）
    avoid: list[str]   # 本段要避免的内容
```

为什么 key 用字符串？因为 JSON 字典的 key 必须是字符串，LLM 输出的 JSON 里 `"0"` 和 `"1"` 是字符串，主图 state 里也保持字符串 key，`merge_sections` 按 `str(section_index)` 合并。

#### 主编节点：工厂函数模式

主编节点也用闭包注入 Agent：

```python
def make_chief_editor_node(chief_editor, on_event=None):
    async def chief_editor_node(state: MultiSectionState) -> dict:
        plan = await chief_editor.generate(
            state["outline_sections"],
            state["resume_basics"],
        )
        return {
            "chief_plan": plan.model_dump()["sections"],
            "round": state["round"] + 1,
        }
    return chief_editor_node
```

注意：`ChiefPlan` 用 `model_dump()` 转成 dict 再取 `["sections"]`，因为 state 里的 `chief_plan` 字段类型是 `dict` 不是 Pydantic 模型——LangGraph 节点间传 dict 更稳，避免 Pydantic 序列化坑。

#### 主编不需要工具

主编用 `complete()`（不带工具），不用 `complete_with_tools()`。主编是调度角色，不需要联网搜索——搜索是撰写 agent 在生成具体内容时才需要的。

***

### 阶段 4：Send API 并行 MapReduce

**文件**：`backend/app/llm/graph.py` 的 `build_multi_section_graph` 和 `fan_out`

#### 设计动机

主编跑完产出 `chief_plan` 后，要派发 5 段并发撰写。LangGraph 的 **Send API** 就是干这个的——从一个节点派发 N 个子任务并发执行。

#### Send API 是什么

`Send` 是 LangGraph 的一个类型，告诉框架："拿这份 state 去跑某某节点一次"。

```python
from langgraph.types import Send

def fan_out(state: MultiSectionState) -> list[Send]:
    """根据 chief_plan 派发 N 个并发 Send 到 section_subgraph 节点。"""
    sends = []
    for i in range(state["section_count"]):
        section = state["outline_sections"][i]
        payload = ContentGenerationInput(...)        # 构造段 i 的 payload
        plan_i = state["chief_plan"].get(str(i), {})  # 段 i 的主编指引
        sub_state: SectionGenState = {
            "section_index": i,
            "payload": payload,
            "plan": plan_i,
            "draft": None,
            "issues": [],
            "issues_history": [],
            "attempts": 0,
            "max_retries": max_retries,
        }
        sends.append(Send("section_subgraph", sub_state))
    return sends
```

返回 `list[Send]`，LangGraph 看到 list 就并发执行所有 Send。

#### 关键：条件边 + Send 派发

主图里这样连：

```python
graph.add_edge(START, "chief_editor")
graph.add_conditional_edges(
    "chief_editor",      # 起点
    fan_out,             # 派发函数
    ["section_subgraph"] # 告诉 LangGraph fan_out 派发的目标节点列表
)
```

`add_conditional_edges` 第二参数传 `fan_out` 函数，函数返回 `list[Send]` 时 LangGraph 并发派发；返回普通字符串（"end" / "revise" 之类）时按字符串走单边。

第三参数 `["section_subgraph"]` 是个**目标节点声明**——LangGraph 要求提前列出 fan\_out 可能派发的目标节点（这跟静态分析图结构有关，框架要预先知道有哪些边）。

#### 包装节点（阶段 2 埋的坑在这填）

阶段 2 说的"子图当节点返回值对不上"——这里用包装节点解决：

```python
async def section_subgraph_node(state: SectionGenState) -> dict:
    sub_result = await section_subgraph.ainvoke(state)
    section_index = sub_result["section_index"]
    return {"sections": {str(section_index): sub_result}}
```

`section_subgraph_node` 是主图的节点（接收 `SectionGenState`，因为 Send 派发的 state 就是 `SectionGenState`），内部调子图 `section_subgraph.ainvoke(state)` 跑完自纠环，把子图 final state 包装成 `{"sections": {str(idx): state}}` 返回。LangGraph 拿这个返回值调 `merge_sections` reducer 合并 5 个 Send 的结果到主图 `sections` 字段。

#### 5 段汇合后继续

```python
graph.add_edge("section_subgraph", "cross_check")
graph.add_edge("cross_check", "all_done")
graph.add_edge("all_done", END)
```

LangGraph 看到 5 个 Send 都跑完（汇合点）后，继续走 `cross_check` → `all_done` → END。

***

### 阶段 5：跨段一致性校验

**文件**：`backend/app/llm/graph.py` 的 `make_cross_check_node`

#### 设计动机

5 段并发撰写后，可能出现：

- 段 3（工作经历）写了"XX 公司"，段 4（项目经历）也写了"XX 公司"——重复
- 段 2（技能）列了 Python，段 4（项目）也列了 Python——重复

跨段校验节点扫一遍所有段的 draft，找跨段重复的公司/学校/技能名。

#### 实现：纯代码规则（不调 LLM）

第一版用纯代码规则，不调 LLM——LLM 调一次几秒，跨段校验如果也调 LLM 太慢，纯代码扫一遍几百毫秒搞定。

```python
def _extract_keywords(content, found):
    """递归从 content dict 里找 company/school/skill 字段值。"""
    if isinstance(content, dict):
        for key, val in content.items():
            if key in ("company", "school", "skill") and isinstance(val, str) and len(val) > 2:
                found.add(val)
            else:
                _extract_keywords(val, found)
    elif isinstance(content, list):
        for item in content:
            _extract_keywords(item, found)

async def cross_check_node(state: MultiSectionState) -> dict:
    section_keywords = {}
    for k, sub_state in state["sections"].items():
        draft = sub_state.get("draft")
        if draft is None:
            section_keywords[k] = set()
            continue
        found = set()
        _extract_keywords(draft.content, found)
        section_keywords[k] = found

    # 找跨段重复
    issues = []
    keys = sorted(section_keywords.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            common = section_keywords[keys[i]] & section_keywords[keys[j]]
            if common:
                issues.append(f"段{keys[i]}与段{keys[j]}重复内容: {sorted(common)}")
    return {"cross_issues": issues}
```

#### 留扩展：cross\_issues 非空未来可回 chief\_editor 重排

第一版 `cross_issues` 非空时只是记录，不做后续动作（5 段还是按当前结果落库）。预留扩展：未来可以让 `cross_issues` 非空时回 `chief_editor` 重排（这是 `MultiSectionState` 里 `round` / `max_rounds` 字段留的口子，第一版固定 `max_rounds=1` 不自动重排）。

#### all\_done 节点：推事件不改 state

```python
async def all_done_node(state: MultiSectionState) -> dict:
    if on_event is not None:
        await on_event(StatusEvent(
            event="status",
            data=StatusData(stage="done", message="5 段内容生成完成"),
        ))
    return {}  # 不改 state
```

落库交给 workflow 层（`generate_all_contents_streaming`）——节点只管推事件，落库这种"重操作"在节点外做，避免节点函数承担太多职责。

***

### 阶段 6：并发瓶颈治理（retriever pool + embedding Semaphore）

**文件**：`backend/app/llm/retriever.py`

#### 问题：5 段并发 RAG 检索导致连接震荡

每个 writer 都要调 `retrieve_samples` 做一次 RAG 检索。V1 是每次调用都新建 asyncpg 连接 + 关闭——5 段并发时同时新建 5 个连接，跑完关闭，下一轮又新建。连接震荡严重，PostgreSQL 端连接数飙升，asyncpg 也频繁握手。

#### 解法 1：全局 asyncpg 连接池

```python
_pool: asyncpg.Pool | None = None

async def _get_pool() -> asyncpg.Pool:
    """懒加载全局连接池。5 段并发共用一个池，复用连接。"""
    global _pool
    if _pool is None:
        settings = get_settings()
        dsn = settings.pgvector_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,        # 常驻 2 个连接
            max_size=8,        # 最多 8 个并发连接（够 5 段 + buffer）
            command_timeout=10,
        )
    return _pool
```

5 段并发从同一个池拿连接，用完还回池，下一段复用。`min_size=2` 保 2 个常驻连接，`max_size=8` 上限够 5 段 + buffer。应用关闭时调 `close_pool()` 释放。

#### 解法 2：SQL 移除 target\_position 精确匹配

V1 的 SQL：

```sql
SELECT * FROM resume_sample
WHERE target_position = $1
ORDER BY embedding <=> $2 LIMIT $3
```

问题：精确匹配 `target_position` 太严——"Python 后端"查不到"后端工程师"的样本，5 段并发时多个段查同一岗位样本全空，RAG 失去作用。

C+G 改造：移除 `WHERE target_position = $1`，全库向量相似度检索：

```sql
SELECT target_position, section_title, content
FROM resume_sample
ORDER BY embedding <=> $1 LIMIT $2
```

只用段标题向量做近邻检索，不管岗位名——结果集更大更灵活。检索结果样本里带 `target_position` 字段，日志记录用了哪些岗位的样本（便于结果分析）。

#### 解法 3：embedding 调用并发限流

`embed_text` 函数（给段标题向量化）也加了 `asyncio.Semaphore` 限流，避免 5 段同时调 embedding API 触发限速。

#### 降级策略

检索失败不阻塞生成：

```python
except Exception as error:
    logger.warning("RAG 检索失败，降级返回空列表: %s: %s", type(error).__name__, error)
    return []
```

RAG 是增强不是必需，失败就空列表，writer 不带参考材料也能生成（只是质量略降）。

***

### 阶段 7：SSE 多段事件 + 流式端点

**文件**：`backend/app/schemas/sse_event.py`、`backend/app/workflows/content.py`、`backend/app/api/v1/contents.py`

#### 7.1 SSE 事件 schema：discriminated union

SSE 事件有 6 种：`status` / `tool` / `tool_done` / `check` / `done` / `error`。用 Pydantic **discriminated union** 包成一个类型：

```python
class StatusEvent(BaseModel):
    event: Literal["status"]
    data: StatusData

class CheckEvent(BaseModel):
    event: Literal["check"]
    data: CheckData

# ... 6 种事件类

SSEEvent = Annotated[
    Union[StatusEvent, ToolEvent, ToolDoneEvent, CheckEvent, DoneEvent, ErrorEvent],
    Field(discriminator="event"),
]
```

`Field(discriminator="event")` 告诉 Pydantic："看到 `event` 字段是 `status` 就按 `StatusEvent` 解析"。这样 `format_sse(event: SSEEvent)` 一个函数处理所有事件类型，Pydantic 自动按 event 字段分发。

#### 7.2 关键设计：事件加 `section_index` 字段

V1 事件不带段号（单段生成不需要）。C+G 5 段并发时，前端收到一个 `status` 事件，不知道是哪段在生成。所以给所有 data schema 加 `section_index` 字段：

```python
class StatusData(BaseModel):
    stage: Literal["generating", "regenerating", "done", "failed"]
    message: str
    attempt: int = 1
    section_index: int | None = None  # None = 整体级事件，有值 = 段级事件
```

`None` 表示整体级事件（主编开始、全部完成等），有值表示段级事件（某段 writer 开始、某段 proofreader 校对等）。前端按 `section_index` 聚合到 5 段状态。

#### 7.3 流式生成器：asyncio.Queue + SENTINEL 哨兵

```python
async def generate_all_contents_streaming(
    session: AsyncSession, resume_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    SENTINEL = object()
    queue: asyncio.Queue = asyncio.Queue()

    # ... 准备阶段（查简历 + 大纲 + 校验，失败 yield error 事件 return）

    # on_event 回调：节点调它推事件，事件塞进 queue
    async def on_event(event):
        await queue.put(format_sse(event))

    graph = build_multi_section_graph(
        chief_editor, writer, proofreader,
        max_retries=2, on_event=on_event,
    )

    # 后台任务：跑图 + 落库 + 推 DoneEvent + 塞哨兵
    async def run_graph():
        try:
            result = await graph.ainvoke(initial_state)
            await _finalize_all_success(session, resume_id, result)
            for k, sub_state in result.get("sections", {}).items():
                draft = sub_state.get("draft")
                if draft is None:
                    continue
                await queue.put(format_sse(DoneEvent(...)))
        except Exception as error:
            await queue.put(format_sse(ErrorEvent(...)))
        finally:
            await queue.put(SENTINEL)  # 无论成功失败，最后塞哨兵

    task = asyncio.create_task(run_graph())
    try:
        while True:
            item = await queue.get()
            if item is SENTINEL:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
```

机制要点：

1. **asyncio.Queue 当传菜窗口**：节点函数调 `on_event(event)` 把事件塞进 queue，主循环从 queue 拿事件 yield 给前端
2. **SENTINEL = object() 哨兵**：后台任务跑完（成功或失败）最后塞个哨兵，主循环看到哨兵就 break
3. **asyncio.create\_task 跑图**：图在后台 task 里跑，主循环同时从 queue 拿事件——异步并发，不阻塞
4. **finally 块 task.cancel()**：客户端断开连接时主循环异常，finally 取消后台 task 防止泄漏

#### 7.4 流式生成器必须 yield 不能 return

这是个硬约束。`asyncio.create_task(run_graph())` 启动后台任务，主循环 `while True: yield item` 把事件吐给前端。如果错写成 `return item`，函数直接结束——前端只能拿到一个事件，后面的全丢。**流式生成器输出内容必须用 yield**。

#### 7.5 SSE 响应头四件套

```python
@router.post("/regenerate/stream", response_class=StreamingResponse)
async def regenerate_all_contents_stream(...) -> StreamingResponse:
    return StreamingResponse(
        generate_all_contents_streaming(session, resume_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",       # 不缓存事件
            "X-Accel-Buffering": "no",         # Nginx 不缓冲
            "Connection": "keep-alive",        # 长连接
        },
    )
```

四个头缺一不可：

- `media_type="text/event-stream"`：SSE 协议规定的 MIME
- `Cache-Control: no-cache`：浏览器不缓存事件
- `X-Accel-Buffering: no`：Nginx 反代时不缓冲（不然事件攒一批一起发，实时性没了）
- `Connection: keep-alive`：长连接保持

#### 7.6 准备阶段异常必须在后台任务内捕获

```python
try:
    result_q = await session.execute(select(Resume).where(Resume.id == resume_id))
    resume = result_q.scalar_one_or_none()
    if resume is None:
        yield format_sse(ErrorEvent(
            event="error",
            data=ErrorData(message=f"简历不存在: {resume_id}"),
        ))
        return
    # ... 大纲校验
except Exception as error:
    yield format_sse(ErrorEvent(
        event="error",
        data=ErrorData(message=f"准备阶段失败: {error}"),
    ))
    return
```

准备阶段（查简历/大纲/校验状态）的异常**必须**转成 SSE error 事件 yield 给前端，不能 raise——如果 raise，FastAPI 会返回 500 状态码，前端 EventSource 直接报错断流，用户不知道发生了什么。转成 SSE error 事件后，前端 EventSource 收到 `event: error` 数据，能正常展示错误信息。

***

## 4. 核心设计模式（通用经验）

### 4.1 LangGraph 状态图基础

- `StateGraph(state_schema=...)`：用 `state_schema` 参数（不是 `input_schema`，新版 API 改名了）
- `add_node(name, func)`：注册节点，节点函数签名 `(state) -> dict`
- `add_edge(from, to)`：固定边
- `add_conditional_edges(from, router_func, {mapping})`：条件边，router\_func 返回字符串走对应边；返回 `list[Send]` 则并发派发
- `START` / `END`：保留字，图的入口和出口

### 4.2 自定义 reducer（多并发写同字段必备）

当多个并发任务都要写同一个字段（如 5 段并发都写 `sections`），必须用自定义 reducer：

```python
class MultiSectionState(TypedDict):
    sections: Annotated[dict[str, SectionGenState], merge_sections]
```

`Annotated[类型, reducer]` 告诉 LangGraph 字段更新时调 reducer。reducer 必须**返回新对象，不能 in-place 改 left**——LangGraph 内部缓存会保留 left 引用，in-place 改会导致状态不一致。

### 4.3 Send API（并发 MapReduce）

```python
def fan_out(state) -> list[Send]:
    return [Send("target_node", sub_state) for ...]

graph.add_conditional_edges("source_node", fan_out, ["target_node"])
```

- `fan_out` 返回 `list[Send]` 时 LangGraph 并发派发
- 每个 `Send` 带独立的初始 state（实现 MapReduce 的 Map 阶段）
- 第三参数 `["target_node"]` 列出可能的目标节点（框架要预先知道边）

### 4.4 闭包注入 Agent 实例（绕过节点签名限制）

LangGraph 节点签名 `(state) -> dict` 不能传额外参数。要让节点调 Agent：

```python
def make_xxx_node(agent, on_event=None):
    async def xxx_node(state) -> dict:
        result = await agent.generate(state["..."])
        return {...}
    return xxx_node
```

工厂函数 `make_xxx_node` 接收 Agent，内部定义节点函数（闭包捕获 Agent），返回节点函数。**同一个编译图可以复用**——只要 Agent 实例不变，编译一次到处跑。

### 4.5 子图作为主图节点（包装节点模式）

子图直接 `add_node` 当主图节点用时，子图 final state 字段和主图字段对不上会被忽略。**包一层包装节点**：

```python
async def section_subgraph_node(state) -> dict:
    sub_result = await section_subgraph.ainvoke(state)
    return {"sections": {str(sub_result["section_index"]): sub_result}}
```

包装节点调子图，把子图 final state 包装成主图字段格式返回。

### 4.6 异步任务 + 哨兵 + finally 防泄漏

```python
SENTINEL = object()
queue: asyncio.Queue = asyncio.Queue()

async def run_bg():
    try:
        # ... 跑图
    finally:
        await queue.put(SENTINEL)

task = asyncio.create_task(run_bg())
try:
    while True:
        item = await queue.get()
        if item is SENTINEL:
            break
        yield item
finally:
    if not task.done():
        task.cancel()
```

- `asyncio.create_task` 后台跑重活
- 主循环 `while True: yield` 把事件吐给前端
- `SENTINEL = object()` 哨兵标记结束（用 object() 因为它有唯一身份，不会和数据混淆）
- `finally` 块 `task.cancel()` 防止客户端断开时后台任务泄漏

### 4.7 SSE discriminated union（一个函数处理多事件类型）

```python
SSEEvent = Annotated[
    Union[StatusEvent, CheckEvent, ...],
    Field(discriminator="event"),
]

def format_sse(event: SSEEvent) -> str:
    payload = event.model_dump()
    return f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"
```

Pydantic 按 `event` 字段自动分发，一个 `format_sse` 函数处理所有事件类型。

***

## 5. 经验教训（硬约束清单）

### 5.1 字典键拼写一致性

`issues_history`（复数 s）这个键名一旦定下来，**所有地方都必须用复数 s**：

- 子图节点写：`state["issues_history"] + [{...}]`
- reducer 读：合并 5 段 sub\_state 时按这个键取
- 落库存：`content.issues = sub_state.get("issues_history", [])`
- SSE DoneData 字段：`issues_history`
- 前端类型：`issuesHistory`（驼峰）

只要一处写成 `issue_history`（单数），就静默拿不到值，下游默默传错数据——这种 bug 不报错，特别难排查。

### 5.2 LangGraph reducer 必须 shallow copy

```python
# ❌ 错：in-place 改 left
def merge(left, right):
    left.update(right)
    return left

# ✅ 对：返回新对象
def merge(left, right):
    out = {**left}
    out.update(right)
    return out
```

LangGraph 内部缓存保留 left 引用，in-place 改会导致下次 reducer 调用时 left 已被改过，状态不一致。

### 5.3 流式生成器必须 yield 不能 return

```python
# ❌ 错：return 直接结束函数
async def gen():
    return item

# ✅ 对：yield 吐一个事件，函数继续跑
async def gen():
    while True:
        yield item
```

流式端点的核心是持续吐事件给前端，错用 `return` 会直接结束函数，后面的全丢。

### 5.4 SSE 响应头四件套缺一不可

```python
StreamingResponse(
    gen(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    },
)
```

缺 `X-Accel-Buffering: no` 时 Nginx 反代会缓冲事件，攒一批一起发，前端看不到实时进度。

### 5.5 配置文件修改后必须重启后端

`config.py` 改环境变量后，uvicorn 进程不重启不会重新加载——`Settings()` 是进程启动时构造一次的，改了不重启等于没改。开发时改了 config 一定要重启 dev server。

### 5.6 子图作为节点需要包装节点转换返回值

子图 final state 字段（`draft` / `attempts` / `issues` ...）和主图字段（`sections`）对不上时，LangGraph 默默忽略，主图 state 不更新。必须包一层包装节点，把子图 final state 转成主图字段格式。

### 5.7 LangGraph StateGraph 用 state\_schema 不用 input\_schema

新版 LangGraph 的 `StateGraph` 初始化用 `state_schema` 参数（不是 `input_schema`）。`input_schema` 是老版 API，新版已改名，用旧名会报错或行为异常。

### 5.8 准备阶段异常必须转 SSE error 事件

流式端点的准备阶段（查简历/大纲/校验状态）异常**必须** `yield format_sse(ErrorEvent(...))` 然后 `return`，不能 `raise`。raise 会让 FastAPI 返回 500 状态码，前端 EventSource 直接断流报错，用户看不到具体错误信息。

### 5.9 环境变量必须在入口显式 load\_dotenv()

Pydantic Settings 的 `env_file` 参数只把 `.env` 变量加载到 Settings 对象字段，**不会导出到** **`os.environ`**。依赖 `os.environ` 的第三方库（如 LangChain/LangSmith）拿不到值。必须在项目入口处显式调用 `load_dotenv()` 确保变量导出。

### 5.10 模块导入完整性

新增模块（如 `retriever.py`）时容易漏写 `import json` 之类的标准库导入——导入时不报错，运行时 `NameError`。每个新模块写完先跑一次 `python -c "from app.llm.retriever import retrieve_samples"` 验证导入完整性。

### 5.11 缩进错误导致异步任务无法启动

```python
# ❌ 错：task 写在 finally 块内，正常路径不启动
try:
    ...
finally:
    task = asyncio.create_task(run_graph())  # 缩进错了
```

`asyncio.create_task(...)` 的缩进必须确保在主流程路径上，不能缩进到 `finally` 块或 `if` 分支里，否则异步任务不启动，前端一直拿不到事件。

### 5.12 函数参数拼写错误运行时才暴露

```python
# ❌ 错：on_envent（多 n）
async def setup(on_envent=None):
    on_envent(...)  # 运行时 NameError

# ✅ 对：on_event
async def setup(on_event=None):
    on_event(...)
```

回调函数参数拼写错误（如 `on_envent` vs `on_event`）在导入时不报错（Python 不校验参数名），运行时调到才会 `NameError`。写完先跑一遍基本调用验证。

***

## 6. 最终产物

### 6.1 一条主线

清理旧代码后，内容生成只剩一条主线：

```
POST /api/v1/resumes/{resume_id}/contents/regenerate/stream
  → generate_all_contents_streaming(session, resume_id)  # workflow 层
    → build_multi_section_graph(chief, writer, proof, on_event)  # 图
      → chief_editor → fan_out(5 Send) → section_subgraph(×5) → cross_check → all_done
    → _finalize_all_success(session, resume_id, result)  # 落库
  → StreamingResponse(yield SSE 字符串)
```

旧入口已删：

- `POST /contents/generate`（旧批量同步）
- `POST /contents/{idx}/regenerate`（旧单段同步）
- `POST /contents/{idx}/regenerate/stream`（旧单段流式）
- `generate_all_contents_task`（ARQ worker 后台任务）
- `generate_content` / `generate_content_streaming`（workflow 层旧函数）
- `build_content_graph`（旧单段图）
- `DeepSeekContentGenerator.generate()`（父类旧 generate 方法）
- `quality_check.py` 的 `check_content`（旧纯代码校验）

### 6.2 文件清单

| 文件                                       | 职责                                                                                                                                      |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/app/llm/multi_section_state.py` | 状态 schema（`MultiSectionState` / `SectionGenState`）+ `merge_sections` reducer                                                            |
| `backend/app/llm/graph.py`               | 图工厂（`build_section_subgraph` / `build_multi_section_graph` / `make_chief_editor_node` / `make_cross_check_node` / `make_all_done_node`） |
| `backend/app/llm/deepseek.py`            | 三个 Agent（`ChiefEditorAgent` / `DeepSeekWriterAgent` / `DeepSeekProofReaderAgent`）+ 父类 `DeepSeekContentGenerator`                        |
| `backend/app/workflows/content.py`       | 唯一入口 `generate_all_contents_streaming` + 落库 `_finalize_all_success`                                                                     |
| `backend/app/schemas/sse_event.py`       | SSE 事件 schema（discriminated union）+ `format_sse`                                                                                        |
| `backend/app/api/v1/contents.py`         | 端点（`POST /regenerate/stream` + `GET /` + `PATCH /{idx}`）                                                                                |
| `backend/app/llm/retriever.py`           | RAG 检索 + asyncpg 连接池 + `close_pool`                                                                                                     |

### 6.3 三个 Agent 类的继承关系

```
DeepSeekContentGenerator（父类：共用方法 _user_prompt / _example_for / _validate_draft）
  └─ DeepSeekWriterAgent（子类：重写 generate(payload, plan) + _system_prompt(payload, plan)）

ChiefEditorAgent（独立类：调度角色，不继承 DeepSeekContentGenerator）
DeepSeekProofReaderAgent（独立类：反馈角色，不继承）
```

`DeepSeekContentGenerator` 清理后只保留共用方法，不再有 `generate()`——子类 `DeepSeekWriterAgent` 接管生成逻辑，多接 `plan` 参数。

### 6.4 后端验证

- 模块导入验证：`from app.main import app` 通过
- 前端 `tsc --noEmit` 通过（虽然这是前端，但证明 SSE 事件 schema 对得上）
- 前端 `vite build` 通过（2027 模块转译，6.21s）
- 端到端：找一份 `outline_ready` 的简历，点"一键重新生成全部"按钮，5 段 SSE 进度条正常推进 + 内容落库

***

## 复盘总结

C+G 阶段从 V1 的"单段串行 + ARQ 后台 + 前端轮询"升级到"多智能体并发 + LangGraph 编排 + SSE 流式推送"。

核心收益：

1. **耗时**：5 段并发，等于最慢那段（\~30 秒），不是 5 段累加（\~2.5 分钟）
2. **质量**：主编协调 + writer/proofreader 自纠环 + 跨段校验，输出更稳
3. **可观测性**：前端实时看到每段每个事件（开始/校对/重试/完成），不再转圈圈

核心经验：

1. **LangGraph 多并发写同字段必须用自定义 reducer**，且 reducer 不能 in-place 改
2. **节点签名限制用闭包绕过**，工厂函数注入 Agent 实例
3. **子图当主图节点用要包装一层**，把子图 final state 转成主图字段格式
4. **流式生成器必须 yield 不能 return**，配合 asyncio.Queue + SENTINEL 哨兵
5. **SSE 响应头四件套缺一不可**，特别是 `X-Accel-Buffering: no` 防 Nginx 缓冲
6. **字典键拼写一致性**（如 `issues_history` 复数 s）一旦定下来全项目都要遵守，否则静默 bug

