import { cn } from '@/lib/utils'

/**
 * 简历状态映射。后端 Resume.status 字段实际只返回 4 个值：
 *   draft / outline_ready / generating / ready
 * （见 backend/app/schemas/resumes.py 的 ResumeStatus Literal）
 *
 * 这里额外列了 outline_pending / outline_confirmed / failed 三个扩展值，
 * 是为了兼容 Outline 自身的 status 字段（generating / draft / confirmed / failed）
 * 以及未来扩展。找到匹配的就用，找不到走 fallback 显示原始字符串。
 */
type Status =
  | 'draft'
  | 'outline_pending'
  | 'outline_confirmed'
  | 'outline_ready'
  | 'generating'
  | 'ready'
  | 'failed'

const STATUS: Record<Status, { label: string; className: string; pulse?: boolean }> = {
  draft: { label: '草稿', className: 'bg-surface-soft text-ink-muted' },
  outline_pending: { label: '大纲生成中', className: 'bg-accent-soft text-accent', pulse: true },
  outline_confirmed: { label: '大纲已确认', className: 'bg-accent-soft text-accent' },
  // outline_ready 是后端 Resume.status 实际返回的值，等同于"大纲已确认，可生成正文"
  outline_ready: { label: '大纲已确认', className: 'bg-accent-soft text-accent' },
  generating: { label: '内容生成中', className: 'bg-accent-soft text-accent', pulse: true },
  ready: { label: '可导出', className: 'bg-positive/10 text-positive' },
  failed: { label: '生成失败', className: 'bg-negative/10 text-negative' },
}

export function StatusPill({ status, className }: { status: Status; className?: string }) {
  const { label, className: tone, pulse } = STATUS[status] ?? {
    label: status,
    className: 'bg-surface-soft text-ink-muted',
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium',
        tone,
        className,
      )}
    >
      {pulse && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {label}
    </span>
  )
}
