import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AccountBar } from '../src/components/AccountBar'
import type { AccountUser } from '../src/types'

const USER: AccountUser = {
  id: 7,
  email: 'hakura@example.com',
  display_name: 'hakura',
  created_at: '2026-10-10T00:00:00',
}

function renderBar(overrides: Partial<Parameters<typeof AccountBar>[0]> = {}) {
  const onOpenAuth = vi.fn()
  const onLogout = vi.fn()
  render(
    <AccountBar
      user={null}
      checking={false}
      syncState="idle"
      onOpenAuth={onOpenAuth}
      onLogout={onLogout}
      {...overrides}
    />,
  )
  return { onOpenAuth, onLogout }
}

describe('AccountBar', () => {
  it('登录态还没回来时不写「未登录」，先给占位', () => {
    renderBar({ checking: true })

    expect(screen.getByText('账号检测中…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登录 / 注册' })).toBeNull()
  })

  it('未登录时只给一个登录入口', async () => {
    const user = userEvent.setup()
    const { onOpenAuth } = renderBar()

    await user.click(screen.getByRole('button', { name: '登录 / 注册' }))

    expect(onOpenAuth).toHaveBeenCalled()
  })

  it('登录后显示账号名与退出入口，邮箱放在悬停提示里', async () => {
    const user = userEvent.setup()
    const { onLogout } = renderBar({ user: USER })

    expect(screen.getByText('hakura')).toHaveAttribute('title', 'hakura@example.com')
    await user.click(screen.getByRole('button', { name: '退出' }))

    expect(onLogout).toHaveBeenCalled()
  })

  it('没填显示名时用邮箱 @ 前面那段', () => {
    renderBar({ user: { ...USER, display_name: '' } })

    expect(screen.getByText('hakura')).toBeInTheDocument()
  })

  it('同步状态只在登录后出现，按状态各说各话', () => {
    const { unmount } = render(
      <AccountBar
        user={USER}
        checking={false}
        syncState="syncing"
        onOpenAuth={vi.fn()}
        onLogout={vi.fn()}
      />,
    )
    expect(screen.getByText('记忆同步中…')).toBeInTheDocument()
    unmount()

    render(
      <AccountBar
        user={USER}
        checking={false}
        syncState="failed"
        onOpenAuth={vi.fn()}
        onLogout={vi.fn()}
      />,
    )
    expect(screen.getByText('记忆同步失败')).toBeInTheDocument()
  })

  it('未登录时不显示任何同步状态', () => {
    renderBar({ syncState: 'synced' })

    expect(screen.queryByText('记忆已同步')).toBeNull()
  })
})
