import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useRecipeMemory } from '../src/hooks/useRecipeMemory'
import { MEMORY_STORAGE_KEY, readMemory } from '../src/memory'
import { memoryEntry } from './fixtures'

beforeEach(() => {
  localStorage.clear()
})

describe('useRecipeMemory', () => {
  it('初值直接来自本机存储', () => {
    localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify([memoryEntry({ dish: '番茄蛋花汤' })]))

    const { result } = renderHook(() => useRecipeMemory())

    expect(result.current.entries.map((entry) => entry.dish)).toEqual(['番茄蛋花汤'])
  })

  it('记一笔后排在最前并写回存储', () => {
    const { result } = renderHook(() => useRecipeMemory())

    act(() => result.current.remember(memoryEntry({ dish: '红烧肉' })))
    act(() => result.current.remember(memoryEntry({ dish: '番茄蛋花汤' })))

    expect(result.current.entries.map((entry) => entry.dish)).toEqual(['番茄蛋花汤', '红烧肉'])
    expect(readMemory().map((entry) => entry.dish)).toEqual(['番茄蛋花汤', '红烧肉'])
  })

  it('同名再次记入只保留最近一次', () => {
    const { result } = renderHook(() => useRecipeMemory())

    act(() => result.current.remember(memoryEntry({ dish: '红烧肉', at: 1 })))
    act(() => result.current.remember(memoryEntry({ dish: '红烧肉', at: 9, source: 'generated' })))

    expect(result.current.entries).toHaveLength(1)
    expect(result.current.entries[0].source).toBe('generated')
  })

  it('find 命中做过的菜，没做过则返回 null', () => {
    const { result } = renderHook(() => useRecipeMemory())
    act(() => result.current.remember(memoryEntry({ dish: '红烧肉' })))

    expect(result.current.find('红烧肉')?.dish).toBe('红烧肉')
    expect(result.current.find('番茄蛋花汤')).toBeNull()
  })

  it('清空后存储里也不再有记录', () => {
    const { result } = renderHook(() => useRecipeMemory())
    act(() => result.current.remember(memoryEntry()))

    act(() => result.current.clear())

    expect(result.current.entries).toEqual([])
    expect(readMemory()).toEqual([])
  })

})
