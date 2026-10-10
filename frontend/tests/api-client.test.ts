import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  fetchDishes,
  fetchHealth,
  fetchMemories,
  fetchMe,
  loginAccount,
  logoutAccount,
  probeLlmCredentials,
  putMemories,
  recognizeImage,
  resolveRecipe,
} from '../src/api/client'
import { LLM_API_KEY_HEADER, LLM_BASE_URL_HEADER, LLM_KEY_STORAGE_KEY, LLM_MODEL_HEADER } from '../src/llm-key'
import { memoryEntry } from './fixtures'

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

beforeEach(() => {
  localStorage.clear()
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

describe('用户自带的模型 Key', () => {
  function storeKey(settings: { apiKey: string; baseUrl: string; model: string }) {
    localStorage.setItem(LLM_KEY_STORAGE_KEY, JSON.stringify(settings))
  }

  it('本机填了 Key 就随解析请求带上，地址与模型名一并带', async () => {
    storeKey({
      apiKey: 'sk-user',
      baseUrl: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
    })
    const mock = stubFetch(() =>
      jsonResponse({ dish: '土豆肉末拌面', source: 'generated', degraded: false, recipe: null }),
    )

    await resolveRecipe('土豆肉末拌面')

    const headers = mock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers[LLM_API_KEY_HEADER]).toBe('sk-user')
    expect(headers[LLM_BASE_URL_HEADER]).toBe('https://api.deepseek.com/v1')
    expect(headers[LLM_MODEL_HEADER]).toBe('deepseek-chat')
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('没填 Key 时一个凭据头都不带，让服务端用自己配的那份', async () => {
    const mock = stubFetch(() =>
      jsonResponse({ dish: '红烧肉', source: 'library', degraded: false, recipe: null }),
    )

    await resolveRecipe('红烧肉')

    const headers = mock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers[LLM_API_KEY_HEADER]).toBeUndefined()
    expect(headers[LLM_BASE_URL_HEADER]).toBeUndefined()
  })

  it('识别请求不带凭据头：Key 只用在生成分支上', async () => {
    storeKey({ apiKey: 'sk-user', baseUrl: '', model: '' })
    const mock = stubFetch(() => jsonResponse({ recognition_id: 'x', candidates: [] }))

    await recognizeImage(file())

    expect(mock.mock.calls[0][1]?.headers).toBeUndefined()
  })

  it('探活把凭据头一起带上，返回体的说法原样给出', async () => {
    storeKey({ apiKey: 'sk-user', baseUrl: '', model: '' })
    const mock = stubFetch(() =>
      jsonResponse({ ok: true, configured: true, model: 'deepseek-chat', detail: '连接正常' }),
    )

    const body = await probeLlmCredentials()

    expect(mock.mock.calls[0][0]).toBe('/api/llm/probe')
    expect(mock.mock.calls[0][1]?.method).toBe('POST')
    const headers = mock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers[LLM_API_KEY_HEADER]).toBe('sk-user')
    expect(body.ok).toBe(true)
    expect(body.detail).toBe('连接正常')
  })
})

describe('账号接口客户端', () => {
  const ACCOUNT = {
    id: 1,
    email: 'hakura@example.com',
    display_name: 'hakura',
    created_at: '2026-10-10T00:00:00',
  }

  it('登录把邮箱与密码发给 /api/auth/login，回账号信息', async () => {
    const mock = stubFetch(() => jsonResponse({ user: ACCOUNT }))

    const body = await loginAccount('hakura@example.com', 'password123')

    expect(mock.mock.calls[0][0]).toBe('/api/auth/login')
    expect(mock.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(mock.mock.calls[0][1]?.body))).toEqual({
      email: 'hakura@example.com',
      password: 'password123',
    })
    expect(body.user.id).toBe(1)
  })

  it('会话靠 httpOnly Cookie：请求一律带 credentials: include', async () => {
    const mock = stubFetch(() => jsonResponse({ user: null }))

    await fetchMe()

    expect(mock.mock.calls[0][1]?.credentials).toBe('include')
  })

  it('未登录时 /api/auth/me 回 user 为 null，不当作失败', async () => {
    stubFetch(() => jsonResponse({ user: null }))

    expect((await fetchMe()).user).toBeNull()
  })

  it('退出登录是 204 无响应体，照样算成功', async () => {
    stubFetch(
      () =>
        ({
          ok: true,
          status: 204,
          json: async () => {
            throw new Error('没有响应体')
          },
        }) as unknown as Response,
    )

    await expect(logoutAccount()).resolves.toBeUndefined()
  })

  it('登录失败时把错误码带出来，上层才给得出对应文案', async () => {
    stubFetch(() => jsonResponse({ error: { code: 40101, message: '请先登录' } }, 401))

    await expect(loginAccount('hakura@example.com', 'wrong')).rejects.toMatchObject({
      code: 40101,
      status: 401,
    })
  })
})

describe('记忆同步接口客户端', () => {
  it('上传时只带同步需要的字段，来源收窄成接口认识的两种', async () => {
    const mock = stubFetch(() => jsonResponse({ entries: [] }))
    const entry = memoryEntry({ dish: '红烧肉', source: 'generated', favorite: true })

    await putMemories([entry])

    expect(mock.mock.calls[0][0]).toBe('/api/memories')
    expect(mock.mock.calls[0][1]?.method).toBe('PUT')
    expect(JSON.parse(String(mock.mock.calls[0][1]?.body))).toEqual({
      entries: [
        {
          dish: '红烧肉',
          name: '红烧肉',
          source: 'generated',
          recipe: entry.recipe,
          at: entry.at,
          favorite: true,
        },
      ],
    })
  })

  it('来源只在库内与生成之间取值，不会把 unavailable 发上去', async () => {
    const mock = stubFetch(() => jsonResponse({ entries: [] }))

    await putMemories([memoryEntry({ dish: '红烧肉', source: 'library' })])

    expect(JSON.parse(String(mock.mock.calls[0][1]?.body)).entries[0].source).toBe('library')
  })

  it('拉取账号里的记忆按原样返回', async () => {
    stubFetch(() => jsonResponse({ entries: [memoryEntry({ dish: '红烧肉' })] }))

    const body = await fetchMemories()

    expect(body.entries).toHaveLength(1)
    expect(body.entries[0].dish).toBe('红烧肉')
  })
})
