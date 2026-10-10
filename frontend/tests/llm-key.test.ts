import { describe, expect, it } from 'vitest'

import {
  EMPTY_LLM_KEY,
  LLM_API_KEY_HEADER,
  LLM_BASE_URL_HEADER,
  LLM_KEY_STORAGE_KEY,
  LLM_MODEL_HEADER,
  clearLlmKey,
  llmRequestHeaders,
  maskApiKey,
  readLlmKey,
  validateLlmKey,
  writeLlmKey,
} from '../src/llm-key'

function fakeStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial))
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => void data.delete(key),
    setItem: (key: string, value: string) => void data.set(key, value),
  }
}

/** 读写时一律抛异常的存储：无痕模式下的真实表现 */
function hostileStorage(): Storage {
  return {
    get length(): number {
      throw new Error('denied')
    },
    clear: () => {
      throw new Error('denied')
    },
    getItem: () => {
      throw new Error('denied')
    },
    key: () => {
      throw new Error('denied')
    },
    removeItem: () => {
      throw new Error('denied')
    },
    setItem: () => {
      throw new Error('denied')
    },
  } as unknown as Storage
}

describe('本机保存的模型 Key', () => {
  it('没填过时读出 null，也不带任何凭据头', () => {
    const storage = fakeStorage()

    expect(readLlmKey(storage)).toBeNull()
    expect(llmRequestHeaders(storage)).toEqual({})
  })

  it('写进去能原样读回来，首尾空白与地址末尾的斜杠都归一化', () => {
    const storage = fakeStorage()

    writeLlmKey(
      { apiKey: '  sk-abc  ', baseUrl: ' https://api.deepseek.com/v1/ ', model: ' deepseek-chat ' },
      storage,
    )

    expect(readLlmKey(storage)).toEqual({
      apiKey: 'sk-abc',
      baseUrl: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
    })
  })

  it('Key 是空串或只有空白时，当作没填过', () => {
    expect(readLlmKey(fakeStorage({ [LLM_KEY_STORAGE_KEY]: JSON.stringify({ apiKey: '' }) }))).toBeNull()
    expect(
      readLlmKey(fakeStorage({ [LLM_KEY_STORAGE_KEY]: JSON.stringify({ apiKey: '   ' }) })),
    ).toBeNull()
  })

  it('存储里是被改坏的内容时当作没填过，不抛异常', () => {
    expect(readLlmKey(fakeStorage({ [LLM_KEY_STORAGE_KEY]: '{不是 JSON' }))).toBeNull()
    expect(readLlmKey(fakeStorage({ [LLM_KEY_STORAGE_KEY]: JSON.stringify(['sk']) }))).toBeNull()
  })

  it('存储不可用时读成没填、写入静默失败，不影响主链路', () => {
    const storage = hostileStorage()

    expect(readLlmKey(storage)).toBeNull()
    expect(() => writeLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk-abc' }, storage)).not.toThrow()
    expect(() => clearLlmKey(storage)).not.toThrow()
    expect(llmRequestHeaders(storage)).toEqual({})
  })

  it('清除之后本机不再有 Key', () => {
    const storage = fakeStorage()
    writeLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk-abc' }, storage)

    clearLlmKey(storage)

    expect(readLlmKey(storage)).toBeNull()
  })
})

describe('随请求带出去的凭据头', () => {
  it('带地址与模型名时三个头都齐', () => {
    const storage = fakeStorage({
      [LLM_KEY_STORAGE_KEY]: JSON.stringify({
        apiKey: 'sk-abc',
        baseUrl: 'https://api.deepseek.com/v1',
        model: 'deepseek-chat',
      }),
    })

    expect(llmRequestHeaders(storage)).toEqual({
      [LLM_API_KEY_HEADER]: 'sk-abc',
      [LLM_BASE_URL_HEADER]: 'https://api.deepseek.com/v1',
      [LLM_MODEL_HEADER]: 'deepseek-chat',
    })
  })

  it('只填了 Key 时只带 Key，空地址不会变成空头', () => {
    const storage = fakeStorage({
      [LLM_KEY_STORAGE_KEY]: JSON.stringify({ apiKey: 'sk-abc', baseUrl: '', model: '' }),
    })

    expect(llmRequestHeaders(storage)).toEqual({ [LLM_API_KEY_HEADER]: 'sk-abc' })
  })
})

describe('保存前的校验', () => {
  it('Key 必填', () => {
    expect(validateLlmKey(EMPTY_LLM_KEY)).toBe('请填模型 Key')
    expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: '   ' })).toBe('请填模型 Key')
  })

  it('Key 与模型名超长时拦下，上限与后端一致', () => {
    expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'k'.repeat(201) })).toContain('Key 太长')
    expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk', model: 'm'.repeat(65) })).toContain(
      '模型名太长',
    )
  })

  it('地址只接受 https 与本机 http，明文发到外网要拦下来', () => {
    expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk', baseUrl: 'http://api.example.com/v1' }))
      .toContain('https://')
    for (const allowed of [
      'https://api.deepseek.com/v1',
      'http://localhost:8000/v1',
      'http://127.0.0.1:11434/v1',
    ]) {
      expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk', baseUrl: allowed })).toBeNull()
    }
  })

  it('地址留空是合法的：表示用服务端配好的那个', () => {
    expect(validateLlmKey({ ...EMPTY_LLM_KEY, apiKey: 'sk' })).toBeNull()
  })
})

describe('maskApiKey', () => {
  it('长 Key 只露头尾，中间遮掉', () => {
    expect(maskApiKey('sk-local-demo-key-abcdef')).toBe('sk-l••••cdef')
  })

  it('短 Key 整段遮掉，不泄漏长度以外的信息', () => {
    expect(maskApiKey('sk-abc')).not.toContain('sk-abc')
    expect(maskApiKey('')).toHaveLength(4)
  })
})
