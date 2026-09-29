# AI 简历生成器 · 开发文档

> 本文档面向项目维护者，记录架构设计、核心流程、关键决策与已知问题。阅读本文档应能帮助你快速接手并继续开发本项目。

---

## 1. 项目概述

**项目名**：`ai-resume-generator`（AI 简历生成器）

**一句话定位**：用户输入求职岗位、语气、段落数、内容密度等参数，系统调用 LLM 自动生成简历大纲与各段正文，支持人工编辑、单段重生成与 PDF 导出。

**项目类型**：个人学习项目，从同目录下的 `ai-ppt-generator-main` 套壳改造而来（复用其 Docker 容器与部分基础设施）。

**当前状态**：核心 MVP 完成，路径 A（已就绪简历渲染 + PDF 导出）验证通过；路径 B（新建简历完整流程）未测，ARQ worker 未重启。

---

## 2. 技术栈

### 后端
- **语言**：Python 3.13（.venv 由 uv 管理）
- **Web 框架**：FastAPI（异步）
- **数据库**：PostgreSQL，驱动 asyncpg，ORM SQLAlchemy 2.0 异步模式
- **缓存/队列**：Redis + ARQ（异步任务队列）
- **LLM 集成**：LangChain、DeepSeek API、`with_structured_output` 结构化输出
- **PDF 渲染**：reportlab
- **数据库迁移**：Alembic
- **数据校验**：Pydantic v2
- **HTTP 客户端**：httpx
- **包管理**：uv（`pyproject.toml` + `uv.lock`）

### 前端
- **框架**：React 19.2 + TypeScript 5.9
- **构建工具**：Vite 8
- **路由**：react-router 8
- **状态管理**：Zustand 5
- **数据请求**：TanStack Query 5
- **样式**：Tailwind CSS 4
- **包管理**：npm

### 基础设施
- **容器编排**：Docker Compose（复用 `d:\development\ai-ppt-generator-main\docker-compose.yml`）
- **Postgres 容器**：`ai-ppt-generator-main-postgres-1`，端口 `39432`，user/pass=resume，db=resume
- **Redis 端口**：`39379`

---

## 3. 项目结构

### 后端目录树

```
backend/
├── .env                          # 环境变量（LLM_API_KEY、DB DSN、Redis DSN）
├── pyproject.toml                # 依赖声明
├── uv.lock                       # 依赖锁
├── alembic.ini                   # 迁移配置
├── alembic/
│   ├── env.py
│   └── versions/                 # 三个迁移：resumes → outlines → contents
└── app/
    ├── main.py                   # FastAPI 应用入口、路由聚合
    ├── core/
    │   ├── config.py             # Settings（pydantic-settings 加载 .env）
    │   ├── db.py                 # AsyncEngine、async_session_factory、Base
    │   ├── redis.py              # Redis 客户端
    │   ├── queue.py              # ARQ 连接池 get_arq_pool
    │   └── paths.py              # 路径工具
    ├── models/                   # SQLAlchemy ORM 模型
    │   ├── __init__.py           # 必须导入所有模型，否则 Alembic 不识别
    │   ├── resume.py
    │   ├── resume_outline.py
    │   └── resume_content.py
    ├── schemas/                  # Pydantic 请求/响应 schema
    │   ├── resumes.py
    │   ├── resume_outline.py
    │   └── resume_content.py
    ├── api/v1/                   # 路由分层（前缀 /api/v1）
    │   ├── health.py             # 健康检查
    │   ├── resumes.py            # 简历 CRUD
    │   ├── outlines.py           # 大纲生成/确认/取消
    │   └── contents.py           # 内容生成/重生成/编辑
    ├── workflows/                # 业务编排层（API 与 worker 共用）
    │   ├── outline.py            # generate_outline
    │   └── content.py            # generate_content（含自纠环）
    ├── llm/                      # LLM 客户端封装
    │   ├── base.py               # Protocol、Pydantic Draft、输入输出类型
    │   ├── client.py             # StructuredChatClient
    │   ├── deepseek.py           # DeepSeekOutlineGenerator、DeepSeekContentGenerator
    │   └── errors.py             # LLMNotConfiguredError、InvalidXxxOutputError
    ├── services/                 # 业务服务
    │   ├── quality_check.py      # check_content 质量检查
    │   └── pdf_exporter.py       # reportlab PDF 导出
    └── worker/
        ├── settings.py           # WorkerSettings（注册函数对象）
        └── tasks.py              # generate_outline_task、generate_all_contents_task
```

### 前端目录树

```
frontend/
├── package.json
├── vite.config.ts
└── src/
    ├── main.tsx                  # 入口
    ├── App.tsx                   # 路由表
    ├── api/client.ts             # fetch 封装、统一错误处理
    ├── routes/RequireAuth.tsx   # 鉴权路由守卫（mock）
    ├── lib/                      # 工具：sse、download、errors、datetime、utils
    ├── hooks/useEventStream.ts   # SSE hook（备用）
    ├── components/
    │   ├── AppShell.tsx
    │   ├── BrandMark.tsx
    │   ├── WorkbenchHeader.tsx
    │   ├── StatusPill.tsx
    │   └── ui/                   # Button、Dialog、TextField、PillSelect、MenuPopover
    ├── features/
    │   ├── auth/                 # mock 登录、token、store
    │   └── resumes/               # api.ts、types.ts、options.ts
    └── pages/
        ├── AuthPage.tsx          # 登录页
        ├── ResumesPage.tsx       # 列表页
        ├── CreatePage.tsx        # 创建页（6 字段表单）
        └── ResumeDetailPage.tsx  # 详情页（大纲 + 正文 + PDF 导出）
```

---

## 4. 数据库设计

### 4.1 表结构概览

三张主表，按生命周期顺序：

| 表 | 说明 | 关键约束 |
|---|---|---|
| `resumes` | 简历主表 | PK: id (UUID) |
| `resume_outlines` | 大纲（1:1） | FK: resume_id (CASCADE)，revision 乐观锁 |
| `resume_contents` | 各段正文（1:N） | FK: resume_id (CASCADE)，UniqueConstraint(resume_id, section_index) |

### 4.2 状态机

#### Resume.status
```
draft → outline_ready → generating → ready
```
- `draft`：新建简历，大纲尚未生成
- `outline_ready`：大纲已确认，等待触发正文生成
- `generating`：ARQ worker 正在生成正文
- `ready`：所有段正文就绪，可导出 PDF

#### ResumeOutline.status
```
generating → draft → confirmed
```
- `generating`：调 LLM 生成中
- `draft`：LLM 已返回，等待用户确认
- `confirmed`：用户已确认，可触发正文生成
- 异常：`failed`（LLM 输出违规或 worker 异常）

#### ResumeContent.status（每段独立）
```
pending → generating → ready
                     └→ failed
```
- `pending`：占位记录已建（title 先行落库），未生成
- `generating`：正在调 LLM
- `ready`：本段内容就绪
- `failed`：自纠环 3 次仍未通过 / LLM 异常 / worker 异常

### 4.3 关键字段

**Resume**：`title`、`applicant_name`、`target_position`、`tone`（语气）、`section_count`（段数）、`content_density`（内容密度）、`status`

**ResumeOutline**：
- `sections`：JSONB，结构 `[{"title": "...", "content": "..."}]`，content 字段是"写什么描述"而非正文
- `revision`：乐观锁版本号
- `error`：失败诊断信息

**ResumeContent**：
- `section_index`：大纲段索引（0-based），与 `resume_id` 共同组成幂等键
- `position`：展示排序（一般与 section_index 一致，保留独立字段便于人工调整）
- `title`：大纲段标题（生成正文前就先行落库，进度列表可显示）
- `content`：JSONB，两种形状
  - `{items: [...]}`：技能/项目/工作/教育段（列表型）
  - `{paragraphs: [...]}`：自我评价段（段落型）
- `issues`：JSONB，自纠环历史（空列表 = 一次通过）
- `revision`：乐观锁

### 4.4 设计要点

1. **幂等键**：`UniqueConstraint(resume_id, section_index)` —— 重试、断点恢复、重复入队都落到同一行，不会重复建段
2. **乐观锁**：大纲/内容并发编辑用 `revision` 字段，提交时若不一致判冲突
3. **JSONB 而非关系表**：简历内容结构多变（段落 vs 列表），JSONB 比 1:N 关系表更灵活，且 PostgreSQL 原生支持索引
4. **status 字段而非枚举类型**：用 String(16) 而非 PG enum，避免新增状态需迁移
5. **外键 CASCADE**：删简历级联删大纲与内容，不留孤儿记录

---

## 5. API 设计

所有路由前缀 `/api/v1`。

### 5.1 健康检查
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 返回 200 OK，含 DB/Redis 连通状态（即使依赖挂也返回 200，状态字段反映） |

### 5.2 简历 CRUD
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/resumes` | 列表 |
| POST | `/resumes` | 创建 |
| GET | `/resumes/{id}` | 详情（含大纲与段内容快照） |
| PATCH | `/resumes/{id}` | 部分更新 |
| DELETE | `/resumes/{id}` | 删除（级联） |

### 5.3 大纲
| 方法 | 路径 | 说明 | 状态码 |
|---|---|---|---|
| GET | `/resumes/{id}/outline` | 查大纲 | 200 / 404 |
| POST | `/resumes/{id}/outline/generate` | 异步生成大纲（入 ARQ 队列） | 202 + job_id |
| POST | `/resumes/{id}/outline/confirm` | 确认大纲（带 `{revision}`） | 200 / 409 |
| POST | `/resumes/{id}/outline/cancel` | 取消大纲（带 `{revision}`） | 200 / 409 |

> **坑点**：confirm/cancel 的请求体字段是 `revision`（正确拼写），前端曾误传 `revison` 导致 422。

### 5.4 正文内容
| 方法 | 路径 | 说明 | 状态码 |
|---|---|---|---|
| GET | `/resumes/{id}/contents` | 列出所有段（按 position 升序） | 200 |
| POST | `/resumes/{id}/contents/generate` | 异步批量生成（入 ARQ 队列） | 202 + job_id |
| POST | `/resumes/{id}/contents/{section_index}/regenerate` | 同步重生成单段 | 200 / 409 |
| PATCH | `/resumes/{id}/contents/{section_index}` | 手动编辑单段 | 200 / 404 |

**异步 vs 同步的取舍**：
- 批量生成进 ARQ 队列：5 段串行调 LLM 总耗时 30+ 秒，不能阻塞 API worker
- 单段重生成走同步：用户点"重生成这段"按钮，期望 5-10 秒看到结果

### 5.5 PDF 导出
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/resumes/{id}/export.pdf` | 同步生成并返回 PDF（Content-Type: application/pdf） |

> 业务规则：仅当 `resume.status == "ready"` 时允许导出，否则 409。

---

## 6. 核心业务流程

### 6.1 大纲生成流程

```
[用户] POST /resumes/{id}/outline/generate
   ↓
[API] 校验简历存在 → 入 ARQ 队列 → 返回 202 + job_id
   ↓
[Worker] generate_outline_task(ctx, resume_id_str)
   ↓ UUID(str → UUID)
   ↓ 开 session → workflows.outline.generate_outline(session, uuid)
   ↓ 构造 OutlineGenerationInput
   ↓ DeepSeekOutlineGenerator.generate()
   ↓   └ StructuredChatClient.complete(OutlineDraft, system, user)
   ↓       └ LLM with_structured_output → OutlineDraft
   ↓   └ _validate_draft（业务规则二次校验：段数、标题非空、内容非空）
   ↓ 写入 ResumeOutline（status=confirmed? 或 draft? 看实现）
   ↓ 翻转 resume.status → outline_ready
   ↓
[异常] _mark_outline_failed：兜底把 outline 标 failed
```

### 6.2 内容生成自纠环（核心）

`workflows/content.py` 的 `generate_content` 函数：

```python
MAX_RETRIES = 3
issues_for_llm: list[str] = []     # 喂给 LLM 的字符串列表
issues_history: list[dict] = []    # 存数据库的自纠历史

for attempt in range(MAX_RETRIES):
    payload = ContentGenerationInput(
        ...,
        issues=issues_for_llm,     # 上一轮的问题（首轮为空）
    )
    draft = await generator.generate(payload)   # 失败抛异常 → status=failed
    report = check_content(draft)               # 质量检查
    if report.passed:
        break                                   # 通过了，跳出循环
    # 没通过 → 记录问题，下一轮喂回 LLM
    issues_for_llm = [f"{issue.field}: {issue.message}" for issue in report.issues]
    issues_history.append({
        "attempt": attempt + 1,
        "issues": [issue.model_dump() for issue in report.issues],
    })

# 存结果（无论通过还是 3 次用完，都用最后的 draft）
content.title = draft.title
content.content = draft.content
content.issues = issues_history   # 空列表 = 一次通过
content.status = "ready"
content.revision += 1
```

**关键设计**：
1. **Issues 作为反馈信号**：质检不通过的结构化 issues 转字符串回灌 LLM，LLM 知道哪里错了
2. **历史存数据库**：`issues` JSONB 字段保留每轮诊断，便于追溯
3. **最后一轮的结果即使未通过也存**：保证用户能看到内容（status=ready），而不是卡在 failed

### 6.3 异步批量生成流程

`worker/tasks.py` 的 `generate_all_contents_task`：

```
[用户] POST /resumes/{id}/contents/generate
   ↓
[API] 校验：简历存在 + outline.status == "confirmed"
   ↓ 校验失败 → 409 Conflict
   ↓ 入 ARQ 队列（resume_id 必须 str）
   ↓ 返回 202 + job_id
   ↓
[Worker]
   ↓ 1. 校验大纲仍为 confirmed（防止用户中途取消）
   ↓ 2. 翻转 resume.status → generating（前端据此显示"生成中"）
   ↓ 3. 逐段循环 section_index in range(section_count):
   ↓     每段独立 session（一段失败不影响其他段已提交的记录）
   ↓     try: generate_content(seg_session, uuid, idx)
   ↓     except 业务异常: continue（workflow 内部已标 failed）
   ↓     except 非业务异常: _mark_content_failed + continue
   ↓ 4. 全部段处理完 → 翻转 resume.status → ready（仅在还是 generating 时）
   ↓
[异常兜底] _mark_outline_failed / _mark_content_failed
   只在 status 还是 generating 时才更新，避免覆盖 workflow 已精确处理的 failed
```

**关键设计**：
1. **每段独立 session**：一段失败事务回滚只影响这一段，其他段已成功提交的记录不丢
2. **业务异常吞掉继续下一段**：一份简历 5 段，第 3 段失败不影响第 4、5 段
3. **状态翻转只在 generating 时**：避免覆盖用户中途的其他状态变更
4. **兜底异常处理不 raise**：DB 已写 failed 状态，前端能查到，ARQ 不必再标记 job failed，worker 日志干净

### 6.4 单段重生成流程（同步）

```
[用户] POST /resumes/{id}/contents/{section_index}/regenerate
   ↓
[API] 直接调用 generate_content(session, resume_id, section_index)
   ↓ 同步等 LLM 返回（5-10 秒）
   ↓ 成功 → 200 + ResumeContentPublic
   ↓ 业务异常（InvalidContentOutputError/LLMNotConfiguredError）→ 409
   ↓ ValueError（简历不存在/大纲未确认/索引越界）→ 409
```

不走 ARQ 的原因：用户点了"重生成这段"按钮，期望立即看到结果，5-10 秒同步等待可接受；若走 ARQ 还需轮询，体验差。

### 6.5 PDF 导出流程

```
[用户] GET /resumes/{id}/export.pdf
   ↓
[API] 校验 resume.status == "ready"（否则 409）
   ↓ 查简历 + 大纲 + 所有段内容
   ↓ services.pdf_exporter.export(resume, outline, contents) → bytes
   ↓   └ reportlab Document + Flowable
   ↓       ├── Paragraph（标题/段落）
   ↓       ├── Spacer（间距）
   ↓       └── Table（技能/项目等列表型）
   ↓ 返回 StreamingResponse(content=bytes, media_type="application/pdf")
```

---

## 7. LLM 集成

### 7.1 双层校验

第一层：LangChain `with_structured_output(OutlineDraft/ContentDraft)` —— 保证 JSON 结构正确（字段名/类型对）

第二层：业务规则校验（`_validate_draft`）—— with_structured_output 没法强制的业务规则：
- 段数必须等于 `section_count`
- 标题非空
- 内容非空
- 标题长度 ≤ 30 字符

### 7.2 异常分层

```
LLMNotConfiguredError           # 没配 API key
InvalidModelOutputError         # 通用：模型输出不符 schema
  ├── InvalidOutlineOutputError  # 大纲专用
  └── InvalidContentOutputError  # 内容专用
```

业务层捕获时能精确处理（重试大纲生成 vs 重试内容生成），不需要看错误信息判断类型。

### 7.3 Prompt 工程

- **System Prompt**：人设 + JSON 结构 + 硬约束 + 示例（Few-shot）
- **User Prompt**：具体任务 + 输入参数 + 内容密度提示
- **标准段名倾向**：`_PREFERRED_TITLES` 元组引导 LLM 用"个人信息/专业技能/项目经历/工作经历/教育经历"等标准名

### 7.4 内容密度提示

```python
density_hint = {
    "concise": "请控制每段内容简洁，1-2 句话。",
    "medium": "每段内容适中，2-3 句话。",
    "detailed": "每段内容详尽，3-5 句话，包含具体技术栈或成就。",
}
```

---

## 8. 异步任务（ARQ）

### 8.1 WorkerSettings

`app/worker/settings.py` 注册函数对象（不是字符串名）：

```python
class WorkerSettings:
    functions = [generate_outline_task, generate_all_contents_task]
    redis_settings = RedisSettings(host=..., port=39379)
```

### 8.2 任务参数序列化约束

- **UUID 必须转 str**：通过 Redis 队列传递，UUID 不可 JSON 序列化
- **入队用函数名字符串**：`pool.enqueue_job("generate_outline_task", resume_id=str(resume_id))`
- **worker 函数签名**：`async def task(ctx: dict, resume_id: str)` —— 接收端拿到的是 str，内部再 `uuid.UUID(resume_id)` 转

### 8.3 三个并发模型

| 进程 | 角色 | 职责 |
|---|---|---|
| uvicorn | API worker | 接 HTTP、入队、立即返回 202 |
| arq worker | 任务执行者 | 长任务执行（调 LLM、写 DB） |
| Postgres + Redis | 基础设施 | 数据 + 队列 |

API 不阻塞在长任务上，ARQ worker 不接 HTTP，互不干扰。

---

## 9. 前端架构

> 注：本项目以前端能用为优先，未深入优化。文档只记关键决策。

### 9.1 路由表

| 路径 | 组件 | 说明 |
|---|---|---|
| `/login` | AuthPage | 登录页（mock） |
| `/resumes` | ResumesPage | 列表页（需鉴权） |
| `/create` | CreatePage | 创建页（6 字段表单） |
| `/resumes/:id` | ResumeDetailPage | 详情页（大纲区 + 正文区 + PDF 导出） |

### 9.2 状态管理分层

- **服务端状态**：TanStack Query（简历/大纲/内容/导出）—— 所有 LLM 相关数据都走 Query
- **客户端状态**：Zustand（仅 auth token，UI 局部状态用 useState）

### 9.3 轮询门控

`ResumeDetailPage` 用 TanStack Query 轮询 `GET /contents`：

```typescript
refetchInterval: (query) => {
  const contents = query.state.data;
  if (!contents) return false;
  const allReady = contents.every(c => c.status === "ready" || c.status === "failed");
  return allReady ? false : 2000;   // 生成中 2s 轮询，全部就绪停
}
```

全部段就绪后，`useEffect` 自动 `invalidateQueries` 详情接口，拿最新 `resume.status` 触发"导出 PDF"按钮显示。

### 9.4 鉴权（mock）

`features/auth/api.ts` 是写死的假 token，无真实后端鉴权。**遗留口子**：生产化前需接真实 OAuth/JWT。

---

## 10. 配置说明

### 10.1 后端 `.env`（`backend/.env`）

```env
DATABASE_URL=postgresql+asyncpg://resume:resume@127.0.0.1:39432/resume
REDIS_URL=redis://127.0.0.1:39379/0
LLM_API_KEY=sk-...                  # DeepSeek API key，缺失抛 LLMNotConfiguredError
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
```

### 10.2 端口约定

| 服务 | 端口 |
|---|---|
| 后端 API | 39801 |
| 前端 dev | 39173 |
| Postgres | 39432 |
| Redis | 39379 |

### 10.3 前端代理

`vite.config.ts` 中 `/api` 代理到 `http://127.0.0.1:39801`，避免 CORS。

---

## 11. 启动与停止

### 11.1 启动顺序（4 个独立 PowerShell 窗口）

#### ① 基础设施（Postgres + Redis）
```powershell
cd d:\development\ai-ppt-generator-main
docker compose up -d
```

#### ② 后端 API
```powershell
cd d:\development\ai-resume-generator\backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 39801
```

#### ③ ARQ Worker
```powershell
cd d:\development\ai-resume-generator\backend
uv run arq app.worker.tasks.WorkerSettings
```

> **务必启动 worker**，否则异步任务不执行，正文生成后 `resume.status` 不会自动翻 `ready`。

#### ④ 前端 dev server
```powershell
cd d:\development\ai-resume-generator\frontend
npm run dev
```

### 11.2 验证启动成功

- ① `docker ps` 看 postgres 与 redis 容器 `Up`
- ② 浏览器 `http://127.0.0.1:39801/health` 返回 JSON
- ③ ARQ 启动日志列出注册的 `generate_outline_task`、`generate_all_contents_task`
- ④ 浏览器 `http://127.0.0.1:39173` 显示登录页

### 11.3 停止顺序

反向操作：先 `Ctrl+C` 停 ④③②，再 `docker compose down` 关 ①（容器可保留，下次直接 `docker compose start` 复用）。

---

## 12. 数据库迁移

### 12.1 生成新迁移

```powershell
cd d:\development\ai-resume-generator\backend
uv run alembic revision --autogenerate -m "描述本次变更"
```

### 12.2 应用迁移

```powershell
uv run alembic upgrade head
```

### 12.3 现有迁移

- `74ed202db0c6_create_resumes_table.py`
- `890918519b6a_add_resume_outlines_table.py`
- `f5d3e94a6fab_add_resume_contents_table.py`

---

## 13. 测试数据

### 13.1 测试简历

| Resume ID | 状态 | 备注 |
|---|---|---|
| `1e326158-d815-48ff-a1e2-b2c4e0d29165` | ready（手动改） | 5 段正文 ready，路径 A 验证用 |
| `bf494661-...` | draft | 测试残留，可清 |
| `75fff308-...` | outline_ready | 测试残留，可清 |
| `9e376ab0-...` | draft | 测试残留，可清 |

### 13.2 手动改状态（绕过流程用于测试）

```sql
UPDATE resumes SET status = 'ready' WHERE id = '1e326158-d815-48ff-a1e2-b2c4e0d29165';
```

---

## 14. 关键设计决策

1. **状态机驱动**：用 `status` 字段而非状态字段散落各表，前端按状态决定 UI 视图，后端按状态决定可执行的转换
2. **业务编排层独立**（`workflows/`）：API 与 worker 共用同一份业务逻辑，避免重复实现
3. **LLM 双层校验**：结构靠 with_structured_output，业务靠 `_validate_draft`，分层防御
4. **自纠环而非单次调用**：把质检做成迭代反馈，提升内容合格率
5. **幂等键优先**：所有可能重复执行的操作都用幂等键兜底，断点续跑安全
6. **JSONB 而非关系表**：内容结构多变，JSONB 兼顾灵活与索引
7. **同步 vs 异步的取舍**：批量走 ARQ（耗时长），单段走同步（用户期望即时反馈）

---

## 15. 踩坑记录

### 15.1 worker 不回写 resume.status
**现象**：异步生成正文后，`resume.status` 仍是 `generating`，前端按钮不翻成"导出 PDF"。

**根因**：`generate_all_contents_task` 漏写状态翻转逻辑。

**修复**：在 `tasks.py` 末尾加上 `generating → ready` 翻转（仅在还是 generating 时）。

**遗留**：worker 进程未重启，新简历生成正文后状态仍不翻。需重启 ARQ worker 进程才能生效。

### 15.2 422 outline/confirm 字段拼写错误
**现象**：前端调用 `POST /outline/confirm` 返回 422 Unprocessable Content。

**根因**：前端误传字段名 `revison`，正确拼写是 `revision`。

**修复**：前端 schema 改回 `revision`。

**教训**：拼写错误是高频问题（user 也曾把 `functions` 写成 `fucntions`、`issues` 写成 `isssues`），代码审查时务必检查字段名。

### 15.3 ResumeContent 模型 Alembic 不识别
**现象**：`alembic revision --autogenerate` 生成空迁移，没有 `resume_contents` 表。

**根因**：`app/models/__init__.py` 没导入 `ResumeContent`，`Base.metadata.tables` 没加载该模型。

**修复**：在 `__init__.py` 加 `from app.models.resume_content import ResumeContent`，并加入 `__all__`。

**教训**：所有 ORM 模型必须在 `models/__init__.py` 显式导入，否则 Alembic autogenerate 看不到。

### 15.4 ARQ task 参数序列化
**现象**：worker 收到任务后报类型错误。

**根因**：直接传 `uuid.UUID` 对象，Redis 队列走 JSON 序列化失败。

**修复**：`enqueue_job("...", resume_id=str(resume_id))`，worker 内部再 `uuid.UUID(resume_id)` 转。

**关联坑**：`WorkerSettings.functions` 必须用函数对象 `[generate_outline_task, ...]`，不是字符串 `["generate_outline_task"]`。

### 15.5 PowerShell curl 引号/编码
**现象**：PowerShell 用 `curl` 发 POST 请求时 JSON 解析失败。

**根因**：Windows PowerShell 的 curl 是 `Invoke-WebRequest` 的别名，引号处理与 Unix curl 不同；且默认编码显示乱码。

**修复**：改用 `Invoke-RestMethod` 或显式调用 `curl.exe`（真正的 curl 二进制）。

**教训**：Windows 环境测试 HTTP 接口优先用 `Invoke-RestMethod`，避开别名坑。

---

## 16. 遗留口子

| # | 项 | 影响 | 建议处理 |
|---|---|---|---|
| 1 | ARQ worker 未重启 | 新简历正文生成后 status 不自动翻 ready | 重启 worker 进程后即生效 |
| 2 | 路径 B（新建简历完整流程）未测 | 新建简历 → 大纲 → 确认 → 正文 → 导出 完整链路未端到端验证 | 重启 worker 后跑一遍 |
| 3 | 登录是 mock | 无真实后端鉴权，token 写死 | 生产化前接 JWT/OAuth |
| 4 | DB 测试残留 | `bf494661`、`75fff308`、`9e376ab0` 占空间 | `DELETE FROM resumes WHERE id IN (...)` |
| 5 | 无自动化测试 | 仅有 `test_content.py`、`test_llm.py` 临时脚本 | 补 pytest 单测与集成测 |
| 6 | 无限流/熔断 | LLM 调用无限流，DeepSeek 限流会直接失败 | 加 ratelimit 库或 ARQ 内重试 |
| 7 | 无日志收集 | uvicorn/arq 日志只到 stdout | 接 loguru + 文件轮转或 ELK |

---

## 17. 扩展方向

按优先级：

1. **接 LangGraph**：把 `workflows/content.py` 的自纠环改写成 `StateGraph`，真正落地多 Agent 编排
2. **接 LangSmith/Langfuse**：每轮 LLM 调用链路可观测，便于调优
3. **Function Calling**：把"查简历/查大纲/写 DB"等动作封装为 tools，让 LLM 自主决策
4. **RAG**：简历模板库检索，复用历史优质结构
5. **Streaming 输出**：SSE 流式返回生成进度（前端已有 `useEventStream` hook 雏形）
6. **真实鉴权**：JWT + refresh token
7. **多模板 PDF**：支持不同简历模板（学术/技术/管理岗）

---

## 18. 参考资料

- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2.0 异步文档](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [ARQ 官方文档](https://arq-docs.helpmanual.io/)
- [LangChain Structured Output](https://python.langchain.com/docs/modules/model_io/models/chat/structured_output)
- [DeepSeek API](https://platform.deepseek.com/api-docs)
- [reportlab 用户指南](https://docs.reportlab.com/)
