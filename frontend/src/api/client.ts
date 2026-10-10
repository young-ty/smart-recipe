// 后端接口客户端：统一处理前缀、错误体与超时提示（设计方案 5.3、5.4）

import {
  DEMO_MODE,
  demoDishList,
  demoHealth,
  demoRecognition,
  demoRecognizeResponse,
  demoResolve,
} from '../demo'
import { llmRequestHeaders } from '../llm-key'
import type { MemoryEntry } from '../memory'
import type {
  AccountUser,
  DishListResponse,
  ResolveRequest,
  HealthResponse,
  RecipeDetail,
  RecognizeResponse,
  ResolveResponse,
} from '../types'

export class ApiError extends Error {
  readonly code: number
  readonly status: number

  constructor(code: number, message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

type ErrorBody = { error?: { code?: number; message?: string } }

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    // 会话 Cookie 是 httpOnly 的，脚本读不到也删不掉，只能让浏览器自己带着走
    response = await fetch(`${API_BASE}/api${path}`, { credentials: 'include', ...init })
  } catch {
    throw new ApiError(50001, '无法连接后端服务，请确认服务已启动')
  }

  if (!response.ok) {
    let body: ErrorBody = {}
    try {
      body = (await response.json()) as ErrorBody
    } catch {
      body = {}
    }
    throw new ApiError(
      body.error?.code ?? 50001,
      body.error?.message ?? `请求失败（HTTP ${response.status}）`,
      response.status,
    )
  }

  if (response.status === 204) {
    // 退出登录这类接口没有响应体，硬解析 JSON 反而会在这里记一次失败
    return undefined as T
  }
  return (await response.json()) as T
}

export function recognizeImage(file: File): Promise<RecognizeResponse> {
  // 静态演示站没有后端可打，识别结果取自预置的真实模型输出（frontend/src/demo/）
  if (DEMO_MODE) {
    return Promise.resolve(demoRecognizeResponse(demoRecognition(file).sample.id))
  }
  const form = new FormData()
  form.append('file', file)
  return request<RecognizeResponse>('/recognize', { method: 'POST', body: form })
}

export function resolveRecipe(dish: string, index?: number | null): Promise<ResolveResponse> {
  if (DEMO_MODE) {
    return Promise.resolve(demoResolve(dish))
  }
  const body: ResolveRequest = { dish }
  if (index != null) {
    body.index = index
  }
  return request<ResolveResponse>('/recipes/resolve', {
    method: 'POST',
    // 用户自带 Key 时随这次请求带过去：库外菜走他自己的额度（设计方案 7.8）
    headers: { 'Content-Type': 'application/json', ...llmRequestHeaders() },
    body: JSON.stringify(body),
  })
}

export function fetchRecipe(recipeId: number): Promise<RecipeDetail> {
  return request<RecipeDetail>(`/recipes/${recipeId}`)
}

export function fetchDishes(): Promise<DishListResponse> {
  if (DEMO_MODE) {
    return Promise.resolve(demoDishList())
  }
  return request<DishListResponse>('/dishes')
}

export function fetchHealth(): Promise<HealthResponse> {
  if (DEMO_MODE) {
    return Promise.resolve(demoHealth())
  }
  return request<HealthResponse>('/health')
}

export type AccountUserEnvelope = { user: AccountUser }
export type MeResponse = { user: AccountUser | null }
export type MemoryListResponse = { entries: MemoryEntry[] }
export type LlmProbeResponse = {
  ok: boolean
  configured: boolean
  model: string
  detail: string
}

/** 静态演示站没有后端，账号与同步都无从谈起；这里明说不支持，不做假成功 */
const DEMO_NO_BACKEND = '静态演示站不带后端服务：账号功能请在本地运行完整版'

export function registerAccount(
  email: string,
  password: string,
  displayName?: string,
): Promise<AccountUserEnvelope> {
  if (DEMO_MODE) {
    return Promise.reject(new ApiError(50001, DEMO_NO_BACKEND))
  }
  return request<AccountUserEnvelope>('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: displayName || null }),
  })
}

/** 注册即登录，登录同样直接返回账号信息；令牌不出现在响应体里 */
export function loginAccount(email: string, password: string): Promise<AccountUserEnvelope> {
  if (DEMO_MODE) {
    return Promise.reject(new ApiError(50001, DEMO_NO_BACKEND))
  }
  return request<AccountUserEnvelope>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

/** 只退当前这条会话；其他设备上的登录不受影响 */
export function logoutAccount(): Promise<void> {
  if (DEMO_MODE) {
    return Promise.resolve()
  }
  return request<void>('/auth/logout', { method: 'POST' })
}

/** 未登录返回 `{user: null}` 而不是 401：页面据此显示登录入口，而不是弹一条错误 */
export function fetchMe(): Promise<MeResponse> {
  if (DEMO_MODE) {
    return Promise.resolve({ user: null })
  }
  return request<MeResponse>('/auth/me')
}

export function fetchMemories(): Promise<MemoryListResponse> {
  if (DEMO_MODE) {
    return Promise.resolve({ entries: [] })
  }
  return request<MemoryListResponse>('/memories')
}

/** 记忆同步的线上格式：来源在记忆里只会是这两种（拿不到菜谱的菜不会被记下） */
type MemoryPayload = {
  dish: string
  name: string
  source: 'library' | 'generated'
  recipe: MemoryEntry['recipe']
  at: number
  favorite: boolean
}

function toMemoryPayload(entry: MemoryEntry): MemoryPayload {
  return {
    dish: entry.dish,
    name: entry.name,
    source: entry.source === 'generated' ? 'generated' : 'library',
    recipe: entry.recipe,
    at: entry.at,
    favorite: entry.favorite,
  }
}

/** 整表覆盖式上传：合并已经在前端做完，服务端只做去重、排序与截断 */
export function putMemories(entries: MemoryEntry[]): Promise<MemoryListResponse> {
  if (DEMO_MODE) {
    return Promise.resolve({ entries })
  }
  return request<MemoryListResponse>('/memories', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entries: entries.map(toMemoryPayload) }),
  })
}

/**
 * 探活用户自带的模型凭据（设计方案 7.8）。
 *
 * 用一次 max_tokens=1 的最小调用回答「这套 Key / 地址 / 模型名现在能不能用」，
 * 免得等到某道库外菜生成失败才发现填错了。
 */
export function probeLlmCredentials(): Promise<LlmProbeResponse> {
  if (DEMO_MODE) {
    return Promise.reject(new ApiError(50001, DEMO_NO_BACKEND))
  }
  return request<LlmProbeResponse>('/llm/probe', {
    method: 'POST',
    headers: llmRequestHeaders(),
  })
}
