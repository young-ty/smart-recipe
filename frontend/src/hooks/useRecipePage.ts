// 页面级单一状态源：useReducer 集中管理，配合「后发先至」请求保护（设计方案 4.4）
//
// 成品菜方向的状态机只有一条主线：选图 → 识别出候选菜品 → 选中某个候选 →
// 按菜名解析菜谱（库内优先，其次大模型生成，都没有则明确降级）。

import { useCallback, useEffect, useReducer, useRef } from 'react'

import { ApiError, fetchHealth, recognizeImage, resolveRecipe } from '../api/client'
import type { MemoryEntry } from '../memory'
import type {
  DishCandidate,
  ModelInfo,
  RecipeOut,
  RecipeSource,
  ResolveResponse,
} from '../types'

export type RecognizeStatus = 'idle' | 'recognizing' | 'ready' | 'error'

export type SelectedImage = {
  file: File
  previewUrl: string
}

export type ResolveState = {
  loading: boolean
  dish: string
  source: RecipeSource | null
  recipe: RecipeOut | null
  reason: string | null
  error: string | null
  /** 非 null 表示当前显示的是本机记忆里的菜谱（FR-15），没有重新请求后端 */
  rememberedAt: number | null
}

export const EMPTY_RESOLVE: ResolveState = {
  loading: false,
  dish: '',
  source: null,
  recipe: null,
  reason: null,
  error: null,
  rememberedAt: null,
}

export type PageState = {
  status: RecognizeStatus
  image: SelectedImage | null
  candidates: DishCandidate[]
  /** 模型候选的选中项；与 manualDish 互斥 */
  selectedIndex: number | null
  /** 手动纠正的菜名（FR-10 兜底）；非空时 selectedIndex 必为 null */
  manualDish: string | null
  /** 手输提交次数：同名再提交也要重新解析，供解析 effect 作依赖 */
  manualSeq: number
  resolve: ResolveState
  error: string | null
  model: ModelInfo | null
}

export const initialPageState: PageState = {
  status: 'idle',
  image: null,
  candidates: [],
  selectedIndex: null,
  manualDish: null,
  manualSeq: 0,
  resolve: EMPTY_RESOLVE,
  error: null,
  model: null,
}

export type PageAction =
  | { type: 'select-image'; image: SelectedImage }
  | { type: 'recognize-start' }
  | { type: 'recognize-success'; candidates: DishCandidate[]; model: ModelInfo }
  | { type: 'recognize-failure'; error: string }
  | { type: 'select-candidate'; index: number }
  | { type: 'submit-manual-dish'; dish: string }
  | { type: 'resolve-start' }
  | {
      type: 'resolve-success'
      dish: string
      source: RecipeSource
      recipe: RecipeOut | null
      reason: string | null
    }
  | { type: 'resolve-failure'; error: string }
  | { type: 'show-memory'; entry: MemoryEntry }
  | { type: 'model-status'; model: ModelInfo }
  | { type: 'reset' }

/** 识别成功后默认选中 top-1；候选为空时保持未选中 */
export function defaultSelection(candidates: DishCandidate[]): number | null {
  return candidates.length > 0 ? candidates[0].index : null
}

export function pageReducer(state: PageState, action: PageAction): PageState {
  switch (action.type) {
    case 'select-image':
      // 换图即作废上一轮的候选与菜谱，但不影响健康检查拿到的模型信息（记忆也不受影响）
      return { ...initialPageState, image: action.image, model: state.model }
    case 'recognize-start':
      return {
        ...state,
        status: 'recognizing',
        error: null,
        candidates: [],
        selectedIndex: null,
        manualDish: null,
        resolve: EMPTY_RESOLVE,
      }
    case 'recognize-success':
      return {
        ...state,
        status: 'ready',
        error: null,
        candidates: action.candidates,
        selectedIndex: defaultSelection(action.candidates),
        manualDish: null,
        resolve: EMPTY_RESOLVE,
        model: action.model,
      }
    case 'recognize-failure':
      return { ...state, status: 'error', error: action.error }
    case 'select-candidate':
      // 换候选必须丢掉上一道菜的结果，否则会显示成错菜谱
      return {
        ...state,
        selectedIndex: action.index,
        manualDish: null,
        resolve: { ...EMPTY_RESOLVE, loading: true },
      }
    case 'submit-manual-dish':
      // 手输与候选互斥：清空选中下标，改按菜名（不带类别下标）解析
      return {
        ...state,
        selectedIndex: null,
        manualDish: action.dish,
        manualSeq: state.manualSeq + 1,
        resolve: { ...EMPTY_RESOLVE, loading: true },
      }
    case 'resolve-start':
      return { ...state, resolve: { ...state.resolve, loading: true, error: null } }
    case 'resolve-success':
      return {
        ...state,
        resolve: {
          loading: false,
          dish: action.dish,
          source: action.source,
          recipe: action.recipe,
          reason: action.reason,
          error: null,
          rememberedAt: null,
        },
      }
    case 'show-memory':
      // 记忆里的菜谱直接展示：不发请求，但要清掉候选与手输，避免两套来源打架
      return {
        ...state,
        selectedIndex: null,
        manualDish: null,
        resolve: {
          loading: false,
          dish: action.entry.dish,
          source: action.entry.source,
          recipe: action.entry.recipe,
          reason: null,
          error: null,
          rememberedAt: action.entry.at,
        },
      }
    case 'resolve-failure':
      return { ...state, resolve: { ...EMPTY_RESOLVE, error: action.error } }
    case 'model-status':
      return { ...state, model: action.model }
    case 'reset':
      // 模型状态来自服务健康检查，与单次识别无关，重置时保留
      return { ...initialPageState, model: state.model }
    default:
      return state
  }
}

export function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return '未知错误'
}

export type RecipePageController = {
  state: PageState
  selectImage: (file: File) => void
  recognize: () => Promise<void>
  selectCandidate: (index: number) => void
  submitManualDish: (dish: string) => void
  /** 复用记忆里的菜谱：不发请求，直接展示存下来的那一份 */
  showMemory: (entry: MemoryEntry) => void
  reset: () => void
}

export type RecipePageOptions = {
  /** 取到菜谱后写进本机记忆（FR-15） */
  onResolved?: (entry: MemoryEntry) => void
  /** 本机记忆命中查询：识别到做过的菜时直接复用，省掉一次请求与生成开销（FR-15） */
  findRemembered?: (dish: string) => MemoryEntry | null
}

/** 手输的菜名没有类别下标，这时只按菜名解析（归一化与别名匹配都在后端） */
function requestRecipe(dish: string, index: number | null): Promise<ResolveResponse> {
  return index === null ? resolveRecipe(dish) : resolveRecipe(dish, index)
}

export function useRecipePage(options: RecipePageOptions = {}): RecipePageController {
  const { onResolved, findRemembered } = options
  const [state, dispatch] = useReducer(pageReducer, initialPageState)
  const previewUrlRef = useRef<string | null>(null)
  const resolveTokenRef = useRef(0)
  // 识别响应里的模型信息比启动时的健康检查更新，一旦拿到就不再被健康检查覆盖
  const modelFromRecognitionRef = useRef(false)

  // 记忆回调与记忆查询都不参与「重新解析」的触发条件：放进 ref 跟随最新一次渲染，
  // 这样记忆更新不会把当前这道菜重算一遍。
  const latestRef = useRef({ onResolved, findRemembered })
  latestRef.current = { onResolved, findRemembered }

  const releasePreview = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current)
      previewUrlRef.current = null
    }
  }, [])

  const selectImage = useCallback(
    (file: File) => {
      releasePreview()
      const previewUrl = URL.createObjectURL(file)
      previewUrlRef.current = previewUrl
      resolveTokenRef.current += 1
      dispatch({ type: 'select-image', image: { file, previewUrl } })
    },
    [releasePreview],
  )

  const recognize = useCallback(async () => {
    const file = state.image?.file
    if (!file) {
      return
    }
    dispatch({ type: 'recognize-start' })
    try {
      const result = await recognizeImage(file)
      modelFromRecognitionRef.current = true
      dispatch({
        type: 'recognize-success',
        candidates: result.candidates,
        model: {
          loaded: !result.degraded,
          version: result.model_version,
          degraded: result.degraded,
          class_count: result.class_count,
          labels_available: result.labels_available,
        },
      })
    } catch (error) {
      dispatch({ type: 'recognize-failure', error: messageOf(error) })
    }
  }, [state.image])

  const selectCandidate = useCallback((index: number) => {
    dispatch({ type: 'select-candidate', index })
  }, [])

  /** 手输菜名兜底（FR-10）：空输入不触发解析，首尾空白交给后端归一化前先去掉 */
  const submitManualDish = useCallback((dish: string) => {
    const trimmed = dish.trim()
    if (!trimmed) {
      return
    }
    dispatch({ type: 'submit-manual-dish', dish: trimmed })
  }, [])

  /** 复用记忆（FR-15）：直接展示存下来的菜谱；同时作废在途请求，避免迟到响应顶掉它 */
  const showMemory = useCallback((entry: MemoryEntry) => {
    resolveTokenRef.current += 1
    dispatch({ type: 'show-memory', entry })
  }, [])

  const reset = useCallback(() => {
    resolveTokenRef.current += 1
    releasePreview()
    dispatch({ type: 'reset' })
  }, [releasePreview])

  // 启动时读取服务健康检查，让模型标识在首次识别前就反映真实状态
  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then((health) => {
        if (cancelled || modelFromRecognitionRef.current) {
          return
        }
        dispatch({ type: 'model-status', model: health.model })
      })
      .catch(() => {
        // 健康检查失败不阻塞页面：占位文案保留，连接问题由识别流程就近提示
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 菜谱解析的来源只有两种：选中的候选，或手输的菜名（FR-10）。
  // 解析前先查本机记忆（FR-15）：命中就直接展示存下来的那一份，一个请求都不发——
  // 做过的菜（尤其库外菜）不必再让大模型生成一次，省下的正是 token 与等待时间。
  // 未命中的照旧发起请求，且仅采纳最后一次请求的响应，丢弃过期响应。
  const { selectedIndex, manualDish, manualSeq, candidates } = state
  useEffect(() => {
    let dish: string
    let index: number | null = null
    if (selectedIndex !== null) {
      const candidate = candidates.find((item) => item.index === selectedIndex)
      if (!candidate) {
        return
      }
      dish = candidate.name
      index = candidate.index
    } else if (manualDish !== null) {
      // 手输没有类别下标，只按菜名解析（归一化与别名匹配都在后端）
      dish = manualDish
    } else {
      return
    }
    const remembered = latestRef.current.findRemembered?.(dish) ?? null
    if (remembered !== null) {
      // 命中记忆：作废在途请求后直接展出，避免迟到的响应把它顶掉
      resolveTokenRef.current += 1
      dispatch({ type: 'show-memory', entry: remembered })
      return
    }

    const token = resolveTokenRef.current + 1
    resolveTokenRef.current = token
    dispatch({ type: 'resolve-start' })
    const pending = requestRecipe(dish, index)
    pending
      .then((body) => {
        if (token !== resolveTokenRef.current) {
          return
        }
        dispatch({
          type: 'resolve-success',
          dish: body.dish,
          source: body.source,
          recipe: body.recipe,
          reason: body.reason ?? null,
        })
        // 取到菜谱就记一笔（FR-15）：库外的菜下次直接复用，不必重新上传识别
        if (body.recipe !== null) {
          latestRef.current.onResolved?.({
            dish: body.dish,
            name: body.recipe.name,
            source: body.source,
            recipe: body.recipe,
            at: Date.now(),
            // 新记的条目默认未收藏；原本收藏过的由 rememberEntry 保留标记
            favorite: false,
          })
        }
      })
      .catch((error: unknown) => {
        if (token === resolveTokenRef.current) {
          dispatch({ type: 'resolve-failure', error: messageOf(error) })
        }
      })
  }, [selectedIndex, manualDish, manualSeq, candidates])

  useEffect(() => releasePreview, [releasePreview])

  return {
    state,
    selectImage,
    recognize,
    selectCandidate,
    submitManualDish,
    showMemory,
    reset,
  }
}
