import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, recognizeImage: vi.fn(), resolveRecipe: vi.fn(), fetchHealth: vi.fn() }
})

import { ApiError, fetchHealth, recognizeImage, resolveRecipe } from '../src/api/client'
import {
  EMPTY_RESOLVE,
  defaultSelection,
  initialPageState,
  pageReducer,
  useRecipePage,
} from '../src/hooks/useRecipePage'
import type { ResolveResponse } from '../src/types'
import {
  CANDIDATES,
  HEALTH,
  LIBRARY,
  PROVISIONAL_RECOGNIZED,
  RECOGNIZED,
  generatedRecipe,
  memoryEntry,
  picture,
} from './fixtures'

beforeEach(() => {
  vi.mocked(recognizeImage).mockReset()
  vi.mocked(resolveRecipe).mockReset()
  vi.mocked(fetchHealth).mockReset()
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH)
  vi.mocked(resolveRecipe).mockResolvedValue(LIBRARY)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('defaultSelection', () => {
  it('默认选中 top-1 候选', () => {
    expect(defaultSelection(CANDIDATES)).toBe(77)
  })

  it('候选为空时不选中任何项', () => {
    expect(defaultSelection([])).toBeNull()
  })
})

describe('pageReducer', () => {
  it('换图会作废上一轮的候选、选中项与菜谱', () => {
    const ready = pageReducer(initialPageState, {
      type: 'recognize-success',
      candidates: CANDIDATES,
      model: HEALTH.model,
    })
    const next = pageReducer(ready, {
      type: 'select-image',
      image: { file: picture(), previewUrl: 'blob:1' },
    })

    expect(next.image?.previewUrl).toBe('blob:1')
    expect(next.candidates).toEqual([])
    expect(next.selectedIndex).toBeNull()
    expect(next.resolve.recipe).toBeNull()
  })

  it('识别成功把选中项重置为 top-1', () => {
    const dirty = { ...initialPageState, selectedIndex: 110 }
    const next = pageReducer(dirty, {
      type: 'recognize-success',
      candidates: CANDIDATES,
      model: HEALTH.model,
    })

    expect(next.status).toBe('ready')
    expect(next.selectedIndex).toBe(77)
    expect(next.error).toBeNull()
  })

  it('识别失败保留已选图片，便于直接重试', () => {
    const ready = {
      ...initialPageState,
      image: { file: picture(), previewUrl: 'blob:1' },
      status: 'ready' as const,
    }
    const next = pageReducer(ready, { type: 'recognize-failure', error: '服务内部错误，请稍后重试' })

    expect(next.status).toBe('error')
    expect(next.image?.previewUrl).toBe('blob:1')
  })

  it('切换候选会丢掉上一道菜的结果并进入加载态', () => {
    const shown = pageReducer(initialPageState, {
      type: 'resolve-success',
      dish: '红烧肉',
      source: 'library',
      recipe: LIBRARY.recipe,
      reason: null,
    })
    const next = pageReducer(shown, { type: 'select-candidate', index: 110 })

    expect(next.selectedIndex).toBe(110)
    expect(next.resolve.recipe).toBeNull()
    expect(next.resolve.source).toBeNull()
    // 换候选只是清空上一份结果：扒谱要等用户再点一次按钮
    expect(next.resolve.loading).toBe(false)
    expect(next.resolveRequest).toBeNull()
  })

  it('识别出候选不自动扒谱，点按钮才产生解析请求', () => {
    const ready = pageReducer(initialPageState, {
      type: 'recognize-success',
      candidates: CANDIDATES,
      model: HEALTH.model,
    })

    // 识别完先选上 top-1，但一个请求都不发
    expect(ready.selectedIndex).toBe(77)
    expect(ready.resolveRequest).toBeNull()
    expect(ready.resolve).toEqual(EMPTY_RESOLVE)

    const asked = pageReducer(ready, { type: 'request-recipe' })

    expect(asked.resolveRequest).toEqual({ dish: '红烧肉', index: 77, seq: 1 })
    expect(asked.resolve.loading).toBe(true)
  })

  it('没有选中候选时点扒谱不产生请求', () => {
    const ready = pageReducer(initialPageState, { type: 'recognize-success', candidates: [], model: HEALTH.model })

    expect(pageReducer(ready, { type: 'request-recipe' })).toBe(ready)
  })

  it('手输菜名会清空候选选中项并进入加载态', () => {
    const ready = pageReducer(initialPageState, {
      type: 'recognize-success',
      candidates: CANDIDATES,
      model: HEALTH.model,
    })
    const next = pageReducer(ready, { type: 'submit-manual-dish', dish: '麻婆豆腐' })

    expect(next.selectedIndex).toBeNull()
    expect(next.manualDish).toBe('麻婆豆腐')
    expect(next.resolve.loading).toBe(true)
    // 手输与候选互斥，换道菜必须先丢掉上一份结果
    expect(next.resolve.recipe).toBeNull()
  })

  it('同名手输再次提交会推进提交次数，切回候选会清掉手输菜名', () => {
    const manual = pageReducer(initialPageState, { type: 'submit-manual-dish', dish: '麻婆豆腐' })
    const again = pageReducer(manual, { type: 'submit-manual-dish', dish: '麻婆豆腐' })
    const picked = pageReducer(again, { type: 'select-candidate', index: 77 })

    expect(again.manualSeq).toBe(manual.manualSeq + 1)
    expect(picked.manualDish).toBeNull()
    expect(picked.selectedIndex).toBe(77)
  })

  it('换图会一并清掉手输菜名', () => {
    const manual = pageReducer(initialPageState, { type: 'submit-manual-dish', dish: '麻婆豆腐' })
    const next = pageReducer(manual, {
      type: 'select-image',
      image: { file: picture(), previewUrl: 'blob:1' },
    })

    expect(next.manualDish).toBeNull()
    expect(next.manualSeq).toBe(0)
  })
  it('展示记忆会清掉候选选中与手输菜名，并标记来源是记忆', () => {
    const ready = pageReducer(initialPageState, {
      type: 'recognize-success',
      candidates: CANDIDATES,
      model: HEALTH.model,
    })
    const next = pageReducer(ready, { type: 'show-memory', entry: memoryEntry({ at: 123 }) })

    expect(next.selectedIndex).toBeNull()
    expect(next.manualDish).toBeNull()
    expect(next.resolve.loading).toBe(false)
    expect(next.resolve.recipe?.name).toBe('红烧肉')
    expect(next.resolve.rememberedAt).toBe(123)
  })
  it('不可用分支保留后端给出的原因，不伪装成空白', () => {
    const next = pageReducer(initialPageState, {
      type: 'resolve-failure',
      error: '无法连接后端服务，请确认服务已启动',
    })

    expect(next.resolve.recipe).toBeNull()
    expect(next.resolve.error).toBe('无法连接后端服务，请确认服务已启动')
  })

  it('reset 回复初始态但保留模型信息', () => {
    const withModel = pageReducer(initialPageState, { type: 'model-status', model: HEALTH.model })
    const dirty = { ...withModel, selectedIndex: 77, error: 'x' }

    const next = pageReducer(dirty, { type: 'reset' })

    expect(next.selectedIndex).toBeNull()
    expect(next.error).toBeNull()
    expect(next.model).toEqual(HEALTH.model)
  })
})

describe('useRecipePage', () => {
  it('识别成功后默认选中 top-1 并按菜名解析菜谱', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    expect(result.current.state.image?.previewUrl).toBeTruthy()

    await act(async () => {
      await result.current.recognize()
    })

    expect(result.current.state.status).toBe('ready')
    expect(result.current.state.selectedIndex).toBe(77)
    expect(result.current.state.model?.version).toBe('dish-convnext-base-208-onnx')
    // 识别本身不发请求：先点「扒谱」
    expect(resolveRecipe).not.toHaveBeenCalled()
    act(() => result.current.requestRecipe())

    await waitFor(() => expect(result.current.state.resolve.recipe?.name).toBe('红烧肉'))
    expect(resolveRecipe).toHaveBeenCalledWith('红烧肉', 77)
  })

  it('切换候选会按新的类别下标重新解析，且只采纳最后一次响应', async () => {
    // 这份样例走的是「类别清单缺失」的降级路径，第二候选是「类别 #110」
    vi.mocked(recognizeImage).mockResolvedValue(PROVISIONAL_RECOGNIZED)
    const resolvers: ((value: ResolveResponse) => void)[] = []
    vi.mocked(resolveRecipe).mockImplementation(
      () => new Promise<ResolveResponse>((resolve) => resolvers.push(resolve)),
    )

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    act(() => result.current.requestRecipe())
    expect(resolveRecipe).toHaveBeenCalledWith('红烧肉', 77)

    act(() => result.current.selectCandidate(110))
    // 换候选先清空上一份结果，要再点一次「扒谱」才解析新的那个
    expect(result.current.state.resolve.loading).toBe(false)
    act(() => result.current.requestRecipe())
    await waitFor(() => expect(resolveRecipe).toHaveBeenCalledWith('类别 #110', 110))
    expect(result.current.state.resolve.loading).toBe(true)

    await act(async () => {
      resolvers[1]({
        dish: '类别 #110',
        source: 'unavailable',
        degraded: true,
        recipe: null,
        reason: '类别清单缺失',
      })
    })
    // 迟到的旧响应不能覆盖新候选的结果
    await act(async () => {
      resolvers[0](LIBRARY)
    })

    expect(result.current.state.resolve.source).toBe('unavailable')
    expect(result.current.state.resolve.recipe).toBeNull()
    expect(result.current.state.resolve.reason).toBe('类别清单缺失')
  })

  it('解析失败时给出错误文案，候选列表不受影响', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockRejectedValue(new ApiError(50001, '无法连接后端服务，请确认服务已启动'))

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })

    act(() => result.current.requestRecipe())
    await waitFor(() => expect(result.current.state.resolve.error).toBe('无法连接后端服务，请确认服务已启动'))
    expect(result.current.state.candidates).toHaveLength(2)
    expect(result.current.state.selectedIndex).toBe(77)
  })

  it('启动时用健康检查填模型信息，识别响应优先', async () => {
    const healthResolvers: ((value: typeof HEALTH) => void)[] = []
    vi.mocked(fetchHealth).mockImplementation(
      () => new Promise((resolve) => healthResolvers.push(resolve)),
    )

    const { result } = renderHook(() => useRecipePage())
    await act(async () => {
      healthResolvers[0](HEALTH)
    })
    expect(result.current.state.model?.version).toBe('dish-convnext-base-208-onnx')

    vi.mocked(recognizeImage).mockResolvedValue({
      ...RECOGNIZED,
      degraded: true,
      model_version: 'mock-dish-convnext-base-208',
    })
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    expect(result.current.state.model?.degraded).toBe(true)

    await act(async () => {
      healthResolvers[0]({ ...HEALTH, model: { ...HEALTH.model, version: 'stale-health-version' } })
    })
    expect(result.current.state.model?.version).toBe('mock-dish-convnext-base-208')
  })

  it('候选都不对时按手输菜名解析，且不传类别下标', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockImplementation(async (dish) => ({ ...LIBRARY, dish }))

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    act(() => result.current.requestRecipe())
    await waitFor(() => expect(result.current.state.resolve.recipe).not.toBeNull())

    act(() => result.current.submitManualDish('  麻婆豆腐  '))

    // 手输没有类别下标：只按菜名请求，交给后端做归一化与别名匹配
    await waitFor(() => expect(resolveRecipe).toHaveBeenLastCalledWith('麻婆豆腐'))
    expect(result.current.state.selectedIndex).toBeNull()
    expect(result.current.state.manualDish).toBe('麻婆豆腐')
    await waitFor(() => expect(result.current.state.resolve.dish).toBe('麻婆豆腐'))
  })

  it('没有识别结果时也能直接按手输菜名查菜谱', async () => {
    const { result } = renderHook(() => useRecipePage())

    act(() => result.current.submitManualDish('红烧肉'))

    await waitFor(() => expect(result.current.state.resolve.recipe?.name).toBe('红烧肉'))
    expect(resolveRecipe).toHaveBeenCalledWith('红烧肉')
  })

  it('空输入不触发解析，也不改变状态', () => {
    const { result } = renderHook(() => useRecipePage())

    act(() => result.current.submitManualDish('   '))

    expect(resolveRecipe).not.toHaveBeenCalled()
    expect(result.current.state.manualDish).toBeNull()
    expect(result.current.state.resolve.loading).toBe(false)
  })

  it('手输后已发出的候选响应不会覆盖手输结果', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const resolvers: ((value: ResolveResponse) => void)[] = []
    vi.mocked(resolveRecipe).mockImplementation(
      () => new Promise<ResolveResponse>((resolve) => resolvers.push(resolve)),
    )

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    act(() => result.current.requestRecipe())
    expect(resolvers).toHaveLength(1)

    act(() => result.current.submitManualDish('麻婆豆腐'))
    await waitFor(() => expect(resolvers).toHaveLength(2))
    expect(resolveRecipe).toHaveBeenLastCalledWith('麻婆豆腐')

    await act(async () => {
      resolvers[1]({
        dish: '麻婆豆腐',
        source: 'generated',
        degraded: false,
        recipe: generatedRecipe('麻婆豆腐'),
        reason: null,
      })
    })
    // 迟到的候选响应不能把手输结果顶掉
    await act(async () => {
      resolvers[0](LIBRARY)
    })

    expect(result.current.state.resolve.dish).toBe('麻婆豆腐')
    expect(result.current.state.resolve.recipe?.name).toBe('麻婆豆腐')
    expect(result.current.state.resolve.source).toBe('generated')
  })

  it('同名手输再次提交会重新解析', async () => {
    const { result } = renderHook(() => useRecipePage())

    act(() => result.current.submitManualDish('蛋包饭'))
    await waitFor(() => expect(resolveRecipe).toHaveBeenCalledTimes(1))

    act(() => result.current.submitManualDish('蛋包饭'))

    await waitFor(() => expect(resolveRecipe).toHaveBeenCalledTimes(2))
  })

  it('手输后再选候选会切回该候选的类别下标', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    act(() => result.current.submitManualDish('麻婆豆腐'))
    await waitFor(() => expect(resolveRecipe).toHaveBeenLastCalledWith('麻婆豆腐'))

    act(() => result.current.selectCandidate(83))

    act(() => result.current.requestRecipe())
    await waitFor(() => expect(resolveRecipe).toHaveBeenLastCalledWith('梅菜扣肉', 83))
    expect(result.current.state.manualDish).toBeNull()
  })
  it('取到菜谱后写进记忆', async () => {
    const onResolved = vi.fn()
    const { result } = renderHook(() => useRecipePage({ onResolved }))

    act(() => result.current.submitManualDish('红烧肉'))
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1))

    const entry = onResolved.mock.calls[0][0]
    expect(entry.dish).toBe('红烧肉')
    expect(entry.name).toBe('红烧肉')
    expect(entry.source).toBe('library')
    expect(entry.recipe.name).toBe('红烧肉')
    expect(typeof entry.at).toBe('number')
  })

  it('不可用分支不写记忆', async () => {
    const onResolved = vi.fn()
    vi.mocked(resolveRecipe).mockResolvedValue({
      dish: '类别 #110',
      source: 'unavailable',
      degraded: true,
      recipe: null,
      reason: '类别清单缺失',
    })

    const { result } = renderHook(() => useRecipePage({ onResolved }))
    act(() => result.current.submitManualDish('类别 #110'))

    await waitFor(() => expect(result.current.state.resolve.reason).toBe('类别清单缺失'))
    expect(onResolved).not.toHaveBeenCalled()
  })

  it('解析请求只带菜名与类别下标，不夹带别的字段', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const { result } = renderHook(() => useRecipePage())

    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })

    act(() => result.current.requestRecipe())
    await waitFor(() => expect(resolveRecipe).toHaveBeenLastCalledWith('红烧肉', 77))
    expect(vi.mocked(resolveRecipe).mock.calls[0]).toHaveLength(2)
  })

  it('点记忆里的菜谱直接展示，不再请求后端', () => {
    const { result } = renderHook(() => useRecipePage())
    const entry = memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤', at: 456 })

    act(() => result.current.showMemory(entry))

    expect(result.current.state.resolve.recipe?.name).toBe('番茄蛋花汤')
    expect(result.current.state.resolve.source).toBe('library')
    expect(result.current.state.resolve.loading).toBe(false)
    expect(result.current.state.resolve.rememberedAt).toBe(456)
    expect(result.current.state.selectedIndex).toBeNull()
    expect(resolveRecipe).not.toHaveBeenCalled()
  })

  it('记忆展示不会被迟到的候选响应顶掉', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const resolvers: ((value: ResolveResponse) => void)[] = []
    vi.mocked(resolveRecipe).mockImplementation(
      () => new Promise<ResolveResponse>((resolve) => resolvers.push(resolve)),
    )

    const { result } = renderHook(() => useRecipePage())
    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })
    act(() => result.current.requestRecipe())
    expect(resolvers).toHaveLength(1)

    act(() => result.current.showMemory(memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤' })))
    await act(async () => {
      resolvers[0](LIBRARY)
    })

    expect(result.current.state.resolve.recipe?.name).toBe('番茄蛋花汤')
    expect(result.current.state.resolve.rememberedAt).not.toBeNull()
  })
  it('识别到做过的菜时命中记忆：不再请求后端，直接展示存下来的菜谱', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const remembered = memoryEntry({ dish: '红烧肉', at: 789 })
    const findRemembered = vi.fn((dish: string) => (dish === '红烧肉' ? remembered : null))
    const { result } = renderHook(() => useRecipePage({ findRemembered }))

    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })

    act(() => result.current.requestRecipe())
    expect(resolveRecipe).not.toHaveBeenCalled()
    expect(result.current.state.resolve.recipe).toBe(remembered.recipe)
    expect(result.current.state.resolve.loading).toBe(false)
    expect(result.current.state.resolve.rememberedAt).toBe(789)
    expect(findRemembered).toHaveBeenCalledWith('红烧肉')
  })

  it('记忆未命中时照旧请求后端', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const findRemembered = vi.fn(() => null)
    const { result } = renderHook(() => useRecipePage({ findRemembered }))

    act(() => result.current.selectImage(picture()))
    await act(async () => {
      await result.current.recognize()
    })

    act(() => result.current.requestRecipe())
    await waitFor(() => expect(resolveRecipe).toHaveBeenCalledWith('红烧肉', 77))
    expect(result.current.state.resolve.rememberedAt).toBeNull()
  })

  it('命中记忆时作废在途请求，迟到的手输响应不会顶掉它', async () => {
    const resolvers: ((value: ResolveResponse) => void)[] = []
    vi.mocked(resolveRecipe).mockImplementation(
      () => new Promise<ResolveResponse>((resolve) => resolvers.push(resolve)),
    )
    const remembered = memoryEntry({ dish: '麻婆豆腐', name: '麻婆豆腐', source: 'generated', at: 111 })
    const findRemembered = (dish: string) => (dish === '麻婆豆腐' ? remembered : null)
    const { result } = renderHook(() => useRecipePage({ findRemembered }))

    act(() => result.current.submitManualDish('红烧肉'))
    expect(resolvers).toHaveLength(1)

    act(() => result.current.submitManualDish('麻婆豆腐'))
    await act(async () => {
      resolvers[0](LIBRARY)
    })

    expect(result.current.state.resolve.recipe?.name).toBe('麻婆豆腐')
    expect(result.current.state.resolve.rememberedAt).toBe(111)
  })
  it('健康检查失败时模型信息保持为空', async () => {
    vi.mocked(fetchHealth).mockRejectedValue(
      new ApiError(50001, '无法连接后端服务，请确认服务已启动'),
    )

    const { result } = renderHook(() => useRecipePage())
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(result.current.state.model).toBeNull()
  })
})
