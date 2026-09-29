# Agent 测试与生产化

## Context

项目已完成核心功能（多段并发生成、对话式编辑、真实鉴权），但零测试覆盖。本阶段目标：
1. 给 graph 节点写单元测试（mock LLM 返回）
2. 给流式端点写 E2E 测试
3. 加 LLM 输出回归测试（fixture 录制）
4. Docker Compose 完整化

面试价值：能讲清 Agent 测试方法论——如何隔离 LLM 调用、如何测试状态机流转、如何验证 SSE 流式管线、如何用 fixture 防止 prompt 回归。

## 架构决策

### Mock 策略（核心面试点）

项目已有的依赖注入设计让 mock 非常干净：

```
StructuredChatClient (client.py)
    ↑ 被注入到
Agent 类 (deepseek.py)  ← 传 chat=MockChatClient 即可隔离 LLM
    ↑ 被注入到
Graph 工厂 (graph.py)   ← 传 mock agent 即可隔离 agent 逻辑
    ↑ 被调用
Workflow (content.py)   ← E2E 时 monkeypatch agent 构造
```

**三层 mock 粒度**：
- **Agent 单元测试**：mock `StructuredChatClient`，测 agent 的 prompt 拼接 + schema 校验 + 异常转译
- **Graph 流转测试**：mock agent 类（`AsyncMock`），测节点编排 + 状态流转 + 自纠环
- **E2E 流式测试**：monkeypatch `content.py` 模块里的 agent 构造，测 SSE 事件序列 + 落库

### 测试 DB 策略

- 纯逻辑测试（reducer / cross_check / sse_format）：不需要 DB
- Agent / Graph 测试：mock 掉 `retrieve_samples`（RAG 检索），不需要 DB
- E2E 端点测试：用 PostgreSQL 同实例的 `resume_test` 库，通过 `DATABASE_URL` 环境变量切换；conftest 建表 + 事务回滚隔离

## 文件结构

```
backend/tests/
├── conftest.py                         # 共享 fixtures
├── fixtures/
│   └── llm_outputs/                    # 录制的 LLM 输出（回归测试）
│       ├── chief_plan.json
│       ├── writer_draft_skill.json
│       ├── writer_draft_project.json
│       └── proof_report_pass.json
├── unit/
│   ├── conftest.py                     # FakeChatClient + mock agent fixtures
│   ├── test_agents.py                  # Agent 类单元测试
│   ├── test_graph_flow.py              # Graph 状态流转测试
│   ├── test_reducer.py                 # merge_sections reducer
│   ├── test_cross_check.py             # cross_check_node 纯逻辑
│   └── test_sse_format.py             # format_sse + 事件 schema
├── e2e/
│   ├── conftest.py                     # 测试 DB + httpx AsyncClient fixtures
│   ├── test_auth_flow.py               # 注册→登录→refresh→logout
│   ├── test_streaming.py               # SSE 流式生成端点
│   └── test_resume_crud.py             # 简历 CRUD + 用户隔离
└── record_fixtures.py                  # 手动跑：用真实 LLM 录制 fixture（可选）
docker-compose.yml                      # PostgreSQL + Redis + Backend
```

## 实施步骤

### Step 1: 测试基础设施 — conftest + FakeChatClient

**文件**: `backend/tests/conftest.py`, `backend/tests/unit/conftest.py`

创建 `FakeChatClient` 类（核心 mock 组件）：

```python
class FakeChatClient:
    """替身 StructuredChatClient，按 purpose 返回预置 Pydantic 对象。
    
    面试点：不 mock SDK 层（ChatOpenAI），而是 mock 业务封装层（StructuredChatClient），
    因为 agent 依赖的是封装层接口，不是 SDK 接口。这样换 SDK 不影响测试。
    """
    def __init__(self, responses: dict[str, BaseModel]):
        self._responses = responses
        self.call_log: list[dict] = []  # 记录调用参数，断言 prompt 内容

    async def complete(self, schema, *, system, user, purpose):
        self.call_log.append({"purpose": purpose, "system": system, "user": user})
        resp = self._responses.get(purpose)
        if resp is None:
            raise KeyError(f"FakeChatClient 没有为 purpose='{purpose}' 配置返回值")
        return resp

    async def complete_with_tools(self, schema, *, system, user, purpose, tools):
        # 和 complete 一样，工具调用循环由真实 complete_with_tools 处理
        # 测试 agent 逻辑时不需要测工具循环（那是 client.py 的职责）
        return await self.complete(schema, system=system, user=user, purpose=purpose)
```

Mock agent fixtures（用 `AsyncMock` spec 绑定到真实 agent 类）：

```python
@pytest.fixture
def mock_chief_editor():
    agent = AsyncMock(spec=ChiefEditorAgent)
    agent.generate.return_value = ChiefPlan(sections={
        "0": SectionPlan(focus="专业技能", tone_hint="专业严谨", avoid=[]),
        ...
    })
    return agent

@pytest.fixture
def mock_writer_pass():
    """撰写 agent：一次过版"""
    agent = AsyncMock(spec=DeepSeekWriterAgent)
    agent.generate.return_value = ContentDraft(title="专业技能", content={"items": [...]})
    return agent

@pytest.fixture
def mock_proofreader_pass():
    agent = AsyncMock(spec=DeepSeekProofReaderAgent)
    agent.review.return_value = ProofReport(passed=True, issues=[])
    return agent
```

### Step 2: Agent 单元测试

**文件**: `backend/tests/unit/test_agents.py`

用 `FakeChatClient` 构造真实 agent 实例（不 mock agent 类本身），测 agent 内部逻辑：

- `test_outline_generator_valid`: FakeChatClient 返回合法 OutlineDraft → agent.generate() 返回正确对象
- `test_outline_generator_wrong_section_count`: FakeChatClient 返回段数不符 → 抛 InvalidOutlineOutputError
- `test_outline_generator_empty_title`: 返回空 title → 抛 InvalidOutlineOutputError
- `test_writer_agent_with_plan`: 传 plan 参数 → 验证 system prompt 含 plan 内容（检查 call_log）
- `test_writer_agent_without_plan`: plan=None → system prompt 不含主编指引段
- `test_proofreader_pass`: FakeChatClient 返回 ProofReport(passed=True) → review() 返回正确
- `test_proofreader_llm_error`: FakeChatClient 抛 InvalidModelOutputError → review() 转译为 InvalidContentOutputError
- `test_chief_editor_generates_plan`: 返回 ChiefPlan → 验证 sections dict 结构

### Step 3: Graph 状态流转测试

**文件**: `backend/tests/unit/test_graph_flow.py`

用 mock agent 构建**真实** graph，ainvoke 后断言 state：

**单段子图测试**（`build_section_subgraph`）：
- `test_subgraph_pass_first_try`: writer 产出 → proofreader 过 → state["issues"] 为空 → 走 END
- `test_subgraph_revise_then_pass`: 第 1 次 proofreader 不过（issues 非空）→ 回 writer → 第 2 次过 → END
- `test_subgraph_exhaust_retries`: 一直不过 → attempts 达到 max_retries → 走 END（issues 非空）
- `test_subgraph_attempts_increment`: 验证每次 writer 调用后 attempts +1
- `test_subgraph_issues_history_accumulates`: 验证 issues_history 累积每轮 issues

**主图测试**（`build_multi_section_graph`）：
- `test_main_graph_chief_then_fanout`: chief_editor 调 1 次 → 5 个 writer 各调 1 次 → 验证 sections 有 5 个 key
- `test_main_graph_cross_check_no_conflict`: 5 段无重复关键字 → cross_issues 为空
- `test_main_graph_cross_check_finds_duplicate`: 2 段有相同 company → cross_issues 非空
- `test_main_graph_on_event_callback`: 验证 SSE 事件回调被触发（status / check / done）

### Step 4: 纯逻辑单元测试

**文件**: `backend/tests/unit/test_reducer.py`, `test_cross_check.py`, `test_sse_format.py`

**test_reducer.py** — `merge_sections`：
- `test_merge_disjoint_keys`: left={"0":...} right={"1":...} → 合并有 2 个 key
- `test_merge_same_key_overwrites`: left={"0":A} right={"0":B} → 结果是 B（右覆盖左）
- `test_merge_does_not_mutate_left`: 验证 left 原字典不被修改（浅拷贝约束）
- `test_merge_empty_left`: left={} right={"0":...} → 结果 = right

**test_cross_check.py** — `make_cross_check_node`：
- `test_no_duplicates`: 5 段各有不同 company → cross_issues 空
- `test_duplicate_company`: 段 0 和段 2 都有 "XX公司" → cross_issues 含 "段0与段2重复"
- `test_multiple_duplicates`: 多对重复 → 每个 pair 都报
- `test_empty_draft_skipped`: draft=None 的段被跳过

**test_sse_format.py** — `format_sse` + 事件 schema：
- `test_format_status_event`: StatusEvent → 输出含 "event: status" + JSON data
- `test_format_done_event`: DoneEvent → 含 content/title/attempts
- `test_format_error_event`: ErrorEvent → 含 message
- `test_sse_ends_with_double_newline`: 格式以 "\n\n" 结尾
- `test_all_event_types_serializable`: 6 种事件都能 model_dump + json.dumps

### Step 5: E2E — 流式生成端点

**文件**: `backend/tests/e2e/conftest.py`, `backend/tests/e2e/test_streaming.py`

**e2e/conftest.py** 核心 fixtures：
- `test_db`: 创建测试 DB engine + session factory，建表，测试完 drop
- `test_client`: httpx.AsyncClient + app.dependency_overrides 覆盖 get_session + get_current_user
- `mock_llm`: monkeypatch `content.py` 模块里的 `create_chat_model` + 三个 agent 类

**test_streaming.py** 测试场景：
- `test_stream_success`: 简历 + 大纲存在 → SSE 流含 status → check → done 事件 → DB 落库
- `test_stream_resume_not_found`: 不存在的 resume_id → SSE error 事件
- `test_stream_no_outline`: 简历存在但无大纲 → SSE error 事件
- `test_stream_outline_not_confirmed`: 大纲状态非 confirmed → SSE error 事件
- `test_stream_llm_failure`: mock agent 抛异常 → SSE error 事件（不 500）

SSE 解析辅助：从 httpx response 逐行解析 `event:` / `data:` 行，重建事件列表。

### Step 6: E2E — 鉴权端点

**文件**: `backend/tests/e2e/test_auth_flow.py`

测试场景：
- `test_register_success`: POST /auth/register → 201 + TokenPair 结构正确
- `test_register_duplicate_email`: 重复注册 → 409
- `test_register_password_too_short`: 密码 <8 → 422
- `test_login_success`: 先注册再登录 → 200 + TokenPair
- `test_login_wrong_password`: → 401
- `test_refresh_token_rotation`: 登录拿 refresh → /auth/refresh → 新 token 对 → 旧 refresh 失效
- `test_logout_revokes_refresh`: 登出后旧 refresh 不能再 refresh
- `test_protected_endpoint_without_token`: 无 token 访问 /resumes → 401
- `test_protected_endpoint_with_token`: 有 token → 200

### Step 7: LLM 输出回归测试（Fixture 录制）

**文件**: `backend/tests/fixtures/llm_outputs/*.json`, `backend/tests/record_fixtures.py`

模式：
1. `record_fixtures.py`（手动跑，需真实 API Key）：调真实 LLM 生成 chief_plan / writer_draft / proof_report，存 JSON
2. 单元测试加载 fixture JSON → 构造 `FakeChatClient(responses={...})` → 验证 agent 能正确解析
3. 如果 prompt 改动导致 LLM 输出结构变化 → fixture 过期 → 测试失败 → 重新录制

这不是"测 LLM 是否变聪明了"（那不可能），而是"测 agent 代码能否正确解析 LLM 的历史输出格式"——防止 prompt 改动后解析逻辑默默 break。

### Step 8: Docker Compose

**文件**: `docker-compose.yml`（项目根）

```yaml
services:
  postgres:
    image: postgres:16
    ports: ["39432:5432"]
    environment:
      POSTGRES_USER: resume
      POSTGRES_PASSWORD: resume
      POSTGRES_DB: resume
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7
    ports: ["39379:6379"]

  backend:
    build: ./backend
    ports: ["39801:39801"]
    depends_on: [postgres, redis]
    env_file: ./backend/.env
    command: uvicorn app.main:app --host 0.0.0.0 --port 39801

volumes:
  pgdata:
```

+ `backend/Dockerfile`（python:3.12-slim + uv install + uvicorn 启动）

## 验证

```bash
# 全量测试（需 PostgreSQL 运行）
cd backend && uv run pytest -v

# 仅单元测试（不需 DB）
uv run pytest tests/unit/ -v

# 仅 E2E 测试（需 PostgreSQL test 库）
uv run pytest tests/e2e/ -v

# 类型检查
npx tsc --noEmit  # 前端无改动，跳过

# Docker
docker-compose up -d  # 启动全栈
```

## 面试讲点清单

1. **三层 mock 粒度**：SDK 层 → 业务封装层（StructuredChatClient）→ Agent 类。选业务封装层 mock，因为 agent 依赖的是封装层接口
2. **Graph 测状态不测 LLM**：mock agent → 构建 graph → ainvoke → 断言 state 字段（attempts / issues / issues_history）
3. **自纠环测试**：proofreader 第一次返回 issues=False → 验证 writer 被调 2 次、attempts=2、issues_history 有 1 条
4. **SSE E2E**：monkeypatch agent 构造 → httpx 读流 → 解析 SSE 事件序列 → 断言 event 顺序 + DB 落库
5. **Fixture 回归**：录真实 LLM 输出 → 测试加载 fixture → 验证解析逻辑不 break
6. **并发 Send 测试**：5 段并发子图 → 验证 merge_sections reducer 合并 5 份结果
