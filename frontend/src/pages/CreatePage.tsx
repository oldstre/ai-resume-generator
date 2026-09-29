import { ArrowRight, ChevronLeft, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { Button } from '@/components/ui/Button'
import { PillSelect } from '@/components/ui/PillSelect'
import { TextField } from '@/components/ui/TextField'
import { useCreateResume } from '@/features/resumes/api'
import {
  DENSITY_OPTIONS,
  SECTION_COUNT_OPTIONS,
  TONE_OPTIONS,
} from '@/features/resumes/options'
import type { ContentDensity, Tone } from '@/features/resumes/types'
import { errorMessage } from '@/lib/errors'

/**
 * 创建简历表单页。
 *
 * 字段对应后端 ResumeCreate schema：
 *   title / applicant_name / target_position：必填文本
 *   tone / section_count / content_density：PillSelect 选
 *
 * 提交调 useCreateResume().mutate({...})，后端返回新创建的 Resume 对象，
 * 拿到 id 后 navigate 到 /resumes/{id}。详情页还没做，会 fallback 到 /resumes，
 * 列表里能看到这张新卡片（因为 useCreateResume 的 onSuccess 让列表缓存失效了）。
 */
export default function CreatePage() {
  const navigate = useNavigate()
  const create = useCreateResume()

  // 表单状态：每个字段一个 useState
  const [title, setTitle] = useState('')
  const [applicantName, setApplicantName] = useState('')
  const [targetPosition, setTargetPosition] = useState('')
  const [tone, setTone] = useState<Tone>('professional')
  const [sectionCount, setSectionCount] = useState(5)
  const [contentDensity, setContentDensity] = useState<ContentDensity>('medium')

  // 三个文本字段都填了才算就绪
  const ready =
    title.trim().length > 0 &&
    applicantName.trim().length > 0 &&
    targetPosition.trim().length > 0
  const busy = create.isPending

  const submit = () => {
    if (!ready || busy) return
    create.mutate(
      {
        // trim() 防止前后空白把字段长度撑过校验
        title: title.trim(),
        applicant_name: applicantName.trim(),
        target_position: targetPosition.trim(),
        tone,
        section_count: sectionCount,
        content_density: contentDensity,
      },
      {
        // 创建成功后跳到详情页（路由没匹配会自动 fallback 到 /resumes）
        onSuccess: (resume) => navigate(`/resumes/${resume.id}`),
      },
    )
  }

  return (
    <div className="bg-aurora">
      <div className="mx-auto max-w-3xl px-6 py-12">
        {/* 返回链接 */}
        <button
          type="button"
          onClick={() => navigate('/resumes')}
          className="mb-6 inline-flex items-center gap-1 text-sm text-ink-muted transition-colors hover:text-ink"
        >
          <ChevronLeft className="size-4" />
          返回我的简历
        </button>

        {/* 标题区 */}
        <h1 className="text-2xl font-semibold tracking-tight">新建简历</h1>
        <p className="mt-1 text-sm text-ink-muted">
          填写基本信息后，AI 会先生成大纲，确认后再生成正文
        </p>

        {/* 主卡片：表单输入区 + 选项行 + 提交按钮 */}
        <div className="mt-8 rounded-3xl border border-line bg-surface p-5 shadow-card">
          {/* 三个必填文本字段，2 列网格布局 */}
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="简历标题"
              placeholder="例如：张三-前端工程师-2026校招"
              maxLength={200}
              value={title}
              disabled={busy}
              onChange={(event) => setTitle(event.target.value)}
            />
            <TextField
              label="应聘者姓名"
              placeholder="例如：张三"
              maxLength={100}
              value={applicantName}
              disabled={busy}
              onChange={(event) => setApplicantName(event.target.value)}
            />
            <TextField
              label="目标岗位"
              placeholder="例如：前端工程师"
              maxLength={100}
              value={targetPosition}
              disabled={busy}
              onChange={(event) => setTargetPosition(event.target.value)}
            />
            {/* 占位让 2 列网格第二行左侧也对齐 */}
            <div className="hidden sm:block" />
          </div>

          {/* 选项行：tone / section_count / content_density */}
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line/80 pt-4">
            <PillSelect
              label="语气"
              disabled={busy}
              value={tone}
              options={TONE_OPTIONS}
              onChange={(next) => setTone(next as Tone)}
            />
            <PillSelect
              label="段落数"
              disabled={busy}
              value={sectionCount}
              options={SECTION_COUNT_OPTIONS}
              // PillSelect 的 onChange 回调收到的总是字符串，要 Number() 转回 number
              onChange={(next) => setSectionCount(Number(next))}
            />
            <PillSelect
              label="内容密度"
              disabled={busy}
              value={contentDensity}
              options={DENSITY_OPTIONS}
              onChange={(next) => setContentDensity(next as ContentDensity)}
            />

            {/* 提交按钮：靠右对齐 */}
            <Button
              size="md"
              disabled={!ready || busy}
              onClick={submit}
              className="ml-auto"
            >
              {busy ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
              {busy ? '创建中…' : '创建并生成大纲'}
            </Button>
          </div>
        </div>

        {/* loading / 错误提示 */}
        <div className="mt-4 min-h-6 text-center">
          {create.isError && (
            <p role="alert" className="text-[13px] text-negative">
              {errorMessage(create.error, '创建失败，请检查输入后重试')}
            </p>
          )}
        </div>

        {/* 提示文案 */}
        <p className="mt-6 text-center text-xs text-ink-muted">
          提交后会跳到大纲页。AI 先生成大纲，确认后再生成正文，最后可导出 PDF。
        </p>
      </div>
    </div>
  )
}
