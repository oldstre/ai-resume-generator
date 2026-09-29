/**
 * 鉴权 API —— 对接后端 /auth/* 端点。
 *
 * 后端契约（backend/app/api/v1/auth.py）：
 *   POST /auth/register  → 201 TokenPair
 *   POST /auth/login     → 200 TokenPair
 *   POST /auth/logout    → 204（body: { refresh_token }）
 *
 * refresh 端点（/auth/refresh）不走这里——它是请求层 401 拦截的内部逻辑，
 * 定义在 api/client.ts 中，避免 request() → refresh → request() 递归。
 */
import { request } from '@/api/client'

/** 后端 UserRead schema（绝不包含 hashed_password） */
export interface UserPublic {
  id: string
  email: string
  full_name: string
  is_active: boolean
  created_at: string
}

/** 登录 / 注册成功后返回的 token 对 */
export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  user: UserPublic
}

export interface Credentials {
  email: string
  password: string
}

/** 注册需要比登录多一个 full_name */
export interface RegisterPayload extends Credentials {
  full_name: string
}

/** 登录：POST /auth/login → 200 TokenPair */
export async function login(payload: Credentials): Promise<TokenPair> {
  return request<TokenPair>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** 注册：POST /auth/register → 201 TokenPair */
export async function register(payload: RegisterPayload): Promise<TokenPair> {
  return request<TokenPair>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** 登出：POST /auth/logout → 204。revoke refresh token（软删除）。 */
export async function logout(refreshToken: string): Promise<void> {
  await request<void>('/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
}
