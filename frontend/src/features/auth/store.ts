import { create } from 'zustand'
import { setAuthFailureHandler } from '@/api/client'
import { type TokenPair, type UserPublic, logout as apiLogout } from '@/features/auth/api'
import { tokenStore } from '@/features/auth/token'

const USER_KEY = 'airesume.user'

interface AuthState {
  user: UserPublic | null
  /** 首屏用本地 token 换取用户信息期间为 true，用于避免受保护路由误判为未登录 */
  restoring: boolean
  applySession: (session: TokenPair) => void
  restore: () => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  // access token 存在 → 可能已登录，等 restore 确认
  restoring: tokenStore.get() !== null,

  applySession: (session) => {
    tokenStore.set(session.access_token, session.refresh_token)
    // 后端无 /auth/me 接口，本地备份 user 供下次刷新恢复
    localStorage.setItem(USER_KEY, JSON.stringify(session.user))
    set({ user: session.user, restoring: false })
  },

  restore: async () => {
    const token = tokenStore.get()
    if (token === null) {
      set({ user: null, restoring: false })
      return
    }
    // access token 存在（可能已过期）。从本地备份恢复 user 信息。
    // token 有效性由首个 API 请求的 401 拦截器懒校验：过期 → 自动 refresh → 重试。
    const raw = localStorage.getItem(USER_KEY)
    if (raw) {
      try {
        set({ user: JSON.parse(raw) as UserPublic, restoring: false })
        return
      } catch {
        // 备份损坏 → 落到未登录态
      }
    }
    tokenStore.clear()
    localStorage.removeItem(USER_KEY)
    set({ user: null, restoring: false })
  },

  logout: () => {
    // best-effort 调后端 revoke refresh token，不等也不抛错
    // access token 是无状态 JWT 无法主动失效，等 15 分钟自然过期
    const refreshToken = tokenStore.getRefresh()
    if (refreshToken) {
      void apiLogout(refreshToken).catch(() => {})
    }
    tokenStore.clear()
    localStorage.removeItem(USER_KEY)
    set({ user: null, restoring: false })
  },
}))

/**
 * 注册 auth 失败回调：请求层 refresh 也失败时调用。
 *
 * 请求层（client.ts）不能 import store（store → api → client 已有依赖链，
 * 反向 import 会循环），所以用回调打破环：store 主动注册，client 反向调用。
 * 触发时清 user → RequireAuth 检测到 user=null → 自动跳 /login。
 */
setAuthFailureHandler(() => {
  tokenStore.clear()
  localStorage.removeItem(USER_KEY)
  useAuthStore.setState({ user: null, restoring: false })
})
