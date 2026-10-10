// 账号（FR-16）：注册、登录与展示名的取用规则。
//
// 会话令牌只存在 httpOnly cookie 里，前端脚本读不到，所以这里不持有任何令牌，
// 只持有接口返回的账号信息。

import { ApiError } from './api/client'
import type { AccountUser } from './types'

export type AuthMode = 'login' | 'register'

export const MIN_PASSWORD_LENGTH = 8
export const MAX_DISPLAY_NAME_LENGTH = 32

/** 邮箱大小写不敏感：注册与登录都先归一化，免得同一个邮箱建出两个账号 */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase()
}

export function validateEmail(email: string): string | null {
  const value = normalizeEmail(email)
  if (!value) {
    return '请填邮箱'
  }
  // 只挡明显不是邮箱的输入：真正的判定在服务端，前端不堆严格正则
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    return '邮箱格式不对'
  }
  return null
}

export function validatePassword(password: string, mode: AuthMode): string | null {
  if (!password) {
    return '请填密码'
  }
  if (mode === 'register' && password.length < MIN_PASSWORD_LENGTH) {
    return `密码至少 ${MIN_PASSWORD_LENGTH} 位`
  }
  return null
}

export function validateDisplayName(displayName: string): string | null {
  if (displayName.trim().length > MAX_DISPLAY_NAME_LENGTH) {
    return `显示名最多 ${MAX_DISPLAY_NAME_LENGTH} 个字`
  }
  return null
}

/** 展示名：注册时没填就取邮箱 @ 前面那段，页头不用为它留空 */
export function displayNameOf(user: AccountUser): string {
  return user.display_name || user.email.split('@')[0]
}

/** 把接口错误翻成用户能照着改的一句话 */
export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 40101) {
      return '邮箱或密码不正确'
    }
    if (error.code === 40901) {
      return '这个邮箱已经注册过了，直接登录就行'
    }
    // 42901 的文案里带着还要等多少秒，原样显示
    return error.message
  }
  return '登录服务暂时不可用，稍后再试'
}
