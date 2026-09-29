/**
 * 简历相关类型定义。
 *
 * 对应后端 app/schemas/resumes.py 的 ResumePublic / ResumeCreate / ResumeUpdate。
 * 后端用 Pydantic 校验，前端用 TypeScript 类型——两边形状对得上，
 * request<Resume> 返回的数据 IDE 就能自动补全字段名，写错会红线。
 *
 * 字段命名保持 snake_case，跟后端 JSON 一致，省一层转换。
 */

/**
 * 简历状态。后端实际只会返回这 4 个值（见 backend/app/schemas/resumes.py）：
 *   draft        创建后未生成大纲，或大纲未确认
 *   outline_ready 大纲已确认，可以生成正文
 *   generating   正文正在生成
 *   ready        全部就绪，可导出 PDF
 *
 * StatusPill 组件还多列了 outline_pending / outline_confirmed / failed 三个值，
 * 是为兼容未来扩展和 Outline 自身状态字段保留的，目前 Resume.status 不会出现。
 */
export type ResumeStatus =
  | 'draft'
  | 'outline_ready'
  | 'generating'
  | 'ready'
  // 下面三个是 StatusPill 字典里的扩展值，后端 Resume.status 实际不会返回
  | 'outline_pending'
  | 'outline_confirmed'
  | 'failed'

/** 语气风格。后端 Literal["professional", "plain", "punchy"]。 */
export type Tone = 'professional' | 'plain' | 'punchy'

/** 内容密度。后端 Literal["concise", "medium", "detailed"]。 */
export type ContentDensity = 'concise' | 'medium' | 'detailed'

/**
 * 简历完整对象——后端 ResumePublic schema 的 TS 翻译。
 * 列表和详情页都拿这个形状。
 */
export interface Resume {
  id: string
  title: string
  applicant_name: string
  target_position: string
  tone: Tone
  section_count: number
  template_id: string
  template_overrides: Record<string, unknown>
  content_density: ContentDensity
  status: ResumeStatus
  created_at: string // ISO 时间字符串，后端 datetime 序列化后是 string
  updated_at: string
}

/**
 * 创建简历的入参——对应后端 ResumeCreate。
 * 不含 id / status / created_at 这些后端生成的字段。
 */
export interface ResumeCreate {
  title: string
  applicant_name: string
  target_position: string
  tone?: Tone // 不传后端默认 'professional'
  section_count?: number // 不传后端默认 5
  template_id?: string // 不传后端默认 'modern'
  content_density?: ContentDensity // 不传后端默认 'medium'
}

/** 修改简历的入参——对应后端 ResumeUpdate，所有字段可选。 */
export interface ResumeUpdate {
  title?: string
  applicant_name?: string
  target_position?: string
  tone?: Tone
  section_count?: number
  template_id?: string
  content_density?: ContentDensity
}

/* ============================================================
 * 大纲（Outline）相关类型
 * 对应后端 app/schemas/resume_outline.py
 * ========================================================== */

/** 大纲状态。后端 OutlineStatus Literal["generating", "draft", "confirmed", "failed"]。 */
export type OutlineStatus = 'generating' | 'draft' | 'confirmed' | 'failed'

/** 大纲里的一段：标题 + 这段要写什么。对应后端 ResumeSectionDraft。 */
export interface ResumeSectionDraft {
  title: string
  content: string
}

/** 大纲完整对象——对应后端 ResumeOutlinePublic。 */
export interface ResumeOutline {
  id: string
  resume_id: string
  status: OutlineStatus
  sections: ResumeSectionDraft[]
  revision: number
  job_id: string | null
  error: string | null
  created_at: string
  updated_at: string
}

/** PUT /outline 入参——对应后端 ResumeOutlineUpsert。 */
export interface ResumeOutlineUpsert {
  sections: ResumeSectionDraft[]
}

/**
 * POST /outline/confirm 入参——对应后端 ResumeOutlineRevisionRequest。
 * 字段名 revision（正确拼写）。
 */
export interface ResumeOutlineRevisionRequest {
  revision: number
}

/** POST /outline/generate 返回值。 */
export interface OutlineGenerateResponse {
  job_id: string
  status: 'generating'
}

/* ============================================================
 * 段内容（Content）相关类型
 * 对应后端 app/schemas/resume_content.py
 * ========================================================== */

/** 段内容状态。后端 ResumeContentStatus Literal["pending","generating","ready","failed"]。 */
export type ResumeContentStatus = 'pending' | 'generating' | 'ready' | 'failed'

/**
 * 段内容对象——对应后端 ResumeContentPublic。
 *
 * content 是 JSONB，两种形状（见后端 app/llm/deepseek.py 的 _example_for）：
 *   1. { items: [...] }      技能/项目/工作/教育段：列表项，每项是 dict（字段随段类型变）
 *   2. { paragraphs: [...] } 自我评价/职业概述段：字符串数组
 * 前端渲染时按 content.items / content.paragraphs 分支处理。
 */
export interface ResumeContent {
  id: string
  resume_id: string
  section_index: number
  position: number
  title: string
  status: ResumeContentStatus
  content: Record<string, unknown> | null
  issues: Record<string, unknown>[]
  error: string | null
  revision: number
  created_at: string
  updated_at: string
}

/** PATCH 编辑入参——对应后端 ResumeContentUpdate，所有字段可选。 */
export interface ResumeContentUpdate {
  /** 结构化正文，整个 JSON 对象覆盖。 */
  content?: Record<string, unknown> | null
  /** 段标题（比如把"技能"改成"核心技能"）。 */
  title?: string
}

/* ============================================================
 * SSE 事件类型（流式重新生成正文）
 * 对应后端 backend/app/schemas/sse_event.py
 *
 * 后端用 format_sse() 输出每帧：
 *   event: status\n
 *   data: {"stage":"generating","message":"...","attempt":1}\n\n
 *
 * 注意：data 行里只有 data 类的字段，不含 event 字段，
 * 前端解析时要同时读 event: 行和 data: 行，组装成 {event, data}。
 * ========================================================== */

/**
 * status 事件 data：流程状态变化（开始/重试/完成/失败）。
 *
 * section_index 字段：
 *   - null  → 整体级事件（主编开始 / 跨段校验 / 全部完成）
 *   - 0..N  → 某一段的事件（撰写开始 / 校对结果 / 单段完成）
 */
export interface SSEStatusData {
  stage: 'generating' | 'regenerating' | 'done' | 'failed'
  message: string
  attempt: number
  section_index?: number | null
}

/** tool 事件 data：LLM 决定调工具时触发。 */
export interface SSEToolData {
  name: string
  args: Record<string, unknown>
}

/** tool_done 事件 data：工具执行完成。 */
export interface SSEToolDoneData {
  name: string
}

/**
 * check 事件 data：质量检查结果。
 * section_index 语义同 SSEStatusData：null=跨段校验，0..N=单段校对。
 */
export interface SSECheckData {
  passed: boolean
  issues: string[]
  section_index?: number | null
}

/**
 * done 事件 data：单段最终完成，携带完整内容 JSON。
 * section_index 区分归属（多段端点会给每段发一个 done 事件）。
 */
export interface SSEDoneData {
  content: Record<string, unknown>
  title: string
  attempts: number
  issues_history: Record<string, unknown>[]
  section_index?: number | null
}

/** error 事件 data：出错事件。 */
export interface SSEErrorData {
  message: string
}

/**
 * SSE 事件联合类型——对应后端 SSEEvent discriminated union。
 * 用 event 字段做判别（discriminated union），TS 会自动收窄类型。
 *
 * 用法：
 *   if (e.event === 'status') { e.data  ← SSEStatusData }
 *   if (e.event === 'check')  { e.data  ← SSECheckData }
 */
export type SSEEvent =
  | { event: 'status'; data: SSEStatusData }
  | { event: 'tool'; data: SSEToolData }
  | { event: 'tool_done'; data: SSEToolDoneData }
  | { event: 'check'; data: SSECheckData }
  | { event: 'done'; data: SSEDoneData }
  | { event: 'error'; data: SSEErrorData }

/* ============================================================
 * 多段并发流式重新生成（POST /contents/regenerate/stream）
 *
 * 后端跑 LangGraph 多节点图：主编 → fan_out 5 个子图并发 → 跨段校验 → all_done。
 * 前端按 section_index 把事件聚合到 5 个段卡片上实时展示。
 * ========================================================== */

/**
 * 单段在多段流式中的状态机。
 *
 *   idle          → 还没轮到这一段（主编还在规划 / 别的段在跑）
 *   generating    → 段子图开始撰写（status 事件 stage='generating'）
 *   regenerating  → 校对没过，段子图自纠重写（stage='regenerating'）
 *   checking      → 校对中（收到 check 事件但 passed=false 还会回到 regenerating）
 *                   实际不显示这个过渡态，UI 直接到 regenerating；保留类型只为语义完整
 *   done          → 该段最终 done 事件到达，附带完整 content
 *   failed        → 错误事件或整体流被中断
 */
export type SectionStage =
  | 'idle'
  | 'generating'
  | 'regenerating'
  | 'checking'
  | 'done'
  | 'failed'

/** 多段流式中单段的进度快照。 */
export interface SectionProgress {
  /** 当前段所处阶段。 */
  stage: SectionStage
  /** 当前尝试次数（1=第一次写，2=第一次自纠重写…）。 */
  attempt: number
  /** 最近一条 status 事件的文案，给 UI 直接显示。 */
  message: string
  /** 最近一次校对未通过的问题列表；空数组=无问题或已通过。 */
  checkIssues: string[]
  /** 自纠历史，对应后端 done 事件里的 issues_history。done 时才有值。 */
  issuesHistory: { attempts: number; issues: string[] }[]
  /** 该段最终内容；done 时才有值，UI 直接渲染。 */
  content: Record<string, unknown> | null
  /** 该段最终标题；done 时才有值。 */
  title: string | null
}

/**
 * 多段整体状态机。
 *
 *   idle      → 没在跑
 *   running   → 流式中（主编 / 段并发 / 跨段校验 任一阶段都属于 running）
 *   done      → 5 段全部 done（或部分失败后流结束）
 *   error     → 整体级 error 事件，或网络层错误
 */
export type MultiSectionStage = 'idle' | 'running' | 'done' | 'error'

/**
 * 整体级进度快照，包含各段独立状态 + 整体状态。
 *
 * sections 用数组下标当段号（0..N-1），保证渲染时按顺序输出。
 */
export interface MultiSectionProgress {
  stage: MultiSectionStage
  /** 整体级文案（主编开始 / 跨段校验 / 全部完成 / 错误）。 */
  message: string
  /** 各段进度，sections[0]=第一段。 */
  sections: SectionProgress[]
  /** 已 done 的段数，方便 UI 显示 "3/5 完成"。 */
  doneCount: number
}

/* ============================================================
 * 对话式编辑（POST /resumes/{id}/chat）
 * 对应后端 app/schemas/chat.py + app/api/v1/chat.py
 *
 * 用户用自然语言改简历，Agent 通过工具改真实 DB，
 * LangGraph Checkpointer 按 thread_id 持久化会话状态。
 * ========================================================== */

/**
 * 对话式编辑请求体——对应后端 ChatRequest。
 *
 * thread_id：
 *   - 首次对话不传（null）→ 后端生成新 UUID，返回给前端
 *   - 后续对话原样回传上一次响应里的 thread_id
 */
export interface ChatRequest {
  message: string
  thread_id?: string | null
}

/** 对话式编辑响应体——对应后端 ChatResponse。 */
export interface ChatResponse {
  /** Agent 这一轮的回复文本。 */
  reply: string
  /** 会话 ID，下次继续对话原样回传。 */
  thread_id: string
}

/**
 * 前端本地维护的对话消息（不是后端 schema，只为 UI 渲染）。
 * role 区分用户/助手，决定气泡左右对齐。
 */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}
