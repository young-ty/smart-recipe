// 后端接口客户端：统一处理前缀、错误体与超时提示（设计方案 5.3、5.4）

import {
  DEMO_MODE,
  demoDishList,
  demoHealth,
  demoRecognition,
  demoRecognizeResponse,
  demoResolve,
} from '../demo'
import type {
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
    response = await fetch(`${API_BASE}/api${path}`, init)
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
    headers: { 'Content-Type': 'application/json' },
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
