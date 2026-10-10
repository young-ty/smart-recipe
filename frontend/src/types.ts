// 与后端 schemas 一一对应的类型定义（设计方案 5.3）

export type DishCandidate = {
  /** 类别下标，取值 0 ~ class_count-1 */
  index: number
  /** 菜名；类别清单未交付时为「类别 #N」 */
  name: string
  confidence: number
  provisional_name: boolean
}

export type RecognizeResponse = {
  recognition_id: string
  model_version: string
  class_count: number
  labels_available: boolean
  degraded: boolean
  elapsed_ms: number
  candidates: DishCandidate[]
}

export type IngredientLine = {
  name: string
  amount?: string | null
}

/** library = 库内命中，generated = 大模型生成，unavailable = 明确降级 */
export type RecipeSource = 'library' | 'generated' | 'unavailable'

/** seed = 人工整理并入库；generated = 大模型生成（含生成后自动入库） */
export type RecipeOrigin = 'seed' | 'generated'

export type RecipeOut = {
  /** 库内菜谱 id；生成结果未入库时为 null */
  id: number | null
  /**
   * 来源标记。生成的菜谱落库后 source 会变成 library（下次直接命中库内），
   * 但 origin 仍是 generated——界面靠它标注「自动入库」而不是伪装成人工整理。
   */
  origin: RecipeOrigin
  name: string
  cook_time_minutes: number
  difficulty: string
  /** 生成结果不给评分 */
  rating: number | null
  servings: number
  description?: string | null
  required: IngredientLine[]
  optional: IngredientLine[]
  steps: string[]
  video_url: string
}

export type ResolveRequest = {
  dish: string
  index?: number | null
}

export type ResolveResponse = {
  dish: string
  source: RecipeSource
  degraded: boolean
  recipe: RecipeOut | null
  reason?: string | null
}

export type RecipeDetail = {
  recipe_id: number
  name: string
  origin: RecipeOrigin
  cook_time_minutes: number
  difficulty: string
  /** 生成后入库的菜谱没有评分 */
  rating: number | null
  servings: number
  description?: string | null
  image_url?: string | null
  required: IngredientLine[]
  optional: IngredientLine[]
  steps: string[]
  video_url: string
}

export type DishItem = {
  index: number
  name: string
  provisional_name: boolean
  library_recipe_id: number
}

export type DishListResponse = {
  class_count: number
  labels_available: boolean
  library_recipe_count: number
  covered_class_count: number
  items: DishItem[]
}

export type ModelInfo = {
  loaded: boolean
  version: string
  degraded: boolean
  class_count: number
  labels_available: boolean
}

export type HealthResponse = {
  status: string
  database: string
  recipe_count: number
  model: ModelInfo
  generator: { configured: boolean; model: string }
}

/** 登录账号：服务端只回这几个字段，密码哈希与任何令牌都不出后端 */
export type AccountUser = {
  id: number
  email: string
  display_name: string
  created_at: string
}
