// 前端测试共用的后端响应样例（口径与 backend/tests/test_api.py 一致）

import type { MemoryEntry } from '../src/memory'
import type {
  DishCandidate,
  HealthResponse,
  RecipeOut,
  RecognizeResponse,
  ResolveResponse,
} from '../src/types'

// 演示图「红烧肉」在第二代 convnext 权重上的真实输出（前两名），名称取自交付清单
export const CANDIDATES: DishCandidate[] = [
  { index: 77, name: '红烧肉', confidence: 0.885, provisional_name: false },
  { index: 83, name: '梅菜扣肉', confidence: 0.007, provisional_name: false },
]

export const RECOGNIZED: RecognizeResponse = {
  recognition_id: 'd9f1c2a4-7b31-4b0e-9f2a-1c5e8a7d3b60',
  model_version: 'dish-convnext-base-208-onnx',
  class_count: 208,
  labels_available: true,
  degraded: false,
  elapsed_ms: 143,
  candidates: CANDIDATES,
}

/** 类别清单缺失时的降级样例：名称退化为「类别 #N」并带待确认标记 */
export const PROVISIONAL_CANDIDATES: DishCandidate[] = [
  { index: 77, name: '红烧肉', confidence: 0.819, provisional_name: true },
  { index: 110, name: '类别 #110', confidence: 0.008, provisional_name: true },
]

export const PROVISIONAL_RECOGNIZED: RecognizeResponse = {
  ...RECOGNIZED,
  labels_available: false,
  candidates: PROVISIONAL_CANDIDATES,
}

export const LIBRARY_RECIPE: RecipeOut = {
  id: 109,
  origin: 'seed',
  name: '红烧肉',
  cook_time_minutes: 90,
  difficulty: '中等',
  rating: 4.7,
  servings: 4,
  description: '小火慢煨，收汁到挂勺。',
  required: [{ name: '五花肉', amount: '500 克' }],
  optional: [{ name: '八角', amount: '2 颗' }],
  steps: ['五花肉切块焯水', '炒糖色后下肉'],
  video_url: 'https://www.bilibili.com/search?keyword=%E7%BA%A2%E7%83%A7%E8%82%89',
}

/** 大模型生成结果的样例：id 为 null（未落库），origin 为 generated */
export function generatedRecipe(name: string): RecipeOut {
  return { ...LIBRARY_RECIPE, id: null, name, origin: 'generated' }
}

/** 大模型生成并自动入库的样例：source 已是 library，但 origin 仍是 generated */
export function promotedRecipe(name: string): RecipeOut {
  return { ...LIBRARY_RECIPE, id: 10000, name, origin: 'generated' }
}

export const LIBRARY: ResolveResponse = {
  dish: '红烧肉',
  source: 'library',
  degraded: false,
  recipe: LIBRARY_RECIPE,
  reason: null,
}

export const UNAVAILABLE: ResolveResponse = {
  dish: '类别 #110',
  source: 'unavailable',
  degraded: true,
  recipe: null,
  reason: '类别清单缺失，且生成服务未配置，无法给出这道菜的做法',
}

export const HEALTH: HealthResponse = {
  status: 'ok',
  database: 'ok',
  recipe_count: 75,
  model: {
    loaded: true,
    version: 'dish-convnext-base-208-onnx',
    degraded: false,
    class_count: 208,
    labels_available: true,
  },
  generator: { configured: false, model: 'unset' },
}

export const picture = () => new File(['fake'], 'shot.png', { type: 'image/png' })

/** 记忆条目样例：默认就是库内的「红烧肉」 */
export function memoryEntry(overrides: Partial<MemoryEntry> = {}): MemoryEntry {
  const entry: MemoryEntry = {
    dish: '红烧肉',
    name: '红烧肉',
    source: 'library',
    recipe: LIBRARY_RECIPE,
    at: Date.parse('2026-09-22T12:00:00+08:00'),
    ...overrides,
  }
  // 生产代码里记忆条目的 name 就是菜谱标题，只改 name 的样例也保持一致
  if (overrides.name !== undefined && overrides.recipe === undefined) {
    return { ...entry, recipe: { ...entry.recipe, name: entry.name } }
  }
  return entry
}

