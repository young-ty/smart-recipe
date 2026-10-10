// 用户自己的大模型 Key（FR-16）：未登录时只落在本机浏览器里。
//
// 服务端不预置任何 Key：未登录时这份随每次请求的头带过去、用完即弃；登录后由
// useLlmKey 迁进账号（服务端加密落库、只回掩码），请求头里就不再带它（设计方案 7.8）。
// 两处都有时请求头优先，方便临时换一把 Key 试试。

import { defaultStorage, readStoredJson, removeStored, writeStoredJson } from './storage'

export type LlmKeySettings = {
  apiKey: string
  /** 兼容 OpenAI 协议的服务地址；留空表示用服务端配的默认值 */
  baseUrl: string
  /** 模型名；留空表示用服务端配的默认值 */
  model: string
}

export const LLM_KEY_STORAGE_KEY = 'recipe-llm-key.v1'

/** 与后端 `app/api/deps.py` 的请求头名一一对应 */
export const LLM_API_KEY_HEADER = 'X-LLM-Api-Key'
export const LLM_BASE_URL_HEADER = 'X-LLM-Base-Url'
export const LLM_MODEL_HEADER = 'X-LLM-Model'

/** 长度上限与后端一致：超限在本地就拦下，不发一趟注定被拒的请求 */
export const MAX_API_KEY_LENGTH = 200
export const MAX_BASE_URL_LENGTH = 200
export const MAX_MODEL_LENGTH = 64

/** 本机地址是显式例外：本地起一个兼容 OpenAI 协议的网关时用得上 */
const LOCAL_BASE_URL_PREFIXES = ['http://localhost', 'http://127.0.0.1', 'http://[::1]']

export const EMPTY_LLM_KEY: LlmKeySettings = { apiKey: '', baseUrl: '', model: '' }

function isAllowedBaseUrl(baseUrl: string): boolean {
  return (
    baseUrl.startsWith('https://') ||
    LOCAL_BASE_URL_PREFIXES.some((prefix) => baseUrl.startsWith(prefix))
  )
}

/** 去掉首尾空白，并把地址末尾的斜杠归一化掉（与后端同一套口径） */
export function normalizeLlmKey(input: LlmKeySettings): LlmKeySettings {
  return {
    apiKey: input.apiKey.trim(),
    baseUrl: input.baseUrl.trim().replace(/\/+$/, ''),
    model: input.model.trim(),
  }
}

/** 保存前先校验；返回能直接显示的一句话，没问题返回 null */
export function validateLlmKey(input: LlmKeySettings): string | null {
  const settings = normalizeLlmKey(input)
  if (!settings.apiKey) {
    return '请填模型 Key'
  }
  if (settings.apiKey.length > MAX_API_KEY_LENGTH) {
    return `Key 太长（上限 ${MAX_API_KEY_LENGTH} 个字符）`
  }
  if (settings.baseUrl && !isAllowedBaseUrl(settings.baseUrl)) {
    return '模型地址只接受 https://；本机地址可以写 http://localhost 或 http://127.0.0.1'
  }
  if (settings.baseUrl.length > MAX_BASE_URL_LENGTH) {
    return `模型地址太长（上限 ${MAX_BASE_URL_LENGTH} 个字符）`
  }
  if (settings.model.length > MAX_MODEL_LENGTH) {
    return `模型名太长（上限 ${MAX_MODEL_LENGTH} 个字符）`
  }
  return null
}

/** 读本机 Key：没填过、内容被改坏、存储不可用都返回 null */
export function readLlmKey(target: Storage | null = defaultStorage()): LlmKeySettings | null {
  const raw = readStoredJson(LLM_KEY_STORAGE_KEY, target)
  if (typeof raw !== 'object' || raw === null) {
    return null
  }
  const stored = raw as Partial<LlmKeySettings>
  if (typeof stored.apiKey !== 'string' || !stored.apiKey.trim()) {
    return null
  }
  return normalizeLlmKey({
    apiKey: stored.apiKey,
    baseUrl: typeof stored.baseUrl === 'string' ? stored.baseUrl : '',
    model: typeof stored.model === 'string' ? stored.model : '',
  })
}

export function writeLlmKey(
  input: LlmKeySettings,
  target: Storage | null = defaultStorage(),
): void {
  writeStoredJson(LLM_KEY_STORAGE_KEY, normalizeLlmKey(input), target)
}

export function clearLlmKey(target: Storage | null = defaultStorage()): void {
  removeStored(LLM_KEY_STORAGE_KEY, target)
}

/**
 * 随请求带出去的凭据头。
 *
 * 只有真的填了 Key 才带头：带一个空的 `X-LLM-Api-Key` 会让后端把这次请求当成
 * 「用户自带了凭据」，从而不再回落 `.env` 里那份（见 deps.py 的空值判定）。
 */
export function llmRequestHeaders(
  target: Storage | null = defaultStorage(),
): Record<string, string> {
  const settings = readLlmKey(target)
  if (settings === null) {
    return {}
  }
  const headers: Record<string, string> = { [LLM_API_KEY_HEADER]: settings.apiKey }
  if (settings.baseUrl) {
    headers[LLM_BASE_URL_HEADER] = settings.baseUrl
  }
  if (settings.model) {
    headers[LLM_MODEL_HEADER] = settings.model
  }
  return headers
}

/** 卡片上只露头尾：截图与录屏里带不出完整的 Key */
export function maskApiKey(apiKey: string): string {
  const key = apiKey.trim()
  if (key.length <= 8) {
    return '•'.repeat(Math.max(key.length, 4))
  }
  return `${key.slice(0, 4)}••••${key.slice(-4)}`
}
