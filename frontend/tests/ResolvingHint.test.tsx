import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ResolvingHint } from '../src/components/ResolvingHint'

afterEach(() => {
  vi.useRealTimers()
})

describe('ResolvingHint', () => {
  it('按分支说明在等什么', () => {
    render(<ResolvingHint kind="generate" />)

    expect(screen.getByRole('status')).toHaveTextContent('库里没有这道菜，正在让大模型现写…')
  })

  it('等待时间自己往上走，只有这一行会更新', () => {
    vi.useFakeTimers()
    render(<ResolvingHint kind="unknown" />)

    act(() => {
      vi.advanceTimersByTime(4000)
    })

    expect(screen.getByRole('status')).toHaveTextContent('（已等 4 秒；库里没有的话会改由大模型现写')
  })

  it('卸载后不再走秒，不留定时器', () => {
    vi.useFakeTimers()
    const clearInterval = vi.spyOn(window, 'clearInterval')
    const { unmount } = render(<ResolvingHint kind="library" />)

    unmount()

    expect(clearInterval).toHaveBeenCalled()
    clearInterval.mockRestore()
  })
})
