import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, fetchMemories: vi.fn(), putMemories: vi.fn() }
})

import { fetchMemories, putMemories } from '../src/api/client'
import { useMemorySync } from '../src/hooks/useMemorySync'
import { useRecipeMemory } from '../src/hooks/useRecipeMemory'
import { MEMORY_STORAGE_KEY, readMemory } from '../src/memory'
import type { AccountUser } from '../src/types'
import { memoryEntry } from './fixtures'

const USER: AccountUser = {
  id: 7,
  email: 'hakura@example.com',
  display_name: 'hakura',
  created_at: '2026-10-10T00:00:00',
}

function useHarness(user: AccountUser | null) {
  const memory = useRecipeMemory()
  const state = useMemorySync(user, memory)
  return { memory, state }
}

function renderSync(initial: AccountUser | null = null) {
  return renderHook(({ user }) => useHarness(user), { initialProps: { user: initial } })
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(fetchMemories).mockReset()
  vi.mocked(putMemories).mockReset()
  vi.mocked(fetchMemories).mockResolvedValue({ entries: [] })
  vi.mocked(putMemories).mockImplementation(async (entries) => ({ entries }))
})

describe('useMemorySync 未登录', () => {
  it('一个请求都不发，状态留在 idle', async () => {
    const { result } = renderSync(null)

    await act(async () => {})

    expect(fetchMemories).not.toHaveBeenCalled()
    expect(putMemories).not.toHaveBeenCalled()
    expect(result.current.state).toBe('idle')
  })
})

describe('useMemorySync 登录后合并', () => {
  it('服务端那份并进本机，合并结果推一次上去', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '本机的菜', at: 5 })]),
    )
    vi.mocked(fetchMemories).mockResolvedValue({
      entries: [memoryEntry({ dish: '服务端的菜', at: 1 })],
    })
    const { result, rerender } = renderSync(null)

    rerender({ user: USER })

    await waitFor(() => expect(result.current.state).toBe('synced'))
    expect(result.current.memory.entries.map((entry) => entry.dish)).toEqual([
      '本机的菜',
      '服务端的菜',
    ])
    // 合并结果回写本机的那次改动不该再触发一遍同样的推送
    await act(async () => {})
    expect(putMemories).toHaveBeenCalledTimes(1)
    expect(vi.mocked(putMemories).mock.calls[0][0].map((entry) => entry.dish)).toEqual([
      '本机的菜',
      '服务端的菜',
    ])
  })

  it('合并后本机再记一道菜，会再推一次', async () => {
    const { result, rerender } = renderSync(null)
    rerender({ user: USER })
    await waitFor(() => expect(result.current.state).toBe('synced'))

    act(() => result.current.memory.remember(memoryEntry({ dish: '新做的菜', at: 99 })))

    await waitFor(() => expect(putMemories).toHaveBeenCalledTimes(2))
    expect(vi.mocked(putMemories).mock.calls[1][0][0].dish).toBe('新做的菜')
  })

  it('收藏在合并后照样留在本机，不会被服务端那份盖掉', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '红烧肉', at: 5, favorite: true })]),
    )
    vi.mocked(fetchMemories).mockResolvedValue({
      entries: [memoryEntry({ dish: '红烧肉', at: 9, favorite: false })],
    })
    const { result, rerender } = renderSync(null)

    rerender({ user: USER })

    await waitFor(() => expect(result.current.state).toBe('synced'))
    expect(result.current.memory.entries[0].favorite).toBe(true)
  })
})

describe('useMemorySync 失败与退出', () => {
  it('拉取失败时状态是 failed，本机记忆原样保留', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '本机的菜', at: 5 })]),
    )
    vi.mocked(fetchMemories).mockRejectedValue(new Error('后端没起来'))
    const { result, rerender } = renderSync(null)

    rerender({ user: USER })

    await waitFor(() => expect(result.current.state).toBe('failed'))
    expect(result.current.memory.entries.map((entry) => entry.dish)).toEqual(['本机的菜'])
  })

  it('推送失败时状态也是 failed，本机内容不改', async () => {
    vi.mocked(putMemories).mockRejectedValue(new Error('后端没起来'))
    const { result, rerender } = renderSync(null)

    rerender({ user: USER })

    await waitFor(() => expect(result.current.state).toBe('failed'))
  })

  it('退出登录回到 idle，本机记忆不清空', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '本机的菜', at: 5 })]),
    )
    const { result, rerender } = renderSync(null)
    rerender({ user: USER })
    await waitFor(() => expect(result.current.state).toBe('synced'))

    rerender({ user: null })

    await waitFor(() => expect(result.current.state).toBe('idle'))
    expect(result.current.memory.entries.map((entry) => entry.dish)).toEqual(['本机的菜'])
    expect(readMemory().map((entry) => entry.dish)).toEqual(['本机的菜'])
  })
})
