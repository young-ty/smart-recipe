// 模型 Key 的归属切换（FR-16、FR-17）。
//
// 未登录：存在这台设备上（localStorage），随请求头带给后端，用完即弃。
// 已登录：存在账号里，服务端加密落库，前端只看得到掩码；请求不再带头，
// 后端按会话从账号里取。**同一时刻只有一份生效**，不然「卡片显示 A、实际用的是 B」
// 这种事迟早会发生——所以登录时把本机那份迁进账号并清掉本机。

import { useCallback, useEffect, useState } from 'react'

import { ApiError, deleteLlmKey, fetchLlmKey, putLlmKey } from '../api/client'
import {
  clearLlmKey,
  maskApiKey,
  normalizeLlmKey,
  readLlmKey,
  writeLlmKey,
} from '../llm-key'
import type { LlmKeySettings } from '../llm-key'
import type { AccountUser } from '../types'

export type LlmKeyView = {
  configured: boolean
  masked: string
  baseUrl: string
  model: string
  /** 这份 Key 现在存在哪儿：账号上，还是这台设备上 */
  storedOn: 'account' | 'device'
}

export type LlmKeyController = {
  view: LlmKeyView
  /** 登录态还没落定时为真：这段时间不写「还没填」，免得闪一下 */
  checking: boolean
  /** 保存失败时给用户看的一句话 */
  error: string | null
  saving: boolean
  save: (settings: LlmKeySettings) => Promise<void>
  remove: () => Promise<void>
  /** 当前生效的凭据，交给「测试连接」判断能不能探 */
  hasKey: boolean
}

const EMPTY_VIEW: LlmKeyView = {
  configured: false,
  masked: '',
  baseUrl: '',
  model: '',
  storedOn: 'device',
}

function deviceView(settings: LlmKeySettings | null): LlmKeyView {
  if (settings === null) {
    return EMPTY_VIEW
  }
  return {
    configured: true,
    masked: maskApiKey(settings.apiKey),
    baseUrl: settings.baseUrl,
    model: settings.model,
    storedOn: 'device',
  }
}

function accountView(body: {
  configured: boolean
  masked: string
  base_url: string | null
  model: string | null
}): LlmKeyView {
  return {
    configured: body.configured,
    masked: body.masked,
    baseUrl: body.base_url ?? '',
    model: body.model ?? '',
    storedOn: 'account',
  }
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  return '模型 Key 没存上，稍后再试'
}

/**
 * 登录 / 退出时切换 Key 的归属。
 *
 * `online` 为假（静态演示站）时只认本机那份：那种站点没有后端，账号相关的调用
 * 一律不该发出去。
 */
export function useLlmKey(user: AccountUser | null, online: boolean): LlmKeyController {
  const [view, setView] = useState<LlmKeyView>(() => deviceView(readLlmKey()))
  const [checking, setChecking] = useState(online && user !== null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (user === null) {
      // 退出登录：回到这台设备自己那份（登录时已经被清掉，这里通常就是空的）
      setView(deviceView(readLlmKey()))
      setChecking(false)
      setError(null)
      return
    }
    if (!online) {
      setView(deviceView(readLlmKey()))
      setChecking(false)
      return
    }
    let cancelled = false
    setChecking(true)
    const local = readLlmKey()
    fetchLlmKey()
      .then(async (body) => {
        if (body.configured) {
          // 账号里已经有了：本机那份不再参与，清掉以免「显示的是账号的、用的是本机的」
          clearLlmKey()
          return body
        }
        if (local === null) {
          return body
        }
        // 账号里还没有、本机有一份：搬过去，用户在哪台设备登录都能接着用
        const migrated = await putLlmKey(normalizeLlmKey(local))
        clearLlmKey()
        return migrated
      })
      .then((body) => {
        if (!cancelled) {
          setView(accountView(body))
        }
      })
      .catch(() => {
        if (!cancelled) {
          // 拉不到账号里那份时不假装成功：显示本机（通常为空），并说明原因
          setView(deviceView(readLlmKey()))
          setError('读不到账号里的模型 Key，稍后再试')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setChecking(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [user, online])

  const save = useCallback(
    async (settings: LlmKeySettings) => {
      setSaving(true)
      setError(null)
      try {
        if (user !== null && online) {
          setView(accountView(await putLlmKey(normalizeLlmKey(settings))))
          clearLlmKey()
          return
        }
        writeLlmKey(settings)
        setView(deviceView(normalizeLlmKey(settings)))
      } catch (failure) {
        setError(messageOf(failure))
        throw failure
      } finally {
        setSaving(false)
      }
    },
    [user, online],
  )

  const remove = useCallback(async () => {
    setError(null)
    if (user !== null && online) {
      try {
        await deleteLlmKey()
      } catch (failure) {
        setError(messageOf(failure))
        throw failure
      }
      setView({ ...EMPTY_VIEW, storedOn: 'account' })
      return
    }
    clearLlmKey()
    setView(EMPTY_VIEW)
  }, [user, online])

  return { view, checking, error, saving, save, remove, hasKey: view.configured }
}
