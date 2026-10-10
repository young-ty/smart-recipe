import { describe, expect, it } from 'vitest'

import {
  MEMORY_LIMIT,
  MEMORY_STORAGE_KEY,
  findRemembered,
  formatRelativeTime,
  mergeMemories,
  memoryLimitNotice,
  normalizeMemory,
  orderMemory,
  readMemory,
  rememberEntry,
  removeEntry,
  toggleFavorite,
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

describe('收藏与条目管理', () => {
  it('收藏标记能写进存储再读出来，旧数据没有这个字段时按未收藏算', () => {
    const storage = fakeStorage()
    writeMemory([{ ...memoryEntry(), favorite: true }], storage)
    expect(readMemory(storage)[0].favorite).toBe(true)

    const legacy = memoryEntry()
    const withoutFavorite = { ...legacy, favorite: undefined }
    const read = readMemory(
      fakeStorage({ [MEMORY_STORAGE_KEY]: JSON.stringify([withoutFavorite]) }),
    )
    expect(read[0].favorite).toBe(false)
  })

  it('收藏后移到最前，取消收藏后回到按时间排的位置', () => {
    const list = [
      memoryEntry({ dish: '红烧肉', at: 3 }),
      memoryEntry({ dish: '麻婆豆腐', at: 2 }),
      memoryEntry({ dish: '番茄蛋花汤', at: 1 }),
    ]

    const favorited = toggleFavorite(list, '番茄蛋花汤')
    expect(favorited.map((entry) => entry.dish)).toEqual(['番茄蛋花汤', '红烧肉', '麻婆豆腐'])

    const back = toggleFavorite(favorited, '番茄蛋花汤')
    expect(back.map((entry) => entry.dish)).toEqual(['红烧肉', '麻婆豆腐', '番茄蛋花汤'])
  })

  it('记满时先丢没收藏的旧条目，收藏的留着并排在最前', () => {
    let list: MemoryEntry[] = [memoryEntry({ dish: '老菜', at: 0, favorite: true })]
    for (let i = 0; i <= MEMORY_LIMIT; i += 1) {
      list = rememberEntry(list, memoryEntry({ dish: `新菜${i}`, at: i + 1 }))
    }

    expect(list).toHaveLength(MEMORY_LIMIT)
    expect(list[0].dish).toBe('老菜')
    expect(list.some((entry) => entry.dish === '新菜0')).toBe(false)
  })

  it('重新取到收藏过的菜时，收藏标记不丢', () => {
    const favorited = toggleFavorite([memoryEntry({ dish: '红烧肉', at: 1 })], '红烧肉')
    const again = rememberEntry(
      favorited,
      memoryEntry({ dish: '红烧肉', at: 9, source: 'generated' }),
    )

    expect(again).toHaveLength(1)
    expect(again[0].favorite).toBe(true)
    expect(again[0].at).toBe(9)
  })

  it('按菜名删掉一条，其余不动', () => {
    const list = [memoryEntry({ dish: '红烧肉' }), memoryEntry({ dish: '麻婆豆腐' })]

    expect(removeEntry(list, '红烧肉').map((entry) => entry.dish)).toEqual(['麻婆豆腐'])
    expect(removeEntry(list, '没取过的菜')).toHaveLength(2)
  })

  it('没记满时不提示上限，记满后说清新菜会挤掉谁', () => {
    expect(memoryLimitNotice([memoryEntry({ dish: '红烧肉' })])).toBeNull()

    const full = Array.from({ length: MEMORY_LIMIT }, (_, i) =>
      memoryEntry({ dish: `菜${i}`, at: i }),
    )
    expect(memoryLimitNotice(full)).toContain('会挤掉最旧的一条')

    const allFavorited = full.map((entry) => ({ ...entry, favorite: true }))
    expect(memoryLimitNotice(allFavorited)).toContain('取消一个收藏')
  })

  it('orderMemory 只重排顺序，不改动传进来的列表', () => {
    const list = [memoryEntry({ dish: '红烧肉' }), memoryEntry({ dish: '麻婆豆腐', favorite: true })]

    expect(orderMemory(list).map((entry) => entry.dish)).toEqual(['麻婆豆腐', '红烧肉'])
    expect(list.map((entry) => entry.dish)).toEqual(['红烧肉', '麻婆豆腐'])
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

describe('账号同步的合并 mergeMemories（FR-16）', () => {
  it('两边各有的菜合到一起，同名的只留一条', () => {
    const merged = mergeMemories(
      [memoryEntry({ dish: '红烧肉', at: 3 })],
      [memoryEntry({ dish: '番茄蛋花汤', at: 2 })],
    )

    expect(merged.map((entry) => entry.dish)).toEqual(['红烧肉', '番茄蛋花汤'])
  })

  it('同名的菜取时间较新的那份内容', () => {
    const merged = mergeMemories(
      [memoryEntry({ dish: '红烧肉', at: 1, name: '本机那份' })],
      [memoryEntry({ dish: '红烧肉', at: 9, name: '服务端那份' })],
    )

    expect(merged).toHaveLength(1)
    expect(merged[0].name).toBe('服务端那份')
  })

  it('本机那份较新时用它，服务端不覆盖', () => {
    const merged = mergeMemories(
      [memoryEntry({ dish: '红烧肉', at: 9, name: '本机那份' })],
      [memoryEntry({ dish: '红烧肉', at: 1, name: '服务端那份' })],
    )

    expect(merged[0].name).toBe('本机那份')
  })

  it('收藏标记取并集：在另一台设备上收藏过的菜不会被这次合并抹掉', () => {
    const older = mergeMemories(
      [memoryEntry({ dish: '红烧肉', at: 1, favorite: false })],
      [memoryEntry({ dish: '红烧肉', at: 9, favorite: true })],
    )

    expect(older[0].favorite).toBe(true)
    expect(older[0].at).toBe(9)

    const newer = mergeMemories(
      [memoryEntry({ dish: '红烧肉', at: 9, favorite: true })],
      [memoryEntry({ dish: '红烧肉', at: 1, favorite: false })],
    )
    expect(newer[0].favorite).toBe(true)
  })

  it('合并结果照常按收藏在前、各自最近在前排序，并截到上限', () => {
    const local = Array.from({ length: MEMORY_LIMIT }, (_, i) =>
      memoryEntry({ dish: `本机${i}`, at: i + 1 }),
    )
    const remote = [memoryEntry({ dish: '服务端收藏的', at: 0, favorite: true })]

    const merged = mergeMemories(local, remote)

    expect(merged).toHaveLength(MEMORY_LIMIT)
    expect(merged[0].dish).toBe('服务端收藏的')
    expect(merged.some((entry) => entry.dish === '本机0')).toBe(false)
  })

  it('两边都空时结果是空，不凭空造条目', () => {
    expect(mergeMemories([], [])).toEqual([])
  })
})

describe('normalizeMemory', () => {
  it('重排后收藏的排在最前', () => {
    const entries = [
      memoryEntry({ dish: '最近的', at: 5 }),
      memoryEntry({ dish: '收藏的', at: 1, favorite: true }),
    ]

    expect(normalizeMemory(entries).map((entry) => entry.dish)).toEqual(['收藏的', '最近的'])
  })
})
