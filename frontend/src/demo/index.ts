// 静态演示模式（GitHub Pages 上那份纯前端快照）。
//
// 线上演示站没有后端：208 类的 ONNX 有 335 MB，跑不动；没有后端也就没有大模型 Key。
// 所以这一版不假装自己在推理，而是把「真跑过一次」的结果直接摆出来——
// fixtures.json 由 backend/tools/export_demo_fixtures.py 生成，候选与置信度是
// models/dish.onnx 的真实输出，菜谱是同一条链路真实返回的那一份（库外的那些是
// 大模型生成后落库、再从库里导出来的）。
//
// 演示模式只在 `vite build --mode demo` 时打开，开发和联调走的仍是真后端。

import fixtures from 'virtual:demo-fixtures'
import type {
  DishCandidate,
  DishItem,
  DishListResponse,
  HealthResponse,
  RecipeOut,
  RecipeSource,
  ResolveResponse,
  RecognizeResponse,
} from '../types'

export const DEMO_MODE = import.meta.env.MODE === 'demo'

type FixtureSample = {
  id: string
  image: string
  caption: string
  note: string
  candidates: DishCandidate[]
}

type FixtureRecipe = {
  dish: string
  source: RecipeSource
  degraded: boolean
  recipe: RecipeOut | null
}

type FixtureFile = {
  model: { version: string; class_count: number }
  samples: FixtureSample[]
  recipes: Record<string, FixtureRecipe>
}

// 生成的 JSON 里 source / origin 是字符串，靠这里收束成联合类型，不做逐字段校验
const DATA = fixtures as unknown as FixtureFile

export const DEMO_MODEL_VERSION = DATA.model.version
export const DEMO_CLASS_COUNT = DATA.model.class_count

/** 演示模式里不能联网生成，库外菜只能明确降级，理由要写清楚是静态演示 */
export const DEMO_MISS_REASON = '静态演示站不带大模型：这道菜不在预置菜谱里'

export type DemoSampleOption = {
  id: string
  caption: string
  note: string
  /** 与识别结果对应的文件名：按名字查表，同一张图永远给同一份结果 */
  fileName: string
  /** 构建产物的 base 可能是 /仓库名/，图片地址一律走 BASE_URL */
  url: string
}

function fileNameOf(path: string): string {
  return path.split('/').pop() ?? path
}

function imageUrl(path: string): string {
  const base = import.meta.env.BASE_URL || '/'
  return `${base.endsWith('/') ? base : `${base}/`}${path}`
}

export const DEMO_SAMPLES: DemoSampleOption[] = DATA.samples.map((sample) => ({
  id: sample.id,
  caption: sample.caption,
  note: sample.note,
  fileName: fileNameOf(sample.image),
  url: imageUrl(sample.image),
}))

export function demoSampleById(id: string): DemoSampleOption | null {
  return DEMO_SAMPLES.find((sample) => sample.id === id) ?? null
}

/**
 * 演示模式下的「识别」：按文件名对上预置快照。
 *
 * 点示例图进来的一定能对上（文件名就是快照里的那张）；用户自己传的照片对不上，
 * 按文件名和体积取一份固定的快照，并在界面上说明这不是对这张照片的推理。
 */
export function demoRecognition(file: File): { sample: DemoSampleOption; matched: boolean } {
  const exact = DEMO_SAMPLES.find((sample) => sample.fileName === file.name)
  if (exact) {
    return { sample: exact, matched: true }
  }
  const key = `${file.name}:${file.size}`
  let hash = 0
  for (let position = 0; position < key.length; position += 1) {
    hash = (hash * 31 + key.charCodeAt(position)) % 100000007
  }
  return { sample: DEMO_SAMPLES[hash % DEMO_SAMPLES.length], matched: false }
}

function candidatesOf(sampleId: string): DishCandidate[] {
  return DATA.samples.find((sample) => sample.id === sampleId)?.candidates ?? []
}

/** 生成识别响应；演示模式不跑推理，耗时字段没有意义，固定写 0 */
export function demoRecognizeResponse(sampleId: string): RecognizeResponse {
  return {
    recognition_id: `demo-${sampleId}`,
    model_version: DEMO_MODEL_VERSION,
    class_count: DEMO_CLASS_COUNT,
    labels_available: true,
    degraded: false,
    elapsed_ms: 0,
    candidates: candidatesOf(sampleId),
  }
}

/** 取菜谱：命中预置快照就返回那一份，否则明确说清是静态演示没有联网 */
export function demoResolve(dish: string): ResolveResponse {
  const hit = DATA.recipes[dish]
  if (hit) {
    return {
      dish: hit.dish,
      source: hit.source,
      degraded: hit.degraded,
      recipe: hit.recipe,
      reason: null,
    }
  }
  return {
    dish,
    source: 'unavailable',
    degraded: true,
    recipe: null,
    reason: DEMO_MISS_REASON,
  }
}

export const DEMO_RECIPE_COUNT = Object.keys(DATA.recipes).length

export function demoHealth(): HealthResponse {
  return {
    status: 'ok',
    database: 'static',
    recipe_count: DEMO_RECIPE_COUNT,
    model: {
      loaded: true,
      version: DEMO_MODEL_VERSION,
      degraded: false,
      class_count: DEMO_CLASS_COUNT,
      labels_available: true,
    },
    generator: { configured: false, model: '静态演示不联网' },
  }
}

/** 类别清单：把快照里出现过的候选汇总出去，够演示用 */
export function demoDishList(): DishListResponse {
  const items: DishItem[] = []
  const seen = new Set<number>()
  for (const sample of DATA.samples) {
    for (const candidate of sample.candidates) {
      if (seen.has(candidate.index)) {
        continue
      }
      seen.add(candidate.index)
      items.push({
        index: candidate.index,
        name: candidate.name,
        provisional_name: candidate.provisional_name,
        library_recipe_id: DATA.recipes[candidate.name]?.recipe?.id ?? 0,
      })
    }
  }
  items.sort((left, right) => left.index - right.index)
  return {
    class_count: DEMO_CLASS_COUNT,
    labels_available: true,
    library_recipe_count: DEMO_RECIPE_COUNT,
    covered_class_count: items.filter((item) => item.library_recipe_id !== 0).length,
    items,
  }
}

/** 把演示图取成 File，交给页面按「用户选了一张图」正常往下走 */
export async function fetchDemoImageFile(option: DemoSampleOption): Promise<File> {
  const response = await fetch(option.url)
  const blob = await response.blob()
  return new File([blob], option.fileName, { type: blob.type || 'image/jpeg' })
}

/** 选完图给一句说明：点示例图就是那张图的结果，自己传的照片则要讲清不是推理 */
export function demoNotice(file: File): string {
  const { sample, matched } = demoRecognition(file)
  if (matched) {
    return sample.note
  }
  return `静态演示：这张照片没有跑模型，下面这组候选取自预置快照（${sample.caption}），用来看识别到菜谱的完整流程。`
}
