import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  DEMO_CLASS_COUNT,
  DEMO_MISS_REASON,
  DEMO_RECIPE_COUNT,
  DEMO_SAMPLES,
  demoDishList,
  demoHealth,
  demoNotice,
  demoRecognition,
  demoRecognizeResponse,
  demoResolve,
  demoSampleById,
  fetchDemoImageFile,
} from '../src/demo'
import fixtures from '../src/demo/fixtures.json'

function imageFile(name: string, size = 1024): File {
  return new File([new Uint8Array(size)], name, { type: 'image/jpeg' })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('静态演示站的预置数据', () => {
  it('每张示例图都有一份候选，置信度降序且来自真实模型', () => {
    expect(DEMO_SAMPLES).toHaveLength(fixtures.samples.length)
    for (const sample of DEMO_SAMPLES) {
      const response = demoRecognizeResponse(sample.id)
      expect(response.candidates.length).toBeGreaterThan(0)
      expect(response.model_version).toBe(fixtures.model.version)
      expect(response.class_count).toBe(DEMO_CLASS_COUNT)
      expect(response.labels_available).toBe(true)
      const confidences = response.candidates.map((item) => item.confidence)
      expect([...confidences].sort((left, right) => right - left)).toEqual(confidences)
    }
  })

  it('示例图地址走 BASE_URL，放在 Pages 子路径下也显示得出来', () => {
    for (const sample of DEMO_SAMPLES) {
      expect(sample.url.startsWith('/')).toBe(true)
      expect(sample.url.endsWith(sample.fileName)).toBe(true)
    }
  })

  it('每张示例图的 top-1 都能直接拿出菜谱，点进去不会是空页', () => {
    for (const sample of DEMO_SAMPLES) {
      const top1 = demoRecognizeResponse(sample.id).candidates[0]
      const resolved = demoResolve(top1.name)
      expect(resolved.source).toBe('library')
      expect(resolved.recipe).not.toBeNull()
      expect(['seed', 'generated']).toContain(String(resolved.recipe?.origin))
    }
  })

  it('预置菜谱里种子菜谱与「大模型生成后落库」都在，页面标签才有东西可标', () => {
    const origins = Object.values(fixtures.recipes).map((entry) => entry.recipe?.origin)
    expect(origins).toContain('seed')
    expect(origins).toContain('generated')
  })

  it('库里没有的菜明确降级，理由讲清是静态演示', () => {
    expect(demoResolve('佛跳墙')).toMatchObject({
      source: 'unavailable',
      degraded: true,
      recipe: null,
      reason: DEMO_MISS_REASON,
    })
  })

  it('健康检查报的是那份真实模型标识，并说明不联网', () => {
    const health = demoHealth()
    expect(health.model.version).toBe(fixtures.model.version)
    expect(health.recipe_count).toBe(DEMO_RECIPE_COUNT)
    expect(health.generator.configured).toBe(false)
  })

  it('类别清单里可承接的类别数与实际能给出菜谱的条数一致', () => {
    const list = demoDishList()
    expect(list.items.length).toBeGreaterThan(0)
    expect(list.covered_class_count).toBe(
      list.items.filter((item) => item.library_recipe_id !== 0).length,
    )
  })
})

describe('演示模式的识别取数', () => {
  it('示例图的文件名对得上快照，说明文字就是这张图的', () => {
    const sample = DEMO_SAMPLES[0]
    const hit = demoRecognition(imageFile(sample.fileName))
    expect(hit.matched).toBe(true)
    expect(hit.sample.id).toBe(sample.id)
    expect(demoNotice(imageFile(sample.fileName))).toBe(sample.note)
  })

  it('自己传的照片取固定的那一份，并说清不是对它的推理', () => {
    const first = demoRecognition(imageFile('IMG_0001.jpg', 2048))
    const second = demoRecognition(imageFile('IMG_0001.jpg', 2048))
    expect(first.matched).toBe(false)
    expect(first.sample.id).toBe(second.sample.id)
    expect(demoNotice(imageFile('IMG_0001.jpg', 2048))).toContain('没有跑模型')
  })

  it('按 id 取示例图，查不到给 null', () => {
    expect(demoSampleById(DEMO_SAMPLES[0].id)?.caption).toBe(DEMO_SAMPLES[0].caption)
    expect(demoSampleById('not-a-sample')).toBeNull()
  })

  it('把示例图取成同名 File，页面按「用户选了一张图」正常往下走', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ blob: async () => blob }) as unknown as Response),
    )

    const file = await fetchDemoImageFile(DEMO_SAMPLES[0])
    expect(file.name).toBe(DEMO_SAMPLES[0].fileName)
    expect(file.type).toBe('image/jpeg')
  })
})
