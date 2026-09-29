import { FileText, Plus } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@/components/ui/Button'
import { useResumes } from '@/features/resumes/api'
import type { Resume } from '@/features/resumes/types'
import { relativeTime } from '@/lib/datetime'
import { errorMessage } from '@/lib/errors'

/**
 * 简历列表页。
 *
 * 用 useResumes() 拉数据，根据 React Query 的状态分四种渲染：
 *   1. isPending  → 首次加载，渲染骨架
 *   2. isError   → 请求失败，渲染错误提示
 *   3. 空列表     → 渲染引导卡片（保留之前的空状态设计）
 *   4. 有数据     → 渲染卡片网格
 */
export default function ResumesPage() {
  const navigate = useNavigate()
  const resumes = useResumes()

  return (
    <div className="bg-aurora">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <header className="mb-8 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">我的简历</h1>
            <p className="mt-1 text-sm text-ink-muted">
              从这里开始一份新简历，或继续编辑已有的草稿
            </p>
          </div>
          <Button onClick={() => navigate('/create')}>
            <Plus className="size-4" />
            新建简历
          </Button>
        </header>

        {/* 1. 首次加载中：骨架 */}
        {resumes.isPending && <CardSkeletonGrid />}

        {/* 2. 加载失败：错误提示 */}
        {resumes.isError && (
          <p
            role="alert"
            className="rounded-2xl bg-negative/8 px-5 py-4 text-sm text-negative"
          >
            {errorMessage(resumes.error, '简历列表加载失败，请稍后重试')}
          </p>
        )}

        {/* 3. 空列表：引导 */}
        {resumes.data?.length === 0 && <EmptyState />}

        {/* 4. 有数据：卡片网格 */}
        {resumes.data && resumes.data.length > 0 && (
          <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {resumes.data.map((resume) => (
              <ResumeCard key={resume.id} resume={resume} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/** 单张简历卡片。点击跳详情页（详情页还没做，会 fallback 到 /resumes）。 */
function ResumeCard({ resume }: { resume: Resume }) {
  return (
    <li>
      <Link
        to={`/resumes/${resume.id}`}
        className="block overflow-hidden rounded-2xl border border-line bg-surface transition-all duration-200 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card"
      >
        {/* 卡片顶部色块：accent 蓝渐变，配标题字 */}
        <div className="flex h-24 items-end bg-gradient-to-br from-accent/15 to-accent-soft px-4 py-3">
          <p className="line-clamp-2 text-[15px] font-semibold tracking-tight text-ink">
            {resume.title}
          </p>
        </div>
        {/* 卡片底部信息 */}
        <div className="flex flex-col gap-2 px-4 py-3.5">
          <p className="truncate text-sm text-ink-soft">{resume.target_position}</p>
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <StatusPill status={resume.status} />
            <span aria-hidden>·</span>
            <span>{relativeTime(resume.updated_at)}</span>
          </div>
        </div>
      </Link>
    </li>
  )
}

/** 空状态——保留原占位页的设计。 */
function EmptyState() {
  return (
    <div className="grid place-items-center rounded-3xl border border-dashed border-line-strong bg-surface/60 px-6 py-24 text-center">
      <div className="grid size-12 place-items-center rounded-2xl bg-surface-soft text-ink-muted">
        <FileText className="size-5" />
      </div>
      <p className="mt-4 text-sm font-medium text-ink-soft">还没有简历</p>
      <p className="mt-1 text-xs text-ink-muted">点右上"新建简历"开始第一份</p>
    </div>
  )
}

/** 骨架屏：三张占位卡片，避免数据没来时空白。 */
function CardSkeletonGrid() {
  return (
    <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
      {[0, 1, 2].map((key) => (
        <div key={key} className="overflow-hidden rounded-2xl border border-line bg-surface">
          <div className="h-24 animate-pulse bg-surface-soft" />
          <div className="flex flex-col gap-2 px-4 py-3.5">
            <div className="h-4 w-2/3 animate-pulse rounded bg-surface-soft" />
            <div className="h-3 w-1/3 animate-pulse rounded bg-surface-soft" />
          </div>
        </div>
      ))}
    </div>
  )
}
