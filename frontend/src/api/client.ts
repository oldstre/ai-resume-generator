import { tokenStore } from '@/features/auth/token'

/** 后端 detail 可能是字符串、带 message 的对象，或 FastAPI 校验错误数组 */
export type ApiErrorDetail = string | Record<string, unknown> | unknown[]

export class ApiError extends Error {
  readonly status: number
  readonly detail: ApiErrorDetail

  constructor(status: number, detail: ApiErrorDetail) {
    super(typeof detail === 'string' ? detail : '请求失败')
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const API_PREFIX = '/api/v1'

/** FastAPI 校验错误体形状：detail 为带 msg/loc 的数组；此处只透传，展示文案在 lib/errors.ts */
interface ValidationErrorBody {
  detail: Array<{ msg: string; loc: (string | number)[] }>
}

export async function readErrorDetail(response: Response): Promise<ApiErrorDetail> {
  const text = await response.text().catch(() => '')
  if (!text) return response.statusText || '请求失败'

  try {
    const body = JSON.parse(text) as { detail?: ApiErrorDetail } | ValidationErrorBody
    if (body.detail !== undefined && body.detail !== null) return body.detail
  } catch {
    // 非 JSON 响应，原样返回
  }
  return text
}

// ──────────────────────────────────────────────
// 401 自动 refresh 机制
// ──────────────────────────────────────────────

/**
 * 并发去重：多个请求同时 401 时，只发一次 /auth/refresh，其余请求等同一个 Promise。
 * refresh 完成后排队等待的请求拿新 token 各自重试。
 */
let refreshPromise: Promise<string> | null = null

/**
 * 用 refresh token 换新 access token。
 *
 * 走裸 fetch 而非 request()——request() 的 401 拦截会递归调到这里，
 * 用裸 fetch 打断递归。后端 /auth/refresh 做了 token 轮转：
 * 旧 refresh 立即失效，返回新 refresh。
 */
async function doRefresh(): Promise<string> {
  if (refreshPromise) return refreshPromise

  const refreshToken = tokenStore.getRefresh()
  if (!refreshToken) {
    throw new Error('No refresh token')
  }

  refreshPromise = (async () => {
    try {
      const response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!response.ok) {
        throw new Error(`Refresh failed: ${response.status}`)
      }
      const data = (await response.json()) as {
        access_token: string
        refresh_token: string
        token_type: string
      }
      // refresh 端点做 token 轮转：新 refresh 也要存回 localStorage
      tokenStore.set(data.access_token, data.refresh_token)
      return data.access_token
    } finally {
      // 无论成功失败都清掉 promise，下次 401 可重新尝试
      refreshPromise = null
    }
  })()

  return refreshPromise
}

/**
 * auth 失败回调：由 store.ts 注册。
 *
 * 当 refresh 也失败时，请求层无法直接 import store（循环依赖），
 * 改用回调让 store 自己清 user 状态 → RequireAuth 自动跳 /login。
 */
let authFailureHandler: (() => void) | null = null

export function setAuthFailureHandler(handler: (() => void) | null) {
  authFailureHandler = handler
}

/**
 * 全站唯一的请求出口。集中在这里是为了让鉴权头、错误语义、
 * 401 refresh + 重试只有一处定义。
 *
 * 401 处理流程：
 *   1. 请求 A 返回 401
 *   2. 有 refresh token → 调 doRefresh()（并发 401 复用同一个 Promise）
 *   3. refresh 成功 → 拿新 access token 重试 A
 *   4. 重试仍 401 或 refresh 失败 → 清 token + 触发 authFailureHandler 跳登录
 */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = tokenStore.get()
  // multipart 的边界串必须由浏览器自己生成，预先写死 Content-Type 后端就解析不出字段
  const multipart = init.body instanceof FormData

  const doFetch = (accessToken: string | null): Promise<Response> =>
    fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: {
        ...(multipart ? {} : { 'Content-Type': 'application/json' }),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...init.headers,
      },
    })

  let response = await doFetch(token)

  // 401 → 尝试 refresh + 重试（只重试一次）
  if (response.status === 401 && tokenStore.getRefresh()) {
    try {
      const newToken = await doRefresh()
      response = await doFetch(newToken)
    } catch {
      // refresh 本身失败：refresh token 过期 / 被 revoke
      tokenStore.clear()
      authFailureHandler?.()
      throw new ApiError(401, '会话已过期，请重新登录')
    }
  }

  if (!response.ok) {
    // 重试后仍 401（新 token 也被拒），或原本就没 refresh token 的 401
    if (response.status === 401) {
      tokenStore.clear()
      authFailureHandler?.()
    }
    throw new ApiError(response.status, await readErrorDetail(response))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

/**
 * 拉取二进制响应。JSON 的 request() 会破坏文件流，导出类接口走这里。
 * 成功返回 Response（调用方自行 .blob()）；失败抛 ApiError，detail 可能是对象。
 *
 * 同样带 401 refresh + 重试逻辑。
 */
export async function requestBinary(path: string, init: RequestInit = {}): Promise<Response> {
  const token = tokenStore.get()

  const doFetch = (accessToken: string | null): Promise<Response> =>
    fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...init.headers,
      },
    })

  let response = await doFetch(token)

  if (response.status === 401 && tokenStore.getRefresh()) {
    try {
      const newToken = await doRefresh()
      response = await doFetch(newToken)
    } catch {
      tokenStore.clear()
      authFailureHandler?.()
      throw new ApiError(401, '会话已过期，请重新登录')
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      tokenStore.clear()
      authFailureHandler?.()
    }
    throw new ApiError(response.status, await readErrorDetail(response))
  }

  return response
}
