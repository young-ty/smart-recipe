import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AuthMode } from '../src/account'
import { ApiError } from '../src/api/client'
import { AuthDialog } from '../src/components/AuthDialog'

type Values = { email: string; password: string; displayName: string }

function renderDialog(overrides: Partial<Parameters<typeof AuthDialog>[0]> = {}) {
  const onSubmit = vi.fn(async (_mode: AuthMode, _values: Values) => {})
  const onClose = vi.fn()
  render(<AuthDialog open available onClose={onClose} onSubmit={onSubmit} {...overrides} />)
  return { onSubmit, onClose }
}

const submitButton = () => screen.getByRole('button', { name: '登录' })

describe('AuthDialog', () => {
  it('没打开时不渲染任何东西', () => {
    renderDialog({ open: false })

    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('默认是登录：只要邮箱与密码', () => {
    renderDialog()

    expect(screen.getByRole('dialog', { name: '登录' })).toBeInTheDocument()
    expect(screen.getByLabelText('邮箱')).toBeInTheDocument()
    expect(screen.getByLabelText('密码')).toBeInTheDocument()
    expect(screen.queryByLabelText('显示名（可留空）')).toBeNull()
  })

  it('切到注册后多出显示名与密码长度提示', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole('button', { name: '注册一个' }))

    expect(screen.getByRole('dialog', { name: '注册' })).toBeInTheDocument()
    expect(screen.getByLabelText('显示名（可留空）')).toBeInTheDocument()
    expect(screen.getByText('至少 8 位')).toBeInTheDocument()
  })

  it('邮箱格式不对时当场说清，不发请求', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()

    // 少了域名后缀：浏览器的原生邮箱校验放行，由前端这条规则接手
    await user.type(screen.getByLabelText('邮箱'), 'hakura@example')
    await user.type(screen.getByLabelText('密码'), 'password123')
    await user.click(submitButton())

    expect(screen.getByRole('alert')).toHaveTextContent('邮箱格式不对')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('注册时密码太短同样在当地拦下', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()

    await user.click(screen.getByRole('button', { name: '注册一个' }))
    await user.type(screen.getByLabelText('邮箱'), 'hakura@example.com')
    await user.type(screen.getByLabelText('密码'), '1234567')
    await user.click(screen.getByRole('button', { name: '注册' }))

    expect(screen.getByRole('alert')).toHaveTextContent('密码至少 8 位')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('填对了就交给上层，成功后自己关上', async () => {
    const user = userEvent.setup()
    const { onSubmit, onClose } = renderDialog()

    await user.type(screen.getByLabelText('邮箱'), 'Hakura@Example.com')
    await user.type(screen.getByLabelText('密码'), 'password123')
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith('login', {
      email: 'Hakura@Example.com',
      password: 'password123',
      displayName: '',
    })
    expect(onClose).toHaveBeenCalled()
  })

  it('服务端说密码不对时显示在同一处，弹窗留着让人改', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn(async () => {
      throw new ApiError(40101, '请先登录', 401)
    })
    const onClose = vi.fn()
    render(<AuthDialog open available onClose={onClose} onSubmit={onSubmit} />)

    await user.type(screen.getByLabelText('邮箱'), 'hakura@example.com')
    await user.type(screen.getByLabelText('密码'), 'wrong-password')
    await user.click(submitButton())

    expect(await screen.findByRole('alert')).toHaveTextContent('邮箱或密码不正确')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('注册撞上已存在的邮箱时说清可以直接登录', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn(async () => {
      throw new ApiError(40901, '邮箱已被注册', 409)
    })
    render(<AuthDialog open available onClose={vi.fn()} onSubmit={onSubmit} />)

    await user.click(screen.getByRole('button', { name: '注册一个' }))
    await user.type(screen.getByLabelText('邮箱'), 'hakura@example.com')
    await user.type(screen.getByLabelText('密码'), 'password123')
    await user.click(screen.getByRole('button', { name: '注册' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('已经注册过了')
  })

  it('Esc 关闭', async () => {
    const user = userEvent.setup()
    const { onClose } = renderDialog()

    await user.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalled()
  })

  it('演示站没有后端时说明原因并挡住提交', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog({ available: false })

    expect(screen.getByText(/静态演示站不带后端服务/)).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    expect(screen.getByLabelText('邮箱')).toBeDisabled()

    await user.click(submitButton())
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
