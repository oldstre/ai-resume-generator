# 对话式编辑扩展复盘

> **复盘范围**:对话式编辑扩展(后端 8 Step + 前端集成)
> **复盘时间**:2026-09-28
> **复盘目的**:理清"用户自然语言改简历"方案的设计思路、踩过的坑、最终产物,方便后续维护时快速回忆。

***

## 目录

- [1. 背景:为什么要加对话式编辑](#1-背景为什么要加对话式编辑)
- [2. 架构总览](#2-架构总览)
- [3. 分阶段复盘](#3-分阶段复盘)
  - [Step 1:ChatState(图状态)](#step-1chatstate图状态)
  - [Step 2:chat_tools.py(三个工具)](#step-2chat_toolspy三个工具)
  - [Step 3:chat_graph.py(ReAct 图)](#step-3chat_graphpyreact-图)
  - [Step 4:checkpoint.py(AsyncPostgresSaver 单例)](#step-4checkpointpyasyncpostgressaver-单例)
  - [Step 5:workflows/chat.py(run_chat 函数)](#step-5workflowschatpyrun_chat-函数)
  - [Step 6:api/v1/chat.py + schemas/chat.py(端点)](#step-6apiv1chatpy--schemaschatpy端点)
  - [Step 7:路由注册 + lifespan 接 checkpoint](#step-7路由注册--lifespan-接-checkpoint)
  - [Step 8:端到端验证](#step-8端到端验证)
  - [前端集成:ChatPanel 组件](#前端集成chatpanel-组件)
- [4. 核心设计模式](#4-核心设计模式)
- [5. 经验教训(硬约束清单)](#5-经验教训硬约束清单)
- [6. 最终产物](#6-最终产物)

***

## 1. 背景:为什么要加对话式编辑

### 1.1 旧方案有什么痛点

C+G 阶段完成后,简历内容生成已经跑通多智能体并发 + SSE 流式推送。但用户改简历还是两种路径:

1. **整份重新生成**:点"一键重新生成全部"按钮,5 段全部重跑——慢,且会覆盖用户已经满意的部分
2. **手动编辑单段**:点"编辑"按钮,弹窗里直接改 JSON——技术用户能玩,普通用户看到 `{"items":[{"skill":"Python"...}]}` 直接劝退

四个核心痛点:

1. **改一段要重跑全部**:用户只想精简第 2 段,但只能"一键重新生成全部",5 段都重写,违背"只改用户要求的部分"
2. **手动编辑门槛高**:JSON 编辑需要懂结构,普通用户不会
3. **没有上下文记忆**:用户改完第 2 段再问"刚改了啥",系统不知道——每次请求都是无状态的
4. **改完不连贯**:用户分多次改,各段之间可能矛盾(技能段加了 Go 但项目段没体现),没有跨段校验

### 1.2 对话式编辑是什么

让用户像跟 HR 助理聊天一样改简历:

```
用户:把第 2 段精简一下
Agent:(调 get_section 看原内容)→(调 update_section 写库)→ 已改
用户:工作经历里加上 XX 公司
Agent:(调 get_section 看工作经历段)→(调 update_section 加一条)→ 已加
用户:刚改的是哪段?
Agent:刚改了第 2 段(专业技能)和第 4 段(工作经历)
```

### 1.3 设计目标

| 维度 | 旧方案 | 对话式编辑 |
|---|---|---|
| 改一段 | 重跑 5 段(\~30 秒) | Agent 调工具改 1 段(\~5 秒) |
| 用户门槛 | JSON 编辑 | 自然语言对话 |
| 上下文 | 无状态 | LangGraph Checkpointer 按 thread_id 持久化 |
| 跨段校验 | 重新生成时跑 | Agent 自主决定要不要调(本次最小 3 工具,未加) |

***

## 2. 架构总览

### 2.1 三层结构

```
HTTP 层
─────────────────────────────────────────────────────────
POST /api/v1/resumes/{id}/chat
  body: {message, thread_id?}
  → run_chat(resume_id, message, thread_id)
─────────────────────────────────────────────────────────

工作流层(workflows/chat.py)
─────────────────────────────────────────────────────────
1. 查简历全文(基本信息 + 所有段内容)
2. 拼 system prompt(简历全文 + 工具使用规则)
3. 决定 thread_id:
   - 用户没传 → uuid4 生成
   - 用户传了 → 防御性 aget_state 检查,空就降级为新会话
4. 构造 input_messages:
   - 新会话:[SystemMessage, HumanMessage]
   - 继续会话:[HumanMessage]  ← system 已被 checkpointer 记住
5. graph.ainvoke({"messages": input_messages}, config)
6. 拿最后一条 AIMessage 的 content 返回
─────────────────────────────────────────────────────────

图层(chat_graph.py)
─────────────────────────────────────────────────────────
START → agent_node ──(无 tool_calls)──→ END
            │ ↑
            │ │(循环)
            ↓ │
          tool_node(ToolNode 预置)
─────────────────────────────────────────────────────────

工具层(chat_tools.py)
─────────────────────────────────────────────────────────
list_sections(resume_id)              → 概览
get_section(resume_id, section_index) → 读一段
update_section(resume_id, idx, json)  → 改一段(revision + 1)
─────────────────────────────────────────────────────────

持久化层(checkpoint.py)
─────────────────────────────────────────────────────────
AsyncPostgresSaver + AsyncConnectionPool
- 单例 get_checkpointer()
- 启动 setup_checkpointer():pool.open() + saver.setup() 建表
- 退出 close_checkpointer():关 pool
- kwargs={autocommit, prepare_threshold, row_factory}
─────────────────────────────────────────────────────────
```

### 2.2 关键决策表

| 决策点 | 选择 | 理由 |
|---|---|---|
| 持久化 | AsyncPostgresSaver(一步到位) | 不用先 MemorySaver 再迁移,生产场景必须落库 |
| 流式 | 先非流式跑通 | 端到端验证逻辑,流式是后续优化 |
| system prompt | 注入简历全文 | LLM 不调工具也能回答"简历现在咋样",减少工具调用 |
| 工具数 | 最小 3 个 | list/get/update 覆盖核心场景,create/delete 暂不需要 |
| thread_id 生成 | 后端生成 UUID | 前端不可信,后端是 ID 的权威来源 |
| system 注入时机 | 仅首次注入 | add_messages 是追加,反复注入会污染历史 |

***

## 3. 分阶段复盘

### Step 1:ChatState(图状态)

**文件**:`backend/app/llm/chat_state.py`

**设计动机**:LangGraph StateGraph 需要一个 TypedDict 定义状态。`MessagesState` 预置了 `messages` 字段 + `add_messages` reducer,看似直接继承就行。

**关键代码**:

```python
class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

**踩坑点**:最初写成 `class ChatState(MessagesState): pass`,依赖 TypedDict 继承。理论上 `Annotated[..., add_messages]` 元数据应该传给子类,但实际跑起来 LangGraph 拿不到 reducer,input 的 messages 不合并进 state,导致 `state["messages"]` 是空 list,LLM 报 "Empty input messages"。

**修复**:显式重新声明 `messages: Annotated[list[AnyMessage], add_messages]`,不靠继承。详见[硬约束 1](#硬约束-1)。

**教训**:TypedDict 继承在不同 Python 版本下行为有差异,LangGraph 关键字段必须显式声明,不能用 `pass` 偷懒。

***

### Step 2:chat_tools.py(三个工具)

**文件**:`backend/app/llm/chat_tools.py`

**设计动机**:Agent 要改真实 DB,通过工具调用而非自己写 SQL。三个工具覆盖简历编辑核心场景:

- `list_sections` —— 看全貌(概览)
- `get_section` —— 看细节(读一段)
- `update_section` —— 改内容(写一段)

**关键设计**:

1. **工具内拿 session 用 `async_session_factory`**,不用 FastAPI 的 `Depends(get_session)`——LangGraph 工具没有依赖注入机制,且工作流可能在不同上下文调用
2. **`update_section` 接 `str` 不接 `dict`**:LLM 产出的 JSON 可能不合法,接 str 让工具内部 `json.loads` + 清晰错误返回,LLM 能看到错误重试
3. **`revision += 1` 与手动编辑端点保持一致**:乐观锁字段统一管理

**踩坑点**:

- IDE 自动导入错(导了 `from app.workflows import content` 这种无关 import),要手动删
- 拼写错误(`async_session` 漏 factory),import 不报错但运行时 AttributeError

**教训**:工具文件 import 完后跑一次 `uv run python -c "from app.llm.chat_tools import list_sections"` 验证完整性。

***

### Step 3:chat_graph.py(ReAct 图)

**文件**:`backend/app/llm/chat_graph.py`

**设计动机**:ReAct(Reasoning + Acting)是 LLM 自主决策调工具的经典模式。图结构简单清晰:

```
START → agent ──(无 tool_calls)──→ END
         │ ↑
         │ │
         ↓ │
       tools
```

**关键设计**:

1. **`build_chat_graph(checkpointer)` 接收 checkpointer 参数**:图工厂只管组装,不管持久化——职责分离。生产传 AsyncPostgresSaver,测试可传 MemorySaver
2. **`ToolNode(_TOOLS)` 用 LangGraph 预置**:不用手写工具调用循环,自动读最后一条 AIMessage 的 tool_calls 逐个执行
3. **`should_continue` 条件边**:看上条 AIMessage 有没有 tool_calls,有去 tools,无去 END
4. **路径映射 `{"tools":"tools", END:END}`**:should_continue 返回值 → 目标节点名的映射,清晰可读

**踩坑点**:路径映射一开始写成 `{"tools","tools"}`(逗号),应该是冒号 `{"tools":"tools"}`(键值对)。Python 集合 vs 字典的语法混淆。

**教训**:LangGraph 图结构代码量小但语法密集,写完先 `uv run python -c "from app.llm.chat_graph import build_chat_graph"` 验证语法。

***

### Step 4:checkpoint.py(AsyncPostgresSaver 单例)

**文件**:`backend/app/core/checkpoint.py`

**设计动机**:对话历史要跨请求持久化,LangGraph Checkpointer 是标准方案。生产用 Postgres 落库,不用 MemorySaver(重启就丢)。

**关键设计**:

1. **三函数生命周期**:借鉴 `core/redis.py` 模式
   - `get_checkpointer()` —— 同步拿单例(构造对象不连库)
   - `setup_checkpointer()` —— async,启动时调一次:`pool.open()` + `saver.setup()` 建表
   - `close_checkpointer()` —— async,退出时关 pool

2. **`AsyncConnectionPool(open=False)`**:同步构造对象,延迟到 async 上下文才真连库。psycopg3 的 pool 默认 `open=True` 会在构造时启动后台协程连库,但模块导入是同步上下文,会报 "no running event loop"

3. **URL 转换**:`postgresql+asyncpg://...` → `postgresql://...`,psycopg3 不认 SQLAlchemy 的 `+driver` 后缀

**踩坑点 1:`connection_kwargs` 不存在**

最初写成:

```python
_pool = AsyncConnectionPool(
    conninfo=psycopg_url,
    open=False,
    connection_kwargs={...},  # ❌ TypeError
)
```

报错:`AsyncConnectionPool.__init__() got an unexpected keyword argument 'connection_kwargs'`。

读 `psycopg_pool/pool_async.py` 源码才发现正确参数名是 `kwargs`,内部第 701 行 `await self.connection_class.connect(conninfo, **kwargs)` 透传给 `connect()`。

**修复**:`kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}`。

**踩坑点 2:`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`**

修了参数名后,启动报这个错。

根因:`AsyncConnectionPool` 默认 `autocommit=False`,每条 SQL 包在隐式事务里。但 LangGraph saver 的 migration 包含 `CREATE INDEX CONCURRENTLY`,这是 Postgres 特殊语法,**不能在事务块里执行**(它自己内部需要多事务协作)。

对比 `from_conn_string` 源码:`AsyncConnection.connect(conn_string, autocommit=True, prepare_threshold=0, row_factory=dict_row)` 显式开了 autocommit。我们用 pool 没传,默认 False,所以踩坑。

**修复**:三个参数全部对齐 `from_conn_string` 的默认行为:

```python
kwargs={
    "autocommit": True,        # 关键:CREATE INDEX CONCURRENTLY 要求 autocommit
    "prepare_threshold": 0,    # 防 prepared statement 缓存并发坑
    "row_factory": dict_row,   # saver 内部按 dict 访问 row["v"]
}
```

**教训**:

- LangGraph 官方 `from_conn_string` 是 async context manager,适合临时用;长生命周期单例要手动构造 pool + saver
- 看 `from_conn_string` 源码里 `connect()` 的关键参数,用 pool 时要对齐
- psycopg3 的 autocommit 默认 False,跟同步 psycopg2 行为不一致

***

### Step 5:workflows/chat.py(run_chat 函数)

**文件**:`backend/app/workflows/chat.py`

**设计动机**:把"查简历 → 拼 system → 调图 → 拿回复"封装成 `run_chat(resume_id, message, thread_id)`,端点层只管 HTTP,工作流层管业务。

**关键设计**:

1. **system prompt 注入策略**:
   - 新会话:查简历全文 → 拼 system → 注入 `[SystemMessage, HumanMessage]`
   - 继续会话:只传 `[HumanMessage]`,system 已被 checkpointer 记住

2. **防御性 thread_id 检查**:
   ```python
   if not is_new_thread:
       snapshot = await graph.aget_state(config)
       if not snapshot.values:
           is_new_thread = True  # 降级为新会话
   ```
   防用户瞎传 thread_id 导致 Agent 拿不到 system prompt。

3. **`_load_resume_snapshot` 用 `async_session_factory`**:工作流不在请求作用域,不能依赖 FastAPI 依赖注入

4. **拿回复用 `reversed + isinstance(AIMessage)`**:ReAct 图末尾理论上是 AIMessage,但用 `[-1]` 不稳;`reversed + isinstance` 找最后一条 AI 消息,工程上更安全

**踩坑点**:

- 一开始把 `graph.ainvoke({"messages": ...})` 敲成 `{"message": ...}`(少个 s),LangGraph 静默丢弃 input 字段(不在 schema 里),state["messages"] 一直空,LLM 报 "Empty input messages"。**这是最隐蔽的 bug**,详见[硬约束 4](#硬约束-4)

**教训**:

- system prompt 不能每轮都注入:`add_messages` 是追加,反复注入会让历史里堆 N 个 system
- LangGraph input dict 的 key 必须严格匹配 state schema 字段名,拼写错误会被静默丢弃

***

### Step 6:api/v1/chat.py + schemas/chat.py(端点)

**文件**:`backend/app/api/v1/chat.py` + `backend/app/schemas/chat.py`

**设计动机**:HTTP 层尽量薄,只管请求/响应序列化和错误码转换。

**关键设计**:

1. **`_get_resume_or_404` 复制不导入**:跟 `contents.py` 里同名 helper 一模一样,但跨路由文件互相 import 是反模式。先复制,等项目大了再抽到 `api/v1/_helpers.py`
2. **`response_model=ChatResponse` + `ChatResponse(**result)` 双层保险**:
   - 第一层(手写):dict 解包成 Pydantic 实例,显式失败容易调试
   - 第二层(FastAPI):自动校验/过滤,挡住端点 return 错类型
3. **异常映射**:`run_chat` 内部抛 `ValueError` 表示"简历不存在",端点层 catch 转 404

**踩坑点**:无,这一步相对顺畅。

**教训**:schema 独立成文件,跟现有 `schemas/resume_content.py` 风格保持一致,方便后续扩展(比如加流式响应 schema)。

***

### Step 7:路由注册 + lifespan 接 checkpoint

**文件**:`backend/app/api/v1/__init__.py` + `backend/app/main.py`

**设计动机**:把 chat router 挂到 API 树,把 checkpoint 生命周期接到 FastAPI lifespan。

**关键设计**:

1. **路由 prefix `/resumes/{resume_id}/chat`**:和 `contents.py` 的 `/resumes/{resume_id}/contents` 平级,互不冲突,FastAPI 用 Radix Tree 精确匹配
2. **lifespan 启动顺序**:`get_redis()`(同步)在前,`setup_checkpointer()`(异步)在后——先做轻量同步初始化,再做重的异步连库
3. **lifespan 关闭顺序**:checkpoint 放最后,与启动顺序镜像对照

**踩坑点**:`await setup_checkpointer()` 不能漏 await,漏了 Python 不会报错,只发 RuntimeWarning,但服务起来后第一次调 chat 端点就会因为池没开炸掉——隐蔽 bug。

**教训**:

- lifespan 改完必须重启后端,uvicorn reload 不会自动重跑 lifespan
- 配置/连接池改动同样要重启
- Async 函数调用必须显式 await,不要指望 Python 报错

***

### Step 8:端到端验证

**测试设计**:4 个场景验证 4 个核心机制

| 测试 | 验证机制 | 预期 | 实际 |
|---|---|---|---|
| 1 | system prompt 注入 | Agent 不调工具,直接基于 system 回答段数 | ✅ 列出 5 段标题 |
| 2 | 工具调用链路 | Agent 调 get_section + update_section,DB revision +1 | ✅ DB 真改了 |
| 3 | checkpointer 持久化 | Agent 知道"刚改了哪段" | ✅ 准确说出 revision 5→6 |
| 反例 | 防御性 thread_id 检查 | 瞎编 thread_id 降级为新会话 | ✅ Agent 给概览不假装记得 |

**最关键证据**:测试 3 里 Agent 说"revision 从 5 升到 6"——这个数字只在测试 2 的 `update_section` ToolMessage 返回值里出现过,system prompt 里没有。Agent 能说出来,证明 checkpointer 真的把测试 2 的完整对话历史(含 ToolMessage)恢复出来了。

**踩坑点**:见 Step 1(继承 reducer 丢失)和 Step 5(message 拼写错误),都在端到端测试时才暴露。

**教训**:端到端测试是发现隐蔽 bug 的最后一道防线,不能省。每个 Step 单独跑 import 检查只能抓语法错误,抓不到运行时行为问题。

***

### 前端集成:ChatPanel 组件

**文件**:`frontend/src/features/resumes/ChatPanel.tsx` + `api.ts` + `types.ts` + `ResumeDetailPage.tsx`

**设计动机**:让用户能用 UI 跟 Agent 对话,而不是只能 curl。

**关键设计**:

1. **`useChat(id)` 是无状态 mutation**:每轮独立请求,对话历史和 thread_id 由组件层用 `useState` 维护。hook 职责单一,组件层清楚知道"我的对话我负责"

2. **乐观追加 user 消息**:点击发送立即把 user 气泡加进 `messages`,不等后端响应,体验流畅

3. **失败也用助手气泡显示**:不破坏对话流,用户能看到错误原因(后端 404/500 等)

4. **`onSuccess` invalidate contents 缓存**:Agent 改了 DB 后,前端自动重拉 contents,`ContentCard` 显示新内容,不需要用户手动刷新

5. **换简历清空对话**:`useEffect([id])` 监听 id 变化,清空 messages 和 threadId

6. **Enter 发送 / Shift+Enter 换行**:符合常见聊天 UX 惯例

**踩坑点**:无,前端集成相对顺畅,tsc + vite build 一次通过。

**教训**:后端契约清晰(请求/响应 schema 明确),前端集成就简单——先把后端跑通再做前端,事半功倍。

***

## 4. 核心设计模式

### 4.1 Checkpointer 单例 + 三函数生命周期

借鉴 `core/redis.py` 模式,但有关键差异:Redis 单例同步创建即可,Postgres saver 需要 async setup。所以拆成三块:

| 函数 | 同步/异步 | 职责 | 调用时机 |
|---|---|---|---|
| `get_checkpointer()` | 同步 | 拿单例(构造对象不连库) | 任何地方 |
| `setup_checkpointer()` | 异步 | open pool + 建表 | lifespan startup |
| `close_checkpointer()` | 异步 | 关 pool | lifespan shutdown |

### 4.2 system prompt 单次注入 + checkpointer 记忆

无状态 HTTP 下维持会话连续性的标准模式:

- **后端生成 ID**(因为后端才知道 ID 是否唯一、合规)
- **前端记住 ID**(因为前端是会话发起方,知道用户在跟哪份简历聊)
- **每次请求回传 ID**,后端按 ID 从 checkpointer 取历史

system prompt 只在首次注入(新会话),后续轮靠 checkpointer 恢复历史。如果每轮都注入,`add_messages` reducer 会把 system 追加 N 次,污染上下文。

### 4.3 防御性 thread_id 检查

```python
if not is_new_thread:
    snapshot = await graph.aget_state(config)
    if not snapshot.values:
        is_new_thread = True  # 降级
```

防"用户传瞎编的 thread_id"。如果不防,Agent 会拿到空 history + 只有一条 HumanMessage,完全不知道在改哪份简历——瞎改一通。

### 4.4 ReAct 图 + ToolNode 预置

不用手写工具调用循环,LangGraph `ToolNode` 自动读最后一条 AIMessage 的 tool_calls 逐个执行,把结果包成 ToolMessage 返回。图结构代码量小,可读性高。

### 4.5 工具内用 async_session_factory

LangGraph 工具没有依赖注入机制,且工作流可能在不同上下文调用(端点、后台任务、测试)。统一用全局 `async_session_factory` 自己开 session,不依赖 FastAPI 请求作用域。

### 4.6 端点层双层 Pydantic 校验

```python
@router.post("", response_model=ChatResponse)  # 第二层:FastAPI 自动校验
async def chat_with_resume(...) -> ChatResponse:
    result = await run_chat(...)  # dict
    return ChatResponse(**result)  # 第一层:手动解包
```

两层职责不同:第一层显式失败容易调试,第二层兜底挡住端点 return 错类型。

***

## 5. 经验教训(硬约束清单)

### 硬约束 1

**LangGraph State 子类必须显式重新声明带 reducer 的字段**。

```python
# ❌ 错:依赖继承,Annotated 元数据可能丢失
class ChatState(MessagesState):
    pass

# ✅ 对:显式声明
class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

TypedDict 继承时 `Annotated[..., reducer]` 元数据在不同 Python 版本下可能丢失,导致 LangGraph 拿不到 reducer,input 不合并进 state,节点拿到的 `state["messages"]` 是空 list。Debug 时第一时间打印 `state.keys()` 和 `len(state["messages"])` 确认。

### 硬约束 2

**`AsyncConnectionPool` 的连接参数透传用 `kwargs=`,不是 `connection_kwargs=`**。

```python
# ❌ 错
_pool = AsyncConnectionPool(conninfo=..., connection_kwargs={...})

# ✅ 对
_pool = AsyncConnectionPool(conninfo=..., kwargs={...})
```

读 `psycopg_pool/pool_async.py` 源码第 49-70 行签名,内部第 701 行 `await self.connection_class.connect(conninfo, **kwargs)` 透传给 `connect()`。

### 硬约束 3

**`AsyncPostgresSaver` 的连接池必须 `autocommit=True`**。

```python
_pool = AsyncConnectionPool(
    conninfo=psycopg_url,
    open=False,
    kwargs={
        "autocommit": True,        # 关键:CREATE INDEX CONCURRENTLY 要求
        "prepare_threshold": 0,    # 防 prepared statement 并发坑
        "row_factory": dict_row,   # saver 内部按 dict 访问
    },
)
```

saver 的 migration 包含 `CREATE INDEX CONCURRENTLY`,这是 Postgres 特殊语法,不能在事务块里执行。`AsyncConnectionPool` 默认 `autocommit=False` 会包隐式事务,导致 migration 失败。三个参数必须对齐 `from_conn_string` 的默认行为。

### 硬约束 4

**LangGraph 图 ainvoke 时,input dict 的 key 必须严格匹配 state schema 字段名(包括复数 s)**。

```python
# ❌ 错:拼写错误,LangGraph 静默丢弃
result = await graph.ainvoke({"message": input_messages}, config=config)

# ✅ 对:严格匹配 ChatState.messages 字段名
result = await graph.ainvoke({"messages": input_messages}, config=config)
```

拼写错误不会报错,会被静默丢弃,导致节点拿到的 state 字段是空的,引发 "Empty input messages" 等远端症状。这与之前 `issues_history`(复数 s)的硬约束同源——字典键拼写必须严格一致。

### 硬约束 5

**system prompt 只在首次注入,后续轮靠 checkpointer 恢复历史**。

```python
if is_new_thread:
    input_messages = [SystemMessage(...), HumanMessage(...)]
else:
    input_messages = [HumanMessage(...)]  # system 已被 checkpointer 记住
```

`add_messages` reducer 是追加,反复注入 SystemMessage 会让历史里堆 N 个 system,token 爆炸且 LLM 困惑。

### 硬约束 6

**Async 函数调用必须显式 `await`**。

```python
# ❌ 错:漏 await,只拿到协程对象
setup_checkpointer()

# ✅ 对
await setup_checkpointer()
```

Python 不会报错(只发 RuntimeWarning),但连接池不会真开,服务起来后第一次调端点就会炸。lifespan 里的 async 函数调用尤其要警惕。

### 硬约束 7

**thread_id 必须由后端生成,前端只转发**。

```python
# 后端 run_chat
if thread_id is None:
    thread_id = str(uuid.uuid4())  # 后端生成
```

前端不可信,可能传瞎编的、冲突的、超长的 ID。后端生成则完全可控。首次不传(`null`),后续原样回传——后端是 ID 的权威来源。

### 硬约束 8

**`build_xxx_graph(checkpointer)` 接收 checkpointer 参数,图工厂只管组装不管持久化**。

```python
def build_chat_graph(checkpointer):
    # ... 组装节点和边
    return graph.compile(checkpointer=checkpointer)
```

职责分离:生产传 `AsyncPostgresSaver`,测试传 `MemorySaver`,图工厂不关心具体类型。

### 硬约束 9

**端点层用 `_get_resume_or_404` 复制不导入**。

跨路由文件互相 import 是反模式。先复制(`contents.py` 和 `chat.py` 各有一份),等项目大了再抽到 `api/v1/_helpers.py`。

### 硬约束 10

**端点层异常映射:workflow 抛 ValueError → 端点 catch 转 404**。

```python
try:
    result = await run_chat(...)
except ValueError as e:
    raise HTTPException(status_code=404, detail=str(e))
```

让前端拿到的是干净的 HTTP 错误码,而不是 500。虽然 `_get_resume_or_404` 已经挡了一道,但 workflow 内部 `_load_resume_snapshot` 也会再查一次(端点和 workflow 的 session 不是同一个,中间可能有时间差)。防御性 catch 应对极端竞态。

***

## 6. 最终产物

### 6.1 后端文件清单

| 文件 | 行数 | 职责 |
|---|---|---|
| `backend/app/llm/chat_state.py` | ~20 | ChatState TypedDict |
| `backend/app/llm/chat_tools.py` | ~120 | 3 个工具(list/get/update_section) |
| `backend/app/llm/chat_graph.py` | ~80 | ReAct 图工厂 |
| `backend/app/core/checkpoint.py` | ~70 | AsyncPostgresSaver 单例 |
| `backend/app/workflows/chat.py` | ~140 | run_chat 工作流 |
| `backend/app/schemas/chat.py` | ~20 | ChatRequest / ChatResponse |
| `backend/app/api/v1/chat.py` | ~70 | POST /chat 端点 |
| `backend/app/api/v1/__init__.py` | +2 | 注册 chat router |
| `backend/app/main.py` | +4 | lifespan 接 checkpoint setup/close |

### 6.2 前端文件清单

| 文件 | 行数 | 职责 |
|---|---|---|
| `frontend/src/features/resumes/types.ts` | +40 | ChatRequest / ChatResponse / ChatMessage |
| `frontend/src/features/resumes/api.ts` | +35 | useChat mutation hook |
| `frontend/src/features/resumes/ChatPanel.tsx` | ~140 | 对话面板组件 |
| `frontend/src/pages/ResumeDetailPage.tsx` | +6 | 集成 ChatPanel |

### 6.3 Agent 继承关系

```
create_chat_model()  ← ChatOpenAI(DeepSeek 兼容)
       ↓
model.bind_tools(_TOOLS)  ← 让 LLM "看到" 工具箱
       ↓
agent_node(ainvoke)  ← ReAct 决策节点
```

注:对话式编辑的 Agent 不继承 `DeepSeekContentGenerator`(那是结构化输出版),直接用底层 `model.bind_tools()`。两条链路独立,职责不同。

### 6.4 验证状态

- ✅ 后端 import 检查全部通过
- ✅ 后端 lifespan 启动通过(checkpointer 建表成功)
- ✅ 端到端 4 个场景全部通过(测试 1/2/3 + 反例)
- ✅ 前端 tsc + vite build 通过
- ✅ 前端 UI 集成完成(对话式编辑卡片在简历详情页底部)

### 6.5 数据库新增表

LangGraph Checkpointer 自动建表(在 `resume` 数据库里):

- `checkpoints` —— 会话状态快照
- `writes` —— 节点写入记录
- `checkpoint_migrations` —— migration 版本表

这些表跟项目原有业务表(`resumes` / `resume_contents` 等)完全独立,不互相影响。

### 6.6 流程图

```
用户在 ChatPanel 输入"把第2段精简"
       ↓
前端 POST /api/v1/resumes/{id}/chat
  body: {message: "...", thread_id: "abc-123"}
       ↓
api/v1/chat.py: chat_with_resume
  - _get_resume_or_404 校验简历存在
  - 调 run_chat(resume_id, message, thread_id)
       ↓
workflows/chat.py: run_chat
  - 决定 thread_id(传了用传的,没传生成新的)
  - 防御性 aget_state 检查 thread 是否有历史
  - 新会话:查简历全文 → 拼 system → [SystemMessage, HumanMessage]
  - 继续会话:[HumanMessage](system 已被 checkpointer 记住)
  - graph.ainvoke({"messages": input}, config)
       ↓
chat_graph.py: ReAct 图
  agent_node: model_with_tools.ainvoke(state["messages"])
    ↓
    LLM 决定调 get_section(2) → ToolNode 执行 → 返回原内容
    ↓
  agent_node 再次决策:基于原内容,决定调 update_section(2, new_json)
    ↓
    ToolNode 执行 → 改 DB(revision + 1)→ 返回"已改"
    ↓
  agent_node 第三次决策:无 tool_calls → END
       ↓
run_chat 拿最后一条 AIMessage content 返回
       ↓
api/v1/chat.py: ChatResponse(**result) → JSON 响应
       ↓
前端 useChat onSuccess:
  - setThreadId(data.thread_id)
  - 追加助手气泡到 messages
  - invalidateQueries(contentsKey) → ContentCard 自动刷新
       ↓
用户看到 Agent 回复 + 正文区第 2 段同步更新
```

***

## 7. 后续可扩展方向

记录但不在本次实现:

1. **流式响应**:目前非流式,LLM 思考时用户看不到进度。可改 SSE 推流,前端流式渲染助手气泡
2. **更多工具**:`create_section`(加新段)、`delete_section`(删段)、`reorder_sections`(调段顺序)、`cross_check`(主动触发跨段校验)
3. **会话历史持久化到前端**:`sessionStorage` 存 thread_id 和 messages,刷新页面不丢
4. **多简历会话切换**:用户在多份简历间切换时,各简历的对话历史独立维护
5. **工具调用过程可视化**:在助手气泡里显示 Agent 调了哪些工具(类似 ChatGPT 的"搜索中""分析中"提示)
6. **失败重试 + 错误恢复**:Agent 调 update_section 失败时(如 JSON 结构错),自动重试或问用户澄清
