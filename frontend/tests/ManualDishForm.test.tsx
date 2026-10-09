import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ManualDishForm } from '../src/components/ManualDishForm'

const LABEL = '识别不准？手动输入菜名'

describe('ManualDishForm', () => {
  it('空输入时提交按钮不可用，输入后可用', async () => {
    const user = userEvent.setup()
    render(<ManualDishForm activeDish={null} onSubmit={() => {}} />)

    const submit = screen.getByRole('button', { name: '用这个菜名' })
    expect(submit).toBeDisabled()

    await user.type(screen.getByLabelText(LABEL), '红烧肉')
    expect(submit).toBeEnabled()
  })

  it('提交时去掉首尾空白再回调', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ManualDishForm activeDish={null} onSubmit={onSubmit} />)

    await user.type(screen.getByLabelText(LABEL), '  红烧肉  ')
    await user.click(screen.getByRole('button', { name: '用这个菜名' }))

    expect(onSubmit).toHaveBeenCalledWith('红烧肉')
  })

  it('纯空白输入不回调，正常输入可直接回车提交', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ManualDishForm activeDish={null} onSubmit={onSubmit} />)

    const input = screen.getByLabelText(LABEL)
    await user.type(input, '   {Enter}')
    expect(onSubmit).not.toHaveBeenCalled()

    await user.type(input, '蛋包饭{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('蛋包饭')
  })

  it('展示当前生效的手输菜名', () => {
    render(<ManualDishForm activeDish="红烧肉" onSubmit={() => {}} />)

    expect(screen.getByText('当前按手输菜名解析：红烧肉')).toBeInTheDocument()
  })
})
