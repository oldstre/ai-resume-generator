/**
 * 对话式编辑面板。
 *
 * 用户用自然语言改简历，Agent 在后端通过工具改真实 DB，
 * 前端这边只负责：维护对话历史 + 发请求 + 渲染气泡。
 *
 * 会话连续性靠 thread_id：
 *   - 首次对话不传 → 后端生成新 UUID 返回
 *   - 后续对话原样回传 → 后端 checkpointer 恢复历史
 *
 * 历史和 thread_id 都用 useState 存（组件级），离开页面就丢——
 * 简单可控；后续要"刷新页面还在"可挪到 sessionStorage。
 */
import { Loader2, Send } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { errorMessage } from '@/lib/errors'
import { useChat } from './api'
import type { ChatMessage } from './types'

export function ChatPanel({ id }: { id: string }) {
  const chat = useChat(id)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [threadId, setThreadId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  // 滚动容器 ref，用于发新消息时自动滚到底
  const scrollRef = useRef<HTMLDivElement>(null)

  // messages 变化（含 pending 占位）就滚到底，让用户看到最新消息
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, chat.isPending])

  // 换简历（id 变）就清空对话——不同简历的对话不混
  useEffect(() => {
    setMessages([])
    setThreadId(null)
    setInput('')
  }, [id])

  const handleSend = () => {
    const text = input.trim()
    if (!text || chat.isPending) return

    // 乐观追加 user 消息（不等后端响应）
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setInput('')

    chat.mutate(
      { message: text, thread_id: threadId },
      {
        onSuccess: (data) => {
          setThreadId(data.thread_id)
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: data.reply },
          ])
        },
        onError: (error) => {
          // 失败也用助手气泡显示错误，让用户看到原因（不破坏对话流）
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: `（出错了：${errorMessage(error, '请稍后重试')}）`,
            },
          ])
        },
      },
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-ink-muted">
        用自然语言修改简历，例如"把第 2 段精简一下"或"工作经历里加上 XX 公司"。
        Agent 会自动调工具改 DB，改完正文区会同步刷新。
      </p>

      {/* 对话历史区：可滚动，max-h 限制高度 */}
      <div
        ref={scrollRef}
        className="scrollbar-slim max-h-96 space-y-3 overflow-y-auto rounded-2xl border border-line bg-surface-soft/40 p-4"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-ink-muted">
            还没有对话，试着输入需求开始吧。
          </p>
        ) : (
          messages.map((m, i) => (
            <div
              key={i}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                  m.role === 'user'
                    ? 'bg-accent text-white'
                    : 'border border-line bg-surface text-ink'
                }`}
              >
                {m.content}
              </div>
            </div>
          ))
        )}

        {/* 思考中占位：等后端响应时显示 */}
        {chat.isPending && (
          <div className="flex justify-start">
            <div className="inline-flex items-center gap-1.5 rounded-2xl border border-line bg-surface px-3 py-2 text-sm text-ink-muted">
              <Loader2 className="size-3.5 animate-spin" />
              思考中…
            </div>
          </div>
        )}
      </div>

      {/* 输入区：textarea + 发送按钮 */}
      <div className="flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送，Shift+Enter 换行（多行输入）
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          rows={2}
          placeholder="例如：把第 2 段（专业技能）精简一下，删掉冗余描述"
          className="scrollbar-slim flex-1 resize-none rounded-xl border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
        />
        <Button
          onClick={handleSend}
          disabled={chat.isPending || !input.trim()}
        >
          {chat.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          发送
        </Button>
      </div>

      {/* 失败兜底（仅在还没建立对话时显示，避免与气泡里的错误重复） */}
      {chat.isError && messages.length === 0 && (
        <p role="alert" className="text-xs text-negative">
          {errorMessage(chat.error, '请求失败，请稍后重试')}
        </p>
      )}
    </div>
  )
}
