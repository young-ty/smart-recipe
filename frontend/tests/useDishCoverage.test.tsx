import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, fetchDishes: vi.fn() }
})

import { fetchDishes } from '../src/api/client'
import { useDishCoverage } from '../src/hooks/useDishCoverage'
import { DISHES } from './fixtures'

beforeEach(() => {
  vi.mocked(fetchDishes).mockReset()
})

describe('useDishCoverage', () => {
  it('取到清单后给出可承接的类别下标', async () => {
    vi.mocked(fetchDishes).mockResolvedValue(DISHES)

    const { result } = renderHook(() => useDishCoverage())

    expect(result.current).toBeNull()
    await waitFor(() => expect(result.current?.has(77)).toBe(true))
    expect(result.current?.has(83)).toBe(false)
  })

  it('清单取不到时保持 null，页面退回通用提示', async () => {
    vi.mocked(fetchDishes).mockRejectedValue(new Error('清单取不到'))

    const { result } = renderHook(() => useDishCoverage())

    await waitFor(() => expect(fetchDishes).toHaveBeenCalledTimes(1))
    expect(result.current).toBeNull()
  })
})
