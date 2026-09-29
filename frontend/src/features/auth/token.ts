const ACCESS_KEY = 'airesume.accessToken'
const REFRESH_KEY = 'airesume.refreshToken'

/**
 * token 的唯一持有处。单独成模块是为了让请求层和状态层都能读它，
 * 而不必互相 import 造成循环依赖。
 *
 * access token 短命（15 分钟），refresh token 长命（7 天）。
 * 两者都存 localStorage：开发阶段够用，生产环境可改为 access 放内存、
 * refresh 放 httpOnly cookie 以降低 XSS 风险。
 */
export const tokenStore = {
  /** 读取 access token（请求层注入 Authorization 头用） */
  get(): string | null {
    return localStorage.getItem(ACCESS_KEY)
  },
  /** 读取 refresh token（401 时换新 access 用） */
  getRefresh(): string | null {
    return localStorage.getItem(REFRESH_KEY)
  },
  /** 同时写入 access + refresh */
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  /** 清空两个 token（登出 / refresh 失败时调用） */
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}
