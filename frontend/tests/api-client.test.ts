import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  fetchDishes,
  fetchHealth,
  recognizeImage,
  resolveRecipe,
} from '../src/api/client'

// 只依赖 fetch 响应的 ok / status / json 三个成员，避免绑定运行时的 Response 实现
function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

function textResponse(raw: string, status: number) {
  return {
    ok: false,
    status,
    json: async () => {
      throw new Error('not json')
    },
    text: async () => raw,
  } as unknown as Response
}

function stubFetch(impl: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn(async (url: string, init?: RequestInit) => impl(url, init))
  vi.stubGlobal('fetch', mock)
  return mock
}

const file = () => new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' })

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('识别接口客户端', () => {
  it('以 multipart 形式提交文件', async () => {
    const mock = stubFetch(() => jsonResponse({ recognition_id: 'x', candidates: [] }))
    const picture = file()

    await recognizeImage(picture)

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/recognize')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeInstanceOf(FormData)
    expect((init?.body as FormData).get('file')).toBe(picture)
  })

  it('把错误体转换成带错误码的 ApiError', async () => {
    stubFetch(() =>
      jsonResponse({ error: { code: 41501, message: '不支持的图片格式，请上传 jpg / png / webp' } }, 415),
    )

    await expect(recognizeImage(file())).rejects.toBeInstanceOf(ApiError)
    await expect(recognizeImage(file())).rejects.toMatchObject({
      code: 41501,
      status: 415,
      message: '不支持的图片格式，请上传 jpg / png / webp',
    })
  })
})

describe('菜谱解析接口客户端', () => {
  it('带类别下标时菜名与下标一并提交', async () => {
    const mock = stubFetch(() =>
      jsonResponse({ dish: '红烧肉', source: 'library', degraded: false, recipe: null }),
    )

    await resolveRecipe('红烧肉', 77)

    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/recipes/resolve')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ dish: '红烧肉', index: 77 })
  })

  it('省略类别下标时请求体里只有菜名', async () => {
    const mock = stubFetch(() =>
      jsonResponse({ dish: '麻婆豆腐', source: 'generated', degraded: false, recipe: null }),
    )

    await resolveRecipe('麻婆豆腐')

    expect(JSON.parse(String(mock.mock.calls[0][1]?.body))).toEqual({ dish: '麻婆豆腐' })
  })

  it('没有类别下标时只提交菜名', async () => {
    const mock = stubFetch(() =>
      jsonResponse({ dish: '蛋包饭', source: 'unavailable', degraded: true, recipe: null }),
    )

    await resolveRecipe('蛋包饭', null)

    expect(JSON.parse(String(mock.mock.calls[0][1]?.body))).toEqual({ dish: '蛋包饭' })
  })
})

describe('类别清单与健康检查', () => {
  it('/api/dishes 原样返回类别空间与覆盖情况', async () => {
    const mock = stubFetch(() =>
      jsonResponse({
        class_count: 208,
        labels_available: false,
        library_recipe_count: 49,
        covered_class_count: 1,
        items: [{ index: 77, name: '红烧肉', provisional_name: true, library_recipe_id: 109 }],
      }),
    )

    const body = await fetchDishes()

    expect(mock.mock.calls[0][0]).toBe('/api/dishes')
    expect(body.class_count).toBe(208)
    expect(body.items[0].library_recipe_id).toBe(109)
  })

  it('/api/health 返回嵌套的模型与生成器信息', async () => {
    stubFetch(() =>
      jsonResponse({
        status: 'ok',
        database: 'ok',
        recipe_count: 49,
        model: {
          loaded: true,
          version: 'dish-convnext-base-208-onnx',
          degraded: false,
          class_count: 208,
          labels_available: true,
        },
        generator: { configured: false, model: 'unset' },
      }),
    )

    const body = await fetchHealth()

    expect(body.model.class_count).toBe(208)
    expect(body.generator.configured).toBe(false)
  })
})

describe('异常兜底', () => {
  it('网络不可用时给出可读提示', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(fetchHealth()).rejects.toMatchObject({
      code: 50001,
      message: '无法连接后端服务，请确认服务已启动',
    })
  })

  it('错误体不是 JSON 时回退到 HTTP 状态文案', async () => {
    stubFetch(() => textResponse('<html>500</html>', 500))

    await expect(fetchHealth()).rejects.toMatchObject({
      code: 50001,
      message: '请求失败（HTTP 500）',
    })
  })
})
