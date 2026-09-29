import { useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, FileDown, Loader2, Pencil, RefreshCw, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { TextField } from '@/components/ui/TextField'
import { ApiError } from '@/api/client'
import {
  useConfirmOutline,
  useContents,
  useDownloadPdf,
  useMultiSectionStream,
  useOutline,
  useResume,
  useUnconfirmOutline,
  useUpdateContent,
} from '@/features/resumes/api'
import { MultiSectionProgressGrid } from '@/features/resumes/MultiSectionProgressGrid'
import { ChatPanel } from '@/features/resumes/ChatPanel'
import { DENSITY_OPTIONS, TONE_OPTIONS } from '@/features/resumes/options'
import type {
  ResumeContent,
  ResumeContentUpdate,
  ResumeOutline,
} from '@/features/resumes/types'
import { relativeTime } from '@/lib/datetime'
import { errorMessage } from '@/lib/errors'

/**
 * 简历详情页。
 *
 * 上半部分：基本信息 + 状态徽章（来自 resume 主表）
 * 下半部分：大纲区，根据 outline.status 显示不同 UI：
 *   - 没大纲（404）：draft 状态显示"生成大纲"按钮
 *   - generating：显示"大纲生成中"（轮询自动停）
 *   - draft：大纲列表 + "确认" + "重新生成"
 *   - confirmed：大纲列表 + "取消确认" + "生成正文"占位
 *   - failed：错误信息 + "重新生成"
 */
export default function ResumeDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const resume = useResume(id)
  const outline = useOutline(id)

  // 1. resume 加载中：骨架
  if (resume.isPending) {
    return (
      <div className="bg-aurora">
        <div className="mx-auto max-w-4xl px-6 py-12">
          <DetailSkeleton />
        </div>
      </div>
    )
  }

  // 2. resume 加载失败（最常见 404 简历不存在）
  if (resume.isError) {
    return (
      <div className="bg-aurora">
        <div className="mx-auto max-w-4xl px-6 py-12">
          <BackLink />
          <p
            role="alert"
            className="rounded-2xl bg-negative/8 px-5 py-4 text-sm text-negative"
          >
            {errorMessage(resume.error, '简历加载失败，请稍后重试')}
          </p>
        </div>
      </div>
    )
  }

  // 3. data 还没到，TS 兜底
  if (!resume.data) return null

  const data = resume.data

  // 把英文枚举值翻成中文 label
  const toneLabel =
    TONE_OPTIONS.find((option) => option.value === data.tone)?.label ?? data.tone
  const densityLabel =
    DENSITY_OPTIONS.find((option) => option.value === data.content_density)?.label ??
    data.content_density

  return (
    <div className="bg-aurora">
      <div className="mx-auto max-w-4xl px-6 py-12">
        <BackLink />

        {/* 标题 + 状态 */}
        <header className="mb-6 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold tracking-tight">{data.title}</h1>
            <p className="mt-1 text-sm text-ink-muted">
              {data.applicant_name} · {data.target_position}
            </p>
          </div>
          <StatusPill status={data.status} />
        </header>

        {/* 基本信息 */}
        <section className="rounded-3xl border border-line bg-surface p-6 shadow-card">
          <h2 className="mb-4 text-sm font-semibold text-ink-soft">基本信息</h2>
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
            <Field label="标题" value={data.title} />
            <Field label="应聘者" value={data.applicant_name} />
            <Field label="目标岗位" value={data.target_position} />
            <Field label="语气" value={toneLabel} />
            <Field label="段落数" value={`${data.section_count} 段`} />
            <Field label="内容密度" value={densityLabel} />
            <Field label="模板" value={data.template_id} />
            <Field label="创建时间" value={relativeTime(data.created_at)} />
            <Field label="更新时间" value={relativeTime(data.updated_at)} />
          </dl>
        </section>

        {/* 大纲区 */}
        <section className="mt-6 rounded-3xl border border-line bg-surface p-6 shadow-card">
          <h2 className="mb-4 text-sm font-semibold text-ink-soft">大纲</h2>
          <OutlineArea id={id} outline={outline} resumeStatus={data.status} />
        </section>

        {/* 正文区 */}
        <section className="mt-6 rounded-3xl border border-line bg-surface p-6 shadow-card">
          <h2 className="mb-4 text-sm font-semibold text-ink-soft">正文内容</h2>
          <ContentArea
            id={id}
            resumeStatus={data.status}
            resumeTitle={data.title}
            sectionCount={data.section_count}
          />
        </section>

        {/* 对话式编辑区：用户用自然语言改简历，Agent 调工具改真实 DB */}
        <section className="mt-6 rounded-3xl border border-line bg-surface p-6 shadow-card">
          <h2 className="mb-4 text-sm font-semibold text-ink-soft">对话式编辑</h2>
          <ChatPanel id={id} />
        </section>
      </div>
    </div>
  )
}

/**
 * 大纲区——根据 outline query 状态分支渲染。
 */
function OutlineArea({
  id,
  outline,
  resumeStatus,
}: {
  id: string
  outline: ReturnType<typeof useOutline>
  resumeStatus: string
}) {
  // 大纲加载中：骨架
  if (outline.isPending) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="flex flex-col gap-1.5">
            <div className="h-4 w-32 animate-pulse rounded bg-surface-soft" />
            <div className="h-3 w-3/4 animate-pulse rounded bg-surface-soft" />
          </div>
        ))}
      </div>
    )
  }

  // 大纲加载失败：区分 404（还没生成）和其他错误
  if (outline.isError) {
    const is404 = outline.error instanceof ApiError && outline.error.status === 404
    if (is404) {
      // 还没大纲：draft 状态显示"生成大纲"按钮，其他状态给提示
      return (
        <div className="text-center">
          <p className="text-sm text-ink-soft">尚未生成大纲</p>
          <p className="mt-1 text-xs text-ink-muted">
            {resumeStatus === 'draft'
              ? '点击下方按钮让 AI 生成大纲'
              : `当前简历状态：${resumeStatus}，建议新建简历重试`}
          </p>
          {resumeStatus === 'draft' && <GenerateOutlineButton id={id} />}
        </div>
      )
    }
    // 其他错误
    return (
      <p role="alert" className="rounded-xl bg-negative/8 px-4 py-3 text-sm text-negative">
        {errorMessage(outline.error, '大纲加载失败，请稍后重试')}
      </p>
    )
  }

  // 大纲就绪
  if (!outline.data) return null
  return <OutlineContent id={id} outline={outline.data} />
}

/** 生成大纲按钮——触发 useGenerateOutline。 */
function GenerateOutlineButton({ id }: { id: string }) {
  const generate = useGenerateOutline(id)
  return (
    <div className="mt-4 flex flex-col items-center gap-2">
      <Button
        onClick={() => generate.mutate()}
        disabled={generate.isPending}
        className="mt-2"
      >
        {generate.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Sparkles className="size-4" />
        )}
        {generate.isPending ? '提交中…' : '生成大纲'}
      </Button>
      {generate.isError && (
        <p role="alert" className="text-xs text-negative">
          {errorMessage(generate.error, '提交失败，请稍后重试')}
        </p>
      )}
    </div>
  )
}

/** 大纲已就绪的渲染：根据 outline.status 显示不同 UI。 */
function OutlineContent({ id, outline }: { id: string; outline: ResumeOutline }) {
  // 1. generating：大纲生成中（轮询自动跑着，UI 显示进度）
  if (outline.status === 'generating') {
    return (
      <div className="flex items-center gap-3 text-sm text-ink-muted">
        <Loader2 className="size-4 animate-spin text-accent" />
        <span>大纲生成中，AI 正在分析简历信息…</span>
      </div>
    )
  }

  // 2. failed：生成失败
  if (outline.status === 'failed') {
    return (
      <div className="text-center">
        <p role="alert" className="text-sm text-negative">
          {outline.error ?? '大纲生成失败'}
        </p>
        <RegenerateOutlineButton id={id} />
      </div>
    )
  }

  // 3. draft 或 confirmed：展示大纲段列表 + 操作按钮
  const isConfirmed = outline.status === 'confirmed'
  return (
    <div className="space-y-4">
      <ol className="space-y-3">
        {outline.sections.map((section, index) => (
          <li
            key={index}
            className="rounded-2xl border border-line bg-surface-soft/40 p-4"
          >
            <p className="text-[13px] font-semibold text-ink-soft">
              <span className="mr-2 text-ink-muted">{index + 1}.</span>
              {section.title}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{section.content}</p>
          </li>
        ))}
      </ol>

      {/* 操作按钮区 */}
      <div className="flex flex-wrap items-center gap-2 border-t border-line/80 pt-4">
        {isConfirmed ? (
          <UnconfirmOutlineButton id={id} revision={outline.revision} />
        ) : (
          <>
            <ConfirmOutlineButton id={id} revision={outline.revision} />
            <RegenerateOutlineButton id={id} variant="ghost" />
          </>
        )}
      </div>
    </div>
  )
}

/** 确认大纲按钮。 */
function ConfirmOutlineButton({
  id,
  revision,
}: {
  id: string
  revision: number
}) {
  const confirm = useConfirmOutline(id)
  return (
    <>
      <Button
        onClick={() => confirm.mutate(revision)}
        disabled={confirm.isPending}
      >
        {confirm.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : null}
        {confirm.isPending ? '确认中…' : '确认大纲'}
      </Button>
      {confirm.isError && (
        <p role="alert" className="w-full text-xs text-negative">
          {errorMessage(confirm.error, '确认失败，请稍后重试')}
        </p>
      )}
    </>
  )
}

/** 取消确认按钮。 */
function UnconfirmOutlineButton({
  id,
  revision,
}: {
  id: string
  revision: number
}) {
  const unconfirm = useUnconfirmOutline(id)
  return (
    <>
      <Button
        variant="ghost"
        onClick={() => unconfirm.mutate(revision)}
        disabled={unconfirm.isPending}
      >
        {unconfirm.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : null}
        {unconfirm.isPending ? '取消中…' : '取消确认'}
      </Button>
      {unconfirm.isError && (
        <p role="alert" className="w-full text-xs text-negative">
          {errorMessage(unconfirm.error, '取消失败，请稍后重试')}
        </p>
      )}
    </>
  )
}

/** 重新生成大纲按钮。 */
function RegenerateOutlineButton({
  id,
  variant = 'primary',
}: {
  id: string
  variant?: 'primary' | 'ghost'
}) {
  const generate = useGenerateOutline(id)
  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        variant={variant}
        onClick={() => generate.mutate()}
        disabled={generate.isPending}
      >
        {generate.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <RefreshCw className="size-4" />
        )}
        {generate.isPending ? '提交中…' : '重新生成'}
      </Button>
      {generate.isError && (
        <p role="alert" className="text-xs text-negative">
          {errorMessage(generate.error, '提交失败，请稍后重试')}
        </p>
      )}
    </div>
  )
}

/** 返回简历列表的链接。 */
function BackLink() {
  return (
    <Link
      to="/resumes"
      className="mb-6 inline-flex items-center gap-1 text-sm text-ink-muted transition-colors hover:text-ink"
    >
      <ChevronLeft className="size-4" />
      返回我的简历
    </Link>
  )
}

/** 单个 label / value 字段对，<dl> 的子项。 */
function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className="text-sm text-ink">{value}</dd>
    </div>
  )
}

/** 骨架屏。 */
function DetailSkeleton() {
  return (
    <div>
      <div className="mb-6 h-5 w-32 animate-pulse rounded bg-surface-soft" />
      <div className="mb-6 h-7 w-2/3 animate-pulse rounded bg-surface-soft" />
      <div className="rounded-3xl border border-line bg-surface p-6">
        <div className="mb-4 h-4 w-24 animate-pulse rounded bg-surface-soft" />
        <div className="grid gap-4 sm:grid-cols-2">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="flex flex-col gap-1.5">
              <div className="h-3 w-16 animate-pulse rounded bg-surface-soft" />
              <div className="h-4 w-32 animate-pulse rounded bg-surface-soft" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ============================================================
 * 正文（Content）区相关组件
 * ========================================================== */

/**
 * 正文区——根据 contents 查询结果 + resume.status 分支渲染。
 *
 * 流程：
 *   1. contents 为空：
 *      - outline_ready → 显示「一键重新生成全部」按钮（首次生成入口）
 *      - 其他状态 → 文案提示
 *   2. contents 有：
 *      - 顶部操作条显示「导出 PDF」+ 「一键重新生成全部」
 *      - 段卡片列表（每段支持编辑）
 *
 * 全部就绪后会自动刷一次简历详情，让 resume.status 从 generating 变 ready，
 * 这样「导出 PDF」按钮能正确显示。
 */
function ContentArea({
  id,
  resumeStatus,
  resumeTitle,
  sectionCount,
}: {
  id: string
  resumeStatus: string
  resumeTitle: string
  sectionCount: number
}) {
  const contents = useContents(id)
  const queryClient = useQueryClient()
  // 防止 useEffect 反复刷详情的标志位
  const refreshedRef = useRef(false)

  // contents 全部就绪时，刷一次简历详情拿最新 status（多段流跑完后 hook 会 invalidate 缓存）
  useEffect(() => {
    const items = contents.data
    if (!items || items.length === 0) {
      refreshedRef.current = false
      return
    }
    const allDone = items.every(
      (item) => item.status === 'ready' || item.status === 'failed',
    )
    if (allDone && !refreshedRef.current) {
      refreshedRef.current = true
      void queryClient.invalidateQueries({ queryKey: ['resumes', id] })
    }
    if (!allDone) {
      refreshedRef.current = false
    }
  }, [contents.data, id, queryClient])

  // loading：骨架
  if (contents.isPending) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <div key={index} className="flex flex-col gap-1.5">
            <div className="h-4 w-32 animate-pulse rounded bg-surface-soft" />
            <div className="h-3 w-3/4 animate-pulse rounded bg-surface-soft" />
          </div>
        ))}
      </div>
    )
  }

  // 加载失败
  if (contents.isError) {
    return (
      <p role="alert" className="rounded-xl bg-negative/8 px-4 py-3 text-sm text-negative">
        {errorMessage(contents.error, '正文加载失败，请稍后重试')}
      </p>
    )
  }

  const items = contents.data ?? []

  // 还没生成过正文：
  // - outline_ready：显示「一键生成全部内容」按钮（首次生成入口，文案动态切换）
  // - 其他状态：提示先确认大纲
  if (items.length === 0) {
    if (resumeStatus === 'outline_ready') {
      return (
        <RegenerateAllSection id={id} sectionCount={sectionCount} isFirstTime />
      )
    }
    return (
      <p className="text-sm text-ink-soft">请先确认大纲后再生成正文内容。</p>
    )
  }

  return (
    <div className="space-y-4">
      {/* 顶部操作条：就绪显示导出 PDF + 一键重新生成 */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line/80 pb-3">
        {resumeStatus === 'ready' ? (
          <DownloadPdfButton id={id} fallbackName={resumeTitle} />
        ) : (
          <span className="text-sm text-ink-muted">正文已生成</span>
        )}
        <RegenerateAllSection id={id} sectionCount={items.length} />
      </div>

      {/* 段卡片列表 */}
      <div className="space-y-3">
        {items.map((item) => (
          <ContentCard key={item.id} id={id} item={item} />
        ))}
      </div>
    </div>
  )
}

/**
 * "一键重新生成全部"按钮 + 多段进度网格容器。
 *
 * 按钮和进度网格共用同一个 useMultiSectionStream 实例（state 在 hook 内部），
 * 点击按钮 → start() → SSE 事件流驱动 progress 更新 → 网格实时刷新。
 *
 * 后端跑 LangGraph 多节点图：主编 → 5 段并发撰写 + 自纠 → 跨段校验 → all_done。
 * 进度网格只在跑过（非 idle）时才出现，按钮始终在顶部操作条里。
 *
 * 这里把按钮和网格放在同一个组件里，是为了让 useMultiSectionStream 只调用一次——
 * React 的 hook 状态是按调用位置绑定的，分两个组件就分两份 state 了。
 *
 * 结构：
 *   <Button />            ← 顶部操作条里的"一键重新生成全部"按钮
 *   {progress !== idle && <MultiSectionProgressGrid />}  ← 跑起来后出现的网格
 *   {error && <错误文案>}
 */
function RegenerateAllSection({
  id,
  sectionCount,
  isFirstTime = false,
}: {
  id: string
  sectionCount: number
  isFirstTime?: boolean
}) {
  const stream = useMultiSectionStream(id, sectionCount)
  const progress = stream.progress

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => stream.start()}
        disabled={stream.isRunning}
      >
        {stream.isRunning ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Sparkles className="size-3.5" />
        )}
        {stream.isRunning
          ? '并发生成中…'
          : isFirstTime
            ? '一键生成全部内容'
            : '一键重新生成全部'}
      </Button>

      {/* 网格只在跑过（非 idle）时渲染，idle 时整块消失 */}
      {progress.stage !== 'idle' && (
        <div className="mt-3 rounded-3xl border border-line bg-surface-soft/30 p-4">
          <MultiSectionProgressGrid progress={progress} />
          {progress.stage === 'error' && (
            <p role="alert" className="mt-2 text-xs text-negative">
              {progress.message || '生成失败'}
            </p>
          )}
        </div>
      )}
    </>
  )
}

/** 导出 PDF 按钮——拉二进制流并触发下载。 */
function DownloadPdfButton({
  id,
  fallbackName,
}: {
  id: string
  fallbackName: string
}) {
  const download = useDownloadPdf(id)
  return (
    <div className="flex items-center gap-2">
      <Button
        variant="accent"
        onClick={() => download.mutate(fallbackName)}
        disabled={download.isPending}
      >
        {download.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <FileDown className="size-4" />
        )}
        {download.isPending ? '导出中…' : '导出 PDF'}
      </Button>
      {download.isError && (
        <p role="alert" className="text-xs text-negative">
          {errorMessage(download.error, '导出失败，请稍后重试')}
        </p>
      )}
    </div>
  )
}

/** 单段内容卡片：标题 + 正文渲染 + 编辑按钮。 */
function ContentCard({ id, item }: { id: string; item: ResumeContent }) {
  const [editing, setEditing] = useState(false)

  return (
    <div className="rounded-2xl border border-line bg-surface-soft/40 p-4">
      {/* 卡片头：序号 + 标题 + 状态 */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink-soft">
            <span className="mr-2 text-ink-muted">{item.section_index + 1}.</span>
            {item.title}
          </p>
          <p className="mt-0.5 text-xs text-ink-muted">
            v{item.revision} · {item.status}
          </p>
        </div>
        <ContentStatusBadge status={item.status} />
      </div>

      {/* 卡片正文 */}
      <div className="mt-3">
        {item.status === 'generating' || item.status === 'pending' ? (
          <div className="space-y-2">
            <div className="h-3 w-full animate-pulse rounded bg-surface-soft" />
            <div className="h-3 w-5/6 animate-pulse rounded bg-surface-soft" />
          </div>
        ) : item.status === 'failed' ? (
          <p role="alert" className="text-sm text-negative">
            {item.error ?? '该段生成失败'}
          </p>
        ) : (
          renderContent(item.content)
        )}
      </div>

      {/* 卡片操作按钮：就绪才允许编辑（重新生成走顶部"一键重新生成全部"） */}
      {item.status === 'ready' && (
        <div className="mt-3 flex items-center justify-end border-t border-line/60 pt-2">
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
            <Pencil className="size-3.5" />
            编辑
          </Button>
        </div>
      )}

      {editing && (
        <EditContentDialog
          id={id}
          item={item}
          onClose={() => setEditing(false)}
        />
      )}
    </div>
  )
}

/** 段状态小徽章。 */
function ContentStatusBadge({ status }: { status: ResumeContent['status'] }) {
  const map: Record<ResumeContent['status'], { label: string; className: string }> = {
    pending: {
      label: '等待中',
      className: 'bg-surface-soft text-ink-muted',
    },
    generating: {
      label: '生成中',
      className: 'bg-accent-soft text-accent',
    },
    ready: {
      label: '就绪',
      className: 'bg-positive/10 text-positive',
    },
    failed: {
      label: '失败',
      className: 'bg-negative/10 text-negative',
    },
  }
  const cfg = map[status]
  return (
    <span
      className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${cfg.className}`}
    >
      {cfg.label}
    </span>
  )
}

/**
 * 编辑段内容对话框：改标题 + 直接编辑 content JSON（覆盖式）。
 *
 * content 是 JSONB，形状随段类型变（items 数组或 paragraphs 数组），
 * 用 JSON 文本框统一处理，保存时 JSON.parse 再 PATCH。
 */
function EditContentDialog({
  id,
  item,
  onClose,
}: {
  id: string
  item: ResumeContent
  onClose: () => void
}) {
  const update = useUpdateContent(id, item.section_index)
  const [title, setTitle] = useState(item.title)
  const [contentText, setContentText] = useState(
    item.content ? JSON.stringify(item.content, null, 2) : '',
  )
  const [parseError, setParseError] = useState<string | null>(null)

  const handleSave = () => {
    let parsed: Record<string, unknown> | null
    const trimmed = contentText.trim()
    if (!trimmed) {
      parsed = null
    } else {
      try {
        const value = JSON.parse(trimmed)
        if (typeof value !== 'object' || value === null || Array.isArray(value)) {
          setParseError('内容必须是 JSON 对象，如 {"items": [...]} 或 {"paragraphs": [...]}')
          return
        }
        parsed = value as Record<string, unknown>
      } catch (error) {
        setParseError(`JSON 格式错误：${(error as Error).message}`)
        return
      }
    }

    const payload: ResumeContentUpdate = { content: parsed, title }
    update.mutate(payload, {
      onSuccess: () => onClose(),
    })
  }

  return (
    <Dialog
      title="编辑段内容"
      description="可改标题或直接编辑正文 JSON（覆盖式保存）"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={update.isPending}>
            {update.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : null}
            {update.isPending ? '保存中…' : '保存'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField
          label="段标题"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <label className="text-[13px] font-medium text-ink-soft">
            正文（JSON 对象）
          </label>
          <textarea
            value={contentText}
            onChange={(event) => {
              setContentText(event.target.value)
              setParseError(null)
            }}
            rows={12}
            spellCheck={false}
            className="scrollbar-slim w-full rounded-xl border border-line bg-surface px-3 py-2 font-mono text-xs text-ink focus:border-accent focus:outline-none"
          />
          <p className="text-xs text-ink-muted">
            支持两种形状：{'{ "items": [...] }'}（技能/项目/工作/教育段）或
            {' { "paragraphs": ["...", "..."] }'}（自我评价段）。
          </p>
          {parseError && (
            <p role="alert" className="text-xs text-negative">
              {parseError}
            </p>
          )}
          {update.isError && (
            <p role="alert" className="text-xs text-negative">
              {errorMessage(update.error, '保存失败，请稍后重试')}
            </p>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/**
 * 渲染段正文。按 content JSONB 的两种形状分支：
 *   1. content.paragraphs 是字符串数组 → 逐段渲染
 *   2. content.items 是数组 → 每项渲染成键值对卡片
 *   3. 其他形状 → 原样 JSON 展示，兜底
 */
function renderContent(content: Record<string, unknown> | null) {
  if (!content) {
    return <p className="text-sm text-ink-muted">（无内容）</p>
  }

  // paragraphs 形状：自我评价 / 职业概述
  if (Array.isArray(content.paragraphs)) {
    const paragraphs = (content.paragraphs as unknown[]).filter(
      (item): item is string => typeof item === 'string',
    )
    if (paragraphs.length > 0) {
      return (
        <div className="space-y-2">
          {paragraphs.map((text, index) => (
            <p key={index} className="text-sm leading-relaxed text-ink">
              {text}
            </p>
          ))}
        </div>
      )
    }
  }

  // items 形状：技能 / 项目 / 工作 / 教育
  if (Array.isArray(content.items)) {
    const items = (content.items as unknown[]).filter(
      (item): item is Record<string, unknown> =>
        typeof item === 'object' && item !== null && !Array.isArray(item),
    )
    if (items.length > 0) {
      return (
        <ul className="space-y-2">
          {items.map((item, index) => (
            <li
              key={index}
              className="rounded-xl border border-line bg-surface p-3"
            >
              <dl className="flex flex-col gap-1">
                {Object.entries(item).map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-sm">
                    <dt className="shrink-0 text-ink-muted">{key}：</dt>
                    <dd className="break-words text-ink">
                      {Array.isArray(value)
                        ? value.join('、')
                        : String(value ?? '')}
                    </dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      )
    }
  }

  // 兜底：原样 JSON
  return (
    <pre className="scrollbar-slim overflow-x-auto rounded-xl bg-surface-soft/60 p-3 text-xs text-ink-soft">
      {JSON.stringify(content, null, 2)}
    </pre>
  )
}
