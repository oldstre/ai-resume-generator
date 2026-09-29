import { CheckCircle2, Loader2, AlertCircle, Clock } from 'lucide-react'
import type { SectionProgress, MultiSectionProgress } from './types'

/**
 * 多段并发生成进度网格。
 *
 * 5 个段卡片横向（小屏纵向）排开，每张卡片实时显示：
 *   - 段号 + 当前阶段（等待中 / 生成中 / 自纠中 / 完成 / 失败）
 *   - 当前尝试次数（attempt 1=初稿，2+=自纠）
 *   - 最近一条状态文案
 *   - 校对未通过的问题列表（点开看具体原因）
 *   - 完成时直接渲染该段 content（paragraphs / items 两种形状）
 *
 * 父组件把 useMultiSectionStream 的 progress 透传进来即可：
 *   <MultiSectionProgressGrid progress={stream.progress} />
 *
 * 当 progress.stage === 'idle' 时整体不渲染（父组件控制是否显示）。
 */
export function MultiSectionProgressGrid({
  progress,
}: {
  progress: MultiSectionProgress
}) {
  return (
    <div className="space-y-3">
      {/* 顶部状态条：整体文案 + 完成进度计数 */}
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-line bg-surface-soft/60 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm text-ink-soft">
          <OverallIcon stage={progress.stage} />
          <span>{progress.message || '准备中…'}</span>
        </div>
        <span className="shrink-0 text-xs font-medium text-ink-muted">
          {progress.doneCount}/{progress.sections.length} 完成
        </span>
      </div>

      {/* 段卡片网格：大屏 2 列，小屏 1 列 */}
      <div className="grid gap-3 sm:grid-cols-2">
        {progress.sections.map((section, idx) => (
          <SectionCard key={idx} index={idx} section={section} />
        ))}
      </div>
    </div>
  )
}

/** 整体阶段对应的图标。 */
function OverallIcon({ stage }: { stage: MultiSectionProgress['stage'] }) {
  if (stage === 'done') {
    return <CheckCircle2 className="size-4 text-positive" />
  }
  if (stage === 'error') {
    return <AlertCircle className="size-4 text-negative" />
  }
  if (stage === 'running') {
    return <Loader2 className="size-4 animate-spin text-accent" />
  }
  return <Clock className="size-4 text-ink-muted" />
}

/** 单段卡片：阶段图标 + 段号 + 状态文案 + 问题列表 / 段内容预览。 */
function SectionCard({
  index,
  section,
}: {
  index: number
  section: SectionProgress
}) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
      {/* 卡片头：段号 + 阶段图标 + 状态文案 */}
      <div className="flex items-start gap-2.5">
        <SectionStageIcon stage={section.stage} />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-ink-soft">
            <span className="mr-1.5 text-ink-muted">段 {index + 1}</span>
            {section.title ?? '…'}
          </p>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {section.message}
          </p>
        </div>
        {/* 右上角：尝试次数徽章（只在 attempt > 0 时显示） */}
        {section.attempt > 0 && (
          <span className="shrink-0 rounded-full bg-surface-soft px-2 py-0.5 text-[11px] text-ink-muted">
            第 {section.attempt} 次
          </span>
        )}
      </div>

      {/* 校对问题列表（未通过时显示） */}
      {section.checkIssues.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-xl bg-negative/5 px-3 py-2 text-xs text-negative">
          {section.checkIssues.map((issue, i) => (
            <li key={i} className="flex gap-1">
              <span className="shrink-0">·</span>
              <span className="break-words">{issue}</span>
            </li>
          ))}
        </ul>
      )}

      {/* 段最终内容预览（done 时显示） */}
      {section.stage === 'done' && section.content && (
        <div className="mt-3 border-t border-line/60 pt-2.5">
          <ContentPreview content={section.content} />
        </div>
      )}
    </div>
  )
}

/** 段阶段对应的图标 + 配色。 */
function SectionStageIcon({ stage }: { stage: SectionProgress['stage'] }) {
  switch (stage) {
    case 'done':
      return <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-positive" />
    case 'failed':
      return <AlertCircle className="mt-0.5 size-4 shrink-0 text-negative" />
    case 'generating':
    case 'regenerating':
    case 'checking':
      return <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-accent" />
    default:
      return <Clock className="mt-0.5 size-4 shrink-0 text-ink-muted" />
  }
}

/**
 * 段内容预览——只在卡片里做最简渲染，让用户能瞥见生成结果。
 * 完整渲染走 ResumeDetailPage 里 ContentCard 的 renderContent（同样形状）。
 *
 * 形状分支：
 *   1. content.paragraphs: string[] → 逐段 1 行文字
 *   2. content.items: object[]      → 每项 1 行，用 name 字段做标题
 *   3. 其他形状                    → 原样 JSON 展示兜底
 */
function ContentPreview({ content }: { content: Record<string, unknown> }) {
  if (Array.isArray(content.paragraphs)) {
    const paragraphs = (content.paragraphs as unknown[]).filter(
      (item): item is string => typeof item === 'string',
    )
    if (paragraphs.length > 0) {
      return (
        <div className="space-y-1">
          {paragraphs.slice(0, 2).map((text, i) => (
            <p key={i} className="line-clamp-2 text-xs leading-relaxed text-ink-soft">
              {text}
            </p>
          ))}
          {paragraphs.length > 2 && (
            <p className="text-[11px] text-ink-muted">
              +{paragraphs.length - 2} 段…
            </p>
          )}
        </div>
      )
    }
  }

  if (Array.isArray(content.items)) {
    const items = (content.items as unknown[]).filter(
      (item): item is Record<string, unknown> =>
        typeof item === 'object' && item !== null && !Array.isArray(item),
    )
    if (items.length > 0) {
      return (
        <ul className="space-y-1">
          {items.slice(0, 2).map((item, i) => (
            <li key={i} className="truncate text-xs text-ink-soft">
              · {String(item.name ?? Object.values(item)[0] ?? '')}
            </li>
          ))}
          {items.length > 2 && (
            <li className="text-[11px] text-ink-muted">+{items.length - 2} 项…</li>
          )}
        </ul>
      )
    }
  }

  // 兜底
  return (
    <pre className="scrollbar-slim overflow-x-auto rounded-xl bg-surface-soft/60 p-2 text-[11px] text-ink-muted">
      {JSON.stringify(content, null, 2)}
    </pre>
  )
}
