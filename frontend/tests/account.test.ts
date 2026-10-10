import { describe, expect, it } from 'vitest'

import {
  MAX_DISPLAY_NAME_LENGTH,
  MIN_PASSWORD_LENGTH,
  authErrorMessage,
  displayNameOf,
  normalizeEmail,
  validateDisplayName,
  validateEmail,
  validatePassword,
} from '../src/account'
import { ApiError } from '../src/api/client'
import type { AccountUser } from '../src/types'

function user(overrides: Partial<AccountUser> = {}): AccountUser {
  return {
    id: 1,
    email: 'hakura@example.com',
    display_name: '',
    created_at: '2026-10-10T00:00:00',
    ...overrides,
  }
}

describe('邮箱归一化与校验', () => {
  it('注册与登录都先去掉空白并转小写', () => {
    expect(normalizeEmail('  Hakura@Example.COM ')).toBe('hakura@example.com')
  })

  it('空值与明显不是邮箱的输入都拦下', () => {
    expect(validateEmail('')).toBe('请填邮箱')
    expect(validateEmail('   ')).toBe('请填邮箱')
    expect(validateEmail('hakura')).toBe('邮箱格式不对')
    expect(validateEmail('hakura@')).toBe('邮箱格式不对')
    expect(validateEmail('hakura@example.com')).toBeNull()
  })
})

describe('密码校验', () => {
  it('空密码拦下', () => {
    expect(validatePassword('', 'login')).toBe('请填密码')
  })

  it('注册要求至少 8 位，登录不查长度', () => {
    expect(validatePassword('1234567', 'register')).toBe(`密码至少 ${MIN_PASSWORD_LENGTH} 位`)
    expect(validatePassword('12345678', 'register')).toBeNull()
    expect(validatePassword('1234567', 'login')).toBeNull()
  })
})

describe('显示名校验', () => {
  it('超长拦下，留空合法', () => {
    expect(validateDisplayName('x'.repeat(MAX_DISPLAY_NAME_LENGTH + 1))).toContain('最多')
    expect(validateDisplayName('')).toBeNull()
    expect(validateDisplayName('hakura')).toBeNull()
  })
})

describe('页头显示的账号名', () => {
  it('填了显示名就用它', () => {
    expect(displayNameOf(user({ display_name: 'hakura' }))).toBe('hakura')
  })

  it('没填就取邮箱 @ 前面那段，页头不留空', () => {
    expect(displayNameOf(user({ display_name: '' }))).toBe('hakura')
  })
})

describe('登录错误的文案', () => {
  it('密码不对与邮箱已注册各给一句能照着改的话', () => {
    expect(authErrorMessage(new ApiError(40101, '请先登录'))).toBe('邮箱或密码不正确')
    expect(authErrorMessage(new ApiError(40901, '邮箱已被注册'))).toBe(
      '这个邮箱已经注册过了，直接登录就行',
    )
  })

  it('限流原样显示服务端文案，里面带着还要等多少秒', () => {
    const message = '登录尝试过于频繁，请 42 秒后再试'
    expect(authErrorMessage(new ApiError(42901, message))).toBe(message)
  })

  it('其他错误码透传服务端文案，不是 ApiError 时给兜底', () => {
    expect(authErrorMessage(new ApiError(50001, '无法连接后端服务，请确认服务已启动'))).toBe(
      '无法连接后端服务，请确认服务已启动',
    )
    expect(authErrorMessage(new Error('boom'))).toBe('登录服务暂时不可用，稍后再试')
    expect(authErrorMessage(undefined)).toBe('登录服务暂时不可用，稍后再试')
  })
})
