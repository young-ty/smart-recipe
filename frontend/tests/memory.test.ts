import { describe, expect, it } from 'vitest'

import {
  MEMORY_LIMIT,
  MEMORY_STORAGE_KEY,
  findRemembered,
  formatRelativeTime,
  readMemory,
  rememberEntry,
  writeMemory,
} from '../src/memory'
import type { MemoryEntry } from '../src/memory'
import { memoryEntry } from './fixtures'

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

/** 读写全抛异常的存储：无痕模式/被禁用时的替代环境 */
function brokenStorage(): Storage {
  return {
    get length() {
      return 0
    },
    clear: () => {},
    getItem: () => {
      throw new Error('denied')
    },
    key: () => null,
    removeItem: () => {},
    setItem: () => {
      throw new Error('denied')
    },
  }
}

describe('记忆读写', () => {
  it('没有存过记忆时返回空列表', () => {
    expect(readMemory(fakeStorage())).toEqual([])
  })

  it('写入后再读出来是同一份', () => {
    const storage = fakeStorage()
    const entry = memoryEntry()

    writeMemory([entry], storage)

    expect(readMemory(storage)).toEqual([entry])
  })

  it('JSON 坏掉或结构不对时只丢掉坏数据，不抛异常', () => {
    expect(readMemory(fakeStorage({ [MEMORY_STORAGE_KEY]: '{不是 json' }))).toEqual([])
    expect(readMemory(fakeStorage({ [MEMORY_STORAGE_KEY]: '"字符串"' }))).toEqual([])
    expect(
      readMemory(
        fakeStorage({
          [MEMORY_STORAGE_KEY]: JSON.stringify([
            { dish: '', name: '缺菜名' },
            { dish: '只有菜名' },
            null,
            memoryEntry({ dish: '红烧肉' }),
          ]),
        }),
      ),
    ).toHaveLength(1)
  })

  it('旧版本多余的键在读出时被丢掉，不会写回存储', () => {
    const stale = { ...memoryEntry(), preferences: ['no-spicy'], 别的字段: 1 }
    const storage = fakeStorage({ [MEMORY_STORAGE_KEY]: JSON.stringify([stale]) })

    expect(readMemory(storage)[0]).not.toHaveProperty('preferences')
    expect(readMemory(storage)[0]).not.toHaveProperty('别的字段')
  })

  it('读出时按上限截断', () => {
    const many = Array.from({ length: MEMORY_LIMIT + 5 }, (_, i) =>
      memoryEntry({ dish: `菜${i}`, name: `菜${i}` }),
    )
    const storage = fakeStorage({ [MEMORY_STORAGE_KEY]: JSON.stringify(many) })

    expect(readMemory(storage)).toHaveLength(MEMORY_LIMIT)
  })

  it('存储不可用时读为空、写入不抛异常', () => {
    expect(readMemory(brokenStorage())).toEqual([])
    expect(() => writeMemory([memoryEntry()], brokenStorage())).not.toThrow()
  })
})

describe('rememberEntry', () => {
  it('新记录排最前，同名的菜只留最近一次', () => {
    const first = memoryEntry({ dish: '红烧肉', at: 1 })
    const other = memoryEntry({ dish: '番茄蛋花汤', at: 2 })
    const again = memoryEntry({ dish: '红烧肉', at: 3, source: 'generated' })

    const list = rememberEntry(rememberEntry([first], other), again)

    expect(list.map((entry) => entry.dish)).toEqual(['红烧肉', '番茄蛋花汤'])
    expect(list[0].at).toBe(3)
  })

  it('超出上限时丢掉最旧的', () => {
    let list: MemoryEntry[] = []
    for (let i = 0; i < MEMORY_LIMIT + 3; i += 1) {
      list = rememberEntry(list, memoryEntry({ dish: `别菜${i}`, at: i }))
    }

    expect(list).toHaveLength(MEMORY_LIMIT)
    expect(list[0].dish).toBe(`别菜${MEMORY_LIMIT + 2}`)
    expect(list.some((entry) => entry.dish === '别菜0')).toBe(false)
  })
})

describe('记忆命中 findRemembered（省掉一次生成）', () => {
  const hit = memoryEntry({ dish: '土豆肉末拌面', source: 'generated' })

  it('做过这道菜就命中，返回最近的一条', () => {
    expect(findRemembered([hit], '土豆肉末拌面')).toBe(hit)
  })

  it('同名的菜取最近一条（列表本身按最近在前）', () => {
    const older = memoryEntry({ dish: '红烧肉', at: 1 })
    const newer = memoryEntry({ dish: '红烧肉', at: 2 })
    expect(findRemembered([newer, older], '红烧肉')).toBe(newer)
  })

  it('没做过这道菜时返回 null', () => {
    expect(findRemembered([hit], '红烧肉')).toBeNull()
    expect(findRemembered([], '土豆肉末拌面')).toBeNull()
  })
})

describe('formatRelativeTime', () => {
  const now = new Date(2026, 8, 22, 12, 0, 0).getTime()

  it('按分钟/小时/天给出相对时间', () => {
    expect(formatRelativeTime(now - 20_000, now)).toBe('刚刚')
    expect(formatRelativeTime(now - 5 * 60_000, now)).toBe('5 分钟前')
    expect(formatRelativeTime(now - 3 * 3600_000, now)).toBe('3 小时前')
    expect(formatRelativeTime(now - 2 * 86400_000, now)).toBe('2 天前')
  })

  it('超过一周给出具体日期，时间戳在未来时按刚刚处理', () => {
    expect(formatRelativeTime(new Date(2026, 8, 1, 8, 0, 0).getTime(), now)).toBe('9 月 1 日')
    expect(formatRelativeTime(now + 60_000, now)).toBe('刚刚')
  })
})
