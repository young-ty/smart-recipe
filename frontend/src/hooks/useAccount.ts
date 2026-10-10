import { useCallback, useEffect, useState } from 'react'

import { normalizeEmail } from '../account'
import { fetchMe, loginAccount, logoutAccount, registerAccount } from '../api/client'
import { DEMO_MODE } from '../demo'
import type { AccountUser } from '../types'

export type AccountController = {
  user: AccountUser | null
  /** 首次 `/api/auth/me` 返回前为真：这段时间页头不写登录态，免得闪一下「未登录」 */
  checking: boolean
  /** 这份后端是否提供账号服务；静态演示站没有 */
  available: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
}

/**
 * 登录态（FR-16）。
 *
 * 未登录时整页功能照旧可用，账号只是一个可选的加分项；`/api/auth/me` 未登录返回
 * `{user: null}` 而不是 401，所以这里不会把「没登录」当成错误。
 */
export function useAccount(): AccountController {
  const [user, setUser] = useState<AccountUser | null>(null)
  const [checking, setChecking] = useState(!DEMO_MODE)

  useEffect(() => {
    if (DEMO_MODE) {
      return
    }
    let cancelled = false
    fetchMe()
      .then((body) => {
        if (!cancelled) {
          setUser(body.user)
        }
      })
      .catch(() => {
        // 后端没起来时按未登录处理：识别与取菜谱要能继续用，账号先放着
      })
      .finally(() => {
        if (!cancelled) {
          setChecking(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const body = await loginAccount(normalizeEmail(email), password)
    setUser(body.user)
  }, [])

  const register = useCallback(async (email: string, password: string, displayName: string) => {
    const body = await registerAccount(
      normalizeEmail(email),
      password,
      displayName.trim() || undefined,
    )
    setUser(body.user)
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutAccount()
    } catch {
      // 令牌在 httpOnly cookie 里，脚本删不掉；退出请求失败也没别的补救办法，
      // 界面上按已退出处理，反正这次会话不会再被用来做任何事
    } finally {
      setUser(null)
    }
  }, [])

  return { user, checking, available: !DEMO_MODE, login, register, logout }
}
