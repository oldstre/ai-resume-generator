# AI Resume Generator

基于 LangGraph 多 Agent 架构的智能简历生成器。用户输入求职岗位与偏好参数，系统通过主编 Agent 规划大纲、多段 Writer Agent 并发生成正文、Proofreader Agent 校对自纠，最终输出结构化简历并支持 PDF 导出与对话式编辑。

## 架构亮点

- **多 Agent 并发**：主编（ChiefEditor）拆解大纲后，5 段内容由独立 Writer Agent 并发生成，每段自带 Proofreader 自纠环（最多重试 N 次）
- **LangGraph 状态机**：用 StateGraph + Send API 实现多段子图并发派发，自定义 reducer 合并状态
- **SSE 流式进度**：前端实时感知每段生成/校对/完成事件，非轮询
- **对话式编辑**：ReAct Agent + 工具调用，用户用自然语言修改简历（"把项目经历改短一点"），PostgreSQL Checkpointer 持久化会话
- **JWT 双 Token 鉴权**：Access + Refresh Token 轮转，401 自动刷新 + 并发去重

## 技术栈

| 层 | 技术 |
|---|---|
| **后端** | FastAPI、LangGraph、LangChain、SQLAlchemy 2.0 (async)、asyncpg、Alembic、ARQ |
| **LLM** | DeepSeek API、`with_structured_output` 结构化输出、Tavily 联网搜索 |
| **前端** | React 19、TypeScript、Vite、Tailwind CSS、Zustand、TanStack Query |
| **基础设施** | Docker Compose、PostgreSQL 16、Redis 7、pgvector |
| **测试** | pytest + pytest-asyncio，67 个测试（单元 52 + E2E 15） |

## 项目结构

```
backend/
├── app/
│   ├── api/v1/              # 路由层：resumes, outlines, contents, auth, chat
│   ├── core/                # 基础设施：db, redis, config, security, checkpoint
│   ├── models/              # ORM 模型：User, Resume, ResumeOutline, ResumeContent, RefreshToken
│   ├── schemas/             # Pydantic 请求/响应 schema
│   ├── llm/                 # LLM 封装
│   │   ├── deepseek.py      # OutlineGenerator, WriterAgent, ProofReaderAgent, ChiefEditorAgent
│   │   ├── graph.py         # LangGraph 多段并发生成图（Send API + 自定义 reducer）
│   │   ├── chat_graph.py    # 对话式编辑 ReAct 图
│   │   └── chat_tools.py    # 工具：list_sections, get_section, update_section
│   ├── workflows/           # 业务编排：SSE 流式生成、大纲生成
│   └── worker/              # ARQ 异步任务
├── tests/                   # 测试
│   ├── unit/                # 单元测试（mock LLM，不依赖 DB）
│   ├── e2e/                 # 端到端测试（真实 PostgreSQL + SSE）
│   └── fixtures/            # LLM 输出回归 fixture
├── Dockerfile
└── pyproject.toml

frontend/
└── src/
    ├── features/
    │   ├── auth/            # JWT 双 token 存储 + 401 自动刷新
    │   └── resumes/         # 简历列表/创建/详情/对话编辑
    ├── pages/               # AuthPage, ResumesPage, CreatePage, ResumeDetailPage
    └── components/ui/       # Button, Dialog, TextField, PillSelect...
```

## 快速开始

### Docker 一键部署

```bash
# 1. 克隆
git clone https://github.com/<your-username>/ai-resume-generator.git
cd ai-resume-generator

# 2. 配置环境变量
cp backend/.env.example backend/.env
# 编辑 .env，填入 LLM_API_KEY 等

# 3. 启动
docker compose up -d --build

# 4. 验证
curl http://localhost:39801/docs
```

### 本地开发

**后端**：

```bash
cd backend
uv sync
cp .env.example .env  # 填入 API Key
uv run alembic upgrade head
uv run uvicorn app.main:app --port 39801 --reload
```

**前端**：

```bash
cd frontend
npm install
npm run dev  # http://localhost:39173
```

## 核心流程

### 多段并发生成（LangGraph）

```
用户提交生成请求
    │
    ▼
ChiefEditor Agent ──→ ChiefPlan（规划 5 段大纲）
    │
    ├── Send → Section 1: Writer → Proofreader → (fail? rewrite) → pass
    ├── Send → Section 2: Writer → Proofreader → (fail? rewrite) → pass
    ├── Send → Section 3: Writer → Proofreader → (fail? rewrite) → pass
    ├── Send → Section 4: Writer → Proofreader → (fail? rewrite) → pass
    └── Send → Section 5: Writer → Proofreader → (fail? rewrite) → pass
    │
    ▼
CrossCheck（跨段查重：公司/学校重复检测）
    │
    ▼
SSE 推送 done 事件 + DB 落库
```

每段独立自纠，失败自动重写，最多重试 N 次。全程通过 SSE 事件流推送进度。

### 对话式编辑（ReAct Agent）

```
用户："把项目经历那段改短一点"
    │
    ▼
ChatGraph (ReAct) ──→ 调用 list_sections 工具
                   ──→ 调用 get_section("项目经历") 工具
                   ──→ 调用 update_section("项目经历", 新内容) 工具
                   ──→ 回复用户 "已修改"
    │
    ▼
PostgreSQL Checkpointer 持久化对话历史
```

## 测试

```bash
cd backend

# 单元测试（不需要 DB，mock LLM）
uv run pytest tests/unit/ -v

# E2E 测试（需要 PostgreSQL）
uv run pytest tests/e2e/ -v

# 全量
uv run pytest -v
```

测试策略：

| 层级 | 策略 | 示例 |
|------|------|------|
| Agent 单元测试 | FakeChatClient mock 业务封装层 | 验证 prompt 拼接、schema 校验、异常转译 |
| Graph 状态测试 | AsyncMock(spec=Agent) → 真实 graph → 断言 state | 自纠环重试、attempts 累积、issues_history |
| 纯逻辑测试 | 直接调函数 | reducer 合并、跨段查重、SSE 序列化 |
| E2E 鉴权 | 真实 PostgreSQL + JWT | 注册/登录/刷新/登出/受保护端点 |
| E2E 流式 | monkeypatch agent → httpx 读 SSE 流 | 事件序列 + DB 落库验证 |
| LLM 回归 | fixture 录制回放 | 防止 prompt 改动后解析 break |

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/register` | 注册 |
| POST | `/api/v1/auth/login` | 登录（返回 access + refresh token） |
| POST | `/api/v1/auth/refresh` | 刷新 token（轮转） |
| POST | `/api/v1/auth/logout` | 登出（撤销 refresh token） |
| GET | `/api/v1/resumes` | 列出当前用户的简历 |
| POST | `/api/v1/resumes` | 创建简历 |
| POST | `/api/v1/resumes/{id}/outlines/generate` | 生成大纲 |
| POST | `/api/v1/resumes/{id}/contents/regenerate/stream` | SSE 流式生成所有段 |
| PATCH | `/api/v1/resumes/{id}/contents/{section_index}` | 手动编辑某段 |
| POST | `/api/v1/resumes/{id}/chat` | 对话式编辑 |
| GET | `/api/v1/resumes/{id}/export/pdf` | 导出 PDF |

完整文档：启动后访问 `http://localhost:39801/docs`

## 环境变量

参考 `backend/.env.example`，关键变量：

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 连接串 |
| `LLM_API_KEY` | DeepSeek API Key |
| `LLM_BASE_URL` | LLM API 地址 |
| `LLM_MODEL` | 模型名 |
| `TAVILY_API_KEY` | 联网搜索 API Key |
| `DASHSCOPE_API_KEY` | 向量嵌入 API Key |
| `CORS_ORIGINS` | 允许的前端来源 |

## License

MIT
