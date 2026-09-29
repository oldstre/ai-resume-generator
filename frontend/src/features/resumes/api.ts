/**
 * 简历数据 hooks——React Query 封装。
 *
 * 5 个 hook 对应后端 5 个 REST 接口：
 *   useResumes        → GET    /resumes
 *   useResume(id)     → GET    /resumes/{id}
 *   useCreateResume   → POST   /resumes
 *   useUpdateResume   → PATCH  /resumes/{id}
 *   useDeleteResume   → DELETE /resumes/{id}
 *
 * request<T>() 在 src/api/client.ts，会自动拼上 '/api/v1' 前缀、注入 token、解析 JSON。
 *
 * queryKey 是 React Query 的缓存键：
 *   ['resumes']         → 列表缓存
 *   ['resumes', id]     → 单条缓存
 * 同 key 的 useQuery 多次调用只发一次请求，从缓存返回。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { API_PREFIX, request, requestBinary } from '@/api/client'
import { tokenStore } from '@/features/auth/token'
import { filenameFromDisposition, saveBlob } from '@/lib/download'
import type {
  ChatRequest,
  ChatResponse,
  OutlineGenerateResponse,
  Resume,
  ResumeContent,
  ResumeContentUpdate,
  ResumeCreate,
  ResumeOutline,
  ResumeOutlineRevisionRequest,
  ResumeOutlineUpsert,
  ResumeUpdate,
  SSEEvent,
  SSEDoneData,
  SSECheckData,
  SSEStatusData,
  SectionProgress,
  MultiSectionProgress,
} from './types'

/** 列表缓存键。 */
const listKey = ['resumes'] as const
/** 单条缓存键。注意返回的是新数组，不能用同一个引用。 */
const detailKey = (id: string) => ['resumes', id] as const
/** 大纲缓存键。跟简历详情 key 区分，因为 GET 路径不同。 */
const outlineKey = (id: string) => ['resumes', id, 'outline'] as const
/** 段内容列表缓存键。 */
const contentsKey = (id: string) => ['resumes', id, 'contents'] as const

/** 拉简历列表。组件里用 const resumes = useResumes()。 */
export function useResumes() {
  return useQuery({
    queryKey: listKey,
    queryFn: () => request<Resume[]>('/resumes'),
  })
}

/** 拉单个简历。id 变化会自动重取。 */
export function useResume(id: string) {
  return useQuery({
    queryKey: detailKey(id),
    queryFn: () => request<Resume>(`/resumes/${id}`),
    // 详情页默认只在有 id 时启用，避免空字符串请求
    enabled: Boolean(id),
  })
}

/** 创建简历。成功后让列表缓存过期，下次读列表会自动重拉。 */
export function useCreateResume() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ResumeCreate) =>
      request<Resume>('/resumes', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      // 新建后列表肯定变了，让 ['resumes'] 失效触发刷新
      void queryClient.invalidateQueries({ queryKey: listKey })
    },
  })
}

/** 改简历。成功后同时更新单条缓存和列表缓存。 */
export function useUpdateResume(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ResumeUpdate) =>
      request<Resume>(`/resumes/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: (updated) => {
      // 用 setQueryData 把新数据直接塞进单条缓存，省一次请求
      queryClient.setQueryData(detailKey(id), updated)
      // 列表里的对应项也变了，让列表过期
      void queryClient.invalidateQueries({ queryKey: listKey })
    },
  })
}

/** 删简历。成功后让列表过期，下次读会重拉。 */
export function useDeleteResume() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => request<void>(`/resumes/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: listKey })
    },
  })
}

/* ============================================================
 * 大纲（Outline）相关 hooks
 * ========================================================== */

/**
 * 拉大纲。status='generating' 时每 2 秒自动重拉（轮询），
 * 状态变化后停止轮询——避免无意义请求。
 */
export function useOutline(id: string) {
  return useQuery({
    queryKey: outlineKey(id),
    queryFn: () => request<ResumeOutline>(`/resumes/${id}/outline`),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      // React Query v5：query.state.data 是当前缓存数据
      return query.state.data?.status === 'generating' ? 2000 : false
    },
    retry: false, // 大纲不存在（404）就别重试，组件层处理"还没生成大纲"分支
  })
}

/**
 * 触发生成大纲。后端 202 立即返回 {job_id, status}，
 * 真正的 LLM 调用在 ARQ worker 里慢慢跑。
 *
 * 成功后我们手动把 outline 缓存 set 成一个"占位"大纲对象（status='generating'），
 * useOutline 的轮询会立刻启动，前端立刻显示"生成中"。
 */
export function useGenerateOutline(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      request<OutlineGenerateResponse>(`/resumes/${id}/outline/generate`, {
        method: 'POST',
      }),
    onSuccess: () => {
      // 让 outline 缓存失效，触发 useOutline 立刻重拉，看到 status=generating
      void queryClient.invalidateQueries({ queryKey: outlineKey(id) })
    },
  })
}

/**
 * 确认大纲：draft → confirmed。后端同时把 resume.status 改成 'outline_ready'。
 *
 * 入参是 { revison: <number> }——注意这个字段是后端 schema 的拼写错误
 * （少个 i），保留错误拼写让后端校验通过，见 types.ts 的注释。
 */
export function useConfirmOutline(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (revision: number) =>
      request<ResumeOutline>(`/resumes/${id}/outline/confirm`, {
        method: 'POST',
        body: JSON.stringify({ revision } satisfies ResumeOutlineRevisionRequest),
      }),
    onSuccess: (outline) => {
      // 更新 outline 缓存
      queryClient.setQueryData(outlineKey(id), outline)
      // 简历详情也要刷新（status 从 draft → outline_ready）
      void queryClient.invalidateQueries({ queryKey: detailKey(id) })
      void queryClient.invalidateQueries({ queryKey: listKey })
    },
  })
}

/** 取消确认：confirmed → draft。同样需要 revision 校验。 */
export function useUnconfirmOutline(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (revision: number) =>
      request<ResumeOutline>(`/resumes/${id}/outline/unconfirm`, {
        method: 'POST',
        body: JSON.stringify({ revision } satisfies ResumeOutlineRevisionRequest),
      }),
    onSuccess: (outline) => {
      queryClient.setQueryData(outlineKey(id), outline)
      void queryClient.invalidateQueries({ queryKey: detailKey(id) })
      void queryClient.invalidateQueries({ queryKey: listKey })
    },
  })
}

/** 手动改大纲：PUT 整体替换 sections。 */
export function useUpdateOutline(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ResumeOutlineUpsert) =>
      request<ResumeOutline>(`/resumes/${id}/outline`, {
        method: 'PUT',
        body: JSON.stringify(body),
      }),
    onSuccess: (outline) => {
      queryClient.setQueryData(outlineKey(id), outline)
    },
  })
}

/* ============================================================
 * 段内容（Content）相关 hooks
 * ========================================================== */

/**
 * 拉所有段内容。
 *
 * 不再做 2 秒轮询——以前轮询是给 ARQ worker 后台跑 generate_all_contents_task 用的，
 * 现在内容生成走 SSE 流式端点（useMultiSectionStream），实时进度靠事件流推，
 * 段内容落库后 hook 的 done 分支会同步写 React Query 缓存，UI 自动刷新。
 *
 * 注意：后端没生成过内容时这里返回空数组（不是 404），
 * 组件层据此判断"该显示首次生成按钮"。
 */
export function useContents(id: string) {
  return useQuery({
    queryKey: contentsKey(id),
    queryFn: () => request<ResumeContent[]>(`/resumes/${id}/contents`),
    enabled: Boolean(id),
    retry: false,
  })
}

/**
 * 手动编辑单段（不走 LLM）。入参是 { content?, title? }——只传想改的字段。
 * 成功后更新 contents 缓存对应位置。
 */
export function useUpdateContent(id: string, sectionIndex: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ResumeContentUpdate) =>
      request<ResumeContent>(`/resumes/${id}/contents/${sectionIndex}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(contentsKey(id), (old?: ResumeContent[]) =>
        old?.map((item) =>
          item.section_index === sectionIndex ? updated : item,
        ) ?? [updated],
      )
    },
  })
}

/**
 * 导出 PDF。用 requestBinary 拉二进制流，再 saveBlob 触发浏览器下载。
 * 文件名优先取 Content-Disposition，没有就用简历标题+".pdf"。
 *
 * 调用方传 fallbackName（一般是简历标题），mutation 内部不存缓存。
 */
export function useDownloadPdf(id: string) {
  return useMutation({
    mutationFn: async (fallbackName: string) => {
      const response = await requestBinary(`/resumes/${id}/pdf`)
      const blob = await response.blob()
      const filename = filenameFromDisposition(
        response.headers.get('Content-Disposition'),
        `${fallbackName}.pdf`,
      )
      saveBlob(blob, filename)
    },
  })
}

/* ============================================================
 * 多段并发流式重新生成（POST /contents/regenerate/stream）
 *
 * 后端跑 LangGraph 多节点图，5 段并发撰写 + 自纠 + 跨段校验，
 * 通过 SSE 把进度事件流推给前端。前端按 section_index 聚合到 5 段状态。
 * ========================================================== */

/**
 * 创建一份单段的初始进度快照。
 * 多段流开始前，5 段都是这个 idle 状态。
 */
function makeIdleSection(): SectionProgress {
  return {
    stage: 'idle',
    attempt: 0,
    message: '等待中…',
    checkIssues: [],
    issuesHistory: [],
    content: null,
    title: null,
  }
}

/**
 * 创建一份多段整体的初始进度快照。
 * sectionCount 来自简历详情（resume.section_count）。
 */
function makeInitialProgress(sectionCount: number): MultiSectionProgress {
  return {
    stage: 'idle',
    message: '',
    sections: Array.from({ length: sectionCount }, makeIdleSection),
    doneCount: 0,
  }
}

/**
 * 多段并发流式重新生成 hook。
 *
 * 端点：POST /api/v1/resumes/{id}/contents/regenerate/stream
 *
 * 后端跑 LangGraph 多节点图，5 段并发撰写 + 自纠 + 跨段校验，
 * 通过 SSE 把进度事件流推给前端。前端按 section_index 聚合到 5 段状态。
 *
 * 关键设计：
 *   - 一次跑 5 段并发，需要按 section_index 把事件分发到对应段状态
 *   - 整体级事件（section_index=null）单独维护：主编开始 / 跨段校验 / 全部完成
 *   - 每段独立 done，所有段 done 后整体才算 done
 *
 * 用法：
 *   const stream = useMultiSectionStream(id, sectionCount)
 *   <Button onClick={() => stream.start()} disabled={stream.isRunning}>
 *     一键重新生成全部
 *   </Button>
 *   <MultiSectionProgressGrid progress={stream.progress} />
 */
export function useMultiSectionStream(id: string, sectionCount: number) {
  const queryClient = useQueryClient()
  // setProgress 全程用 functional updater（prev => ...），
  // 不需要额外 ref 持有最新值；state 本身驱动渲染。
  const [progress, setProgress] = useState<MultiSectionProgress>(() =>
    makeInitialProgress(sectionCount),
  )
  const controllerRef = useRef<AbortController | null>(null)

  // sectionCount 变了（比如换了简历）→ 重建初始状态
  useEffect(() => {
    setProgress(makeInitialProgress(sectionCount))
  }, [sectionCount])

  // 组件卸载时中止进行中的请求，避免 setState on unmounted
  useEffect(() => {
    return () => {
      controllerRef.current?.abort()
    }
  }, [])

  /**
   * 把单段进度整体替换为 updater 返回的新值。
   * helper：通过 setProgress 拷贝更新 sections[idx]，避免读旧 state。
   */
  const patchSection = (idx: number, updater: (s: SectionProgress) => SectionProgress) => {
    setProgress((prev) => {
      const nextSections = prev.sections.slice()
      nextSections[idx] = updater(nextSections[idx] ?? makeIdleSection())
      // 重新算 doneCount：stage==='done' 的段数
      const doneCount = nextSections.filter((s) => s.stage === 'done').length
      return { ...prev, sections: nextSections, doneCount }
    })
  }

  /**
   * 处理一条 SSE 事件，按 section_index 分发。
   * section_index === null 表示整体级事件，单独更新 progress.message 和 stage。
   */
  const handleEvent = (event: SSEEvent) => {
    switch (event.event) {
      case 'status': {
        const data = event.data as SSEStatusData
        const idx = data.section_index
        if (idx === null || idx === undefined) {
          // 整体级事件：主编开始 / 全部完成 / 整体失败
          setProgress((prev) => {
            const stage: MultiSectionProgress['stage'] =
              data.stage === 'failed'
                ? 'error'
                : data.stage === 'done'
                  ? 'done'
                  : 'running'
            return { ...prev, stage, message: data.message }
          })
          return
        }
        // 段级事件：generating / regenerating / 段失败
        patchSection(idx, (s) => ({
          ...s,
          stage:
            data.stage === 'failed'
              ? 'failed'
              : data.stage === 'regenerating'
                ? 'regenerating'
                : 'generating',
          attempt: data.attempt,
          message: data.message,
        }))
        return
      }
      case 'tool':
      case 'tool_done':
        // 多段端点目前不推工具事件；保留 case 以便后续扩展
        return
      case 'check': {
        const data = event.data as SSECheckData
        const idx = data.section_index
        if (idx === null || idx === undefined) {
          // 跨段校验结果：整体级展示
          setProgress((prev) => ({
            ...prev,
            message: data.passed ? '跨段一致性校验通过' : '跨段校验未通过',
          }))
          return
        }
        // 段校对结果：passed=true 留个简短文案；passed=false 把问题列表塞进段状态
        patchSection(idx, (s) => ({
          ...s,
          stage: data.passed ? 'checking' : s.stage,
          checkIssues: data.passed ? [] : data.issues,
          message: data.passed ? '质量检查通过，等待最终结果' : '质量检查未通过，准备自纠',
        }))
        return
      }
      case 'done': {
        const data = event.data as SSEDoneData
        const idx = data.section_index
        if (idx === null || idx === undefined) {
          // 理论上 done 事件都有 section_index，兜底不处理
          return
        }
        patchSection(idx, (s) => ({
          ...s,
          stage: 'done',
          message: '内容已生成',
          checkIssues: [],
          issuesHistory: data.issues_history as { attempts: number; issues: string[] }[],
          content: data.content,
          title: data.title,
          attempt: data.attempts,
        }))

        // 同步把这一段写进 React Query 缓存，让 ContentArea 不重拉也能看到最新内容
        const cached =
          queryClient.getQueryData<ResumeContent[]>(contentsKey(id)) ?? []
        const current = cached.find((c) => c.section_index === idx)
        if (current) {
          const updated: ResumeContent = {
            ...current,
            title: data.title,
            content: data.content,
            status: 'ready',
            issues: data.issues_history,
            revision: current.revision + 1,
            updated_at: new Date().toISOString(),
          }
          queryClient.setQueryData(contentsKey(id), (old?: ResumeContent[]) =>
            old?.map((item) => (item.section_index === idx ? updated : item)) ?? [updated],
          )
        }
        return
      }
      case 'error': {
        // 整体级错误事件
        setProgress((prev) => ({
          ...prev,
          stage: 'error',
          message: (event.data as { message: string }).message,
        }))
        return
      }
    }
  }

  /**
   * 启动一次多段并发流式重新生成。
   *
   * 流程和单段版基本一致：fetch POST → 读 ReadableStream → 按 \n\n 分帧 →
   * 解析 event:/data: 组装 SSEEvent → 分发到 handleEvent。
   *
   * 区别：端点不一样；启动前要重置 5 段状态为初始值；
   * 流自然结束（后端 all_done 后会关闭响应）时若所有段都已 done，
   * 整体 stage 自动变 done（来自 status 事件），不需要特殊处理。
   */
  const start = async () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller

    // 重置到初始 running 状态，5 段全部 idle
    const fresh = makeInitialProgress(sectionCount)
    fresh.stage = 'running'
    fresh.message = '主编正在规划生成方案…'
    setProgress(fresh)

    try {
      const token = tokenStore.get()
      const response = await fetch(
        `${API_PREFIX}/resumes/${id}/contents/regenerate/stream`,
        {
          method: 'POST',
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        },
      )

      if (!response.ok || !response.body) {
        // 后端在准备阶段就报错（404/409 等），没有 SSE 流
        const text = await response.text().catch(() => '')
        setProgress((prev) => ({
          ...prev,
          stage: 'error',
          message: text || `请求失败 (${response.status})`,
        }))
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!controller.signal.aborted) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        // SSE 协议：两个 \n\n 之间是一个完整事件帧
        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)

          // 帧里每行是 "event: xxx" 或 "data: xxx"，分别收集
          let eventType = ''
          let dataStr = ''
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim()
            } else if (line.startsWith('data:')) {
              dataStr += line.slice(5).trimStart()
            }
          }
          if (eventType && dataStr) {
            try {
              const event = {
                event: eventType,
                data: JSON.parse(dataStr),
              } as SSEEvent
              handleEvent(event)
            } catch {
              // 单帧解析失败不中断整条流（容错）
            }
          }
          boundary = buffer.indexOf('\n\n')
        }
      }

      // 流自然结束：若没有任何段 failed 也没有整体 error 事件，确保 stage 落到 done
      // 后端正常情况会先发 status(done, section_index=null) 再关闭流，这里只是兜底
      setProgress((prev) => {
        if (prev.stage === 'error') return prev
        const allDone = prev.sections.every((s) => s.stage === 'done')
        return {
          ...prev,
          stage: allDone ? 'done' : prev.stage,
          message: allDone ? '5 段内容生成完成' : prev.message,
        }
      })

      // 全部完成后让 React Query 重拉 contents，纠正乐观更新的字段（如 updated_at）
      void queryClient.invalidateQueries({ queryKey: contentsKey(id) })
      // resume.status 也会变（generating → ready），刷详情
      void queryClient.invalidateQueries({ queryKey: detailKey(id) })
    } catch (error) {
      if (controller.signal.aborted) return // 主动中止不算错
      setProgress((prev) => ({
        ...prev,
        stage: 'error',
        message: error instanceof Error ? error.message : '网络错误',
      }))
    }
  }

  /** 重置状态到 idle，给按钮再次点击用。 */
  const reset = () => {
    controllerRef.current?.abort()
    setProgress(makeInitialProgress(sectionCount))
  }

  return {
    progress,
    start,
    reset,
    isRunning: progress.stage === 'running',
  }
}

/* ============================================================
 * 对话式编辑（POST /resumes/{id}/chat）
 *
 * 后端跑 LangGraph ReAct 图：用户自然语言 → Agent 调工具改真实 DB。
 * LangGraph Checkpointer 按 thread_id 持久化会话状态，
 * 前端只需把上次响应的 thread_id 原样回传即可延续上下文。
 * ========================================================== */

/**
 * 对一份简历发起一轮对话式编辑。
 *
 * 注意：useMutation 不会自动维护"对话历史"——历史由组件层用 useState 维护
 * （把每次响应追加到 messages 数组）。thread_id 也由组件层持久化（state 或
 * sessionStorage），hook 本身是无状态的，每轮独立请求。
 *
 * 成功后让 contents 缓存失效：Agent 可能通过 update_section 改了 DB，
 * 让下次读 contents 重拉拿到最新内容，保证 ContentCard 显示新数据。
 */
export function useChat(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ChatRequest) =>
      request<ChatResponse>(`/resumes/${id}/chat`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      // Agent 可能通过 update_section 改了 DB，让 contents 缓存失效
      void queryClient.invalidateQueries({ queryKey: contentsKey(id) })
    },
  })
}
