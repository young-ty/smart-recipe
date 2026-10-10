// 菜谱记忆（FR-15）：做过的菜留在本机，下次识别到同一道菜就直接复用、不再请求后端。
//
// 只落 localStorage，不入库、不上传照片：记忆是「这台设备用过什么」的记录，
// 不需要账号，也就不涉及用户数据外发。任何读写失败（无痕模式、配额满）都降级为空，
// 记忆功能坏掉不能影响识别与取菜谱的主链路。

import type { RecipeOut, RecipeSource } from './types'

export type MemoryEntry = {
  /** 记忆键：同名的菜只留最近一次 */
  dish: string
  /** 菜谱标题；生成结果的名字可能与输入菜名不完全一致 */
  name: string
  source: RecipeSource
  recipe: RecipeOut
  /** 记录时间（epoch ms） */
  at: number
  /** 收藏：条数记满时收藏的不会被挤掉，列表里也排在前面 */
  favorite: boolean
}

export const MEMORY_LIMIT = 12
export const MEMORY_STORAGE_KEY = 'recipe-memory.v1'

/** 取 localStorage；隐私模式下访问本身就可能抛异常，一律当作没有存储 */
function defaultStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

function readJson(key: string, target: Storage | null): unknown {
  if (target === null) {
    return null
  }
  try {
    const raw = target.getItem(key)
    return raw === null ? null : JSON.parse(raw)
  } catch {
    return null
  }
}

function writeJson(key: string, value: unknown, target: Storage | null): void {
  if (target === null) {
    return
  }
  try {
    target.setItem(key, JSON.stringify(value))
  } catch {
    // 配额满或被禁用：记忆退化成「只对本次打开有效」，不影响主链路
  }
}

function isMemoryEntry(value: unknown): value is MemoryEntry {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const entry = value as Partial<MemoryEntry>
  return (
    typeof entry.dish === 'string' &&
    entry.dish.length > 0 &&
    typeof entry.name === 'string' &&
    typeof entry.at === 'number' &&
    typeof entry.recipe === 'object' &&
    entry.recipe !== null &&
    (entry.source === 'library' || entry.source === 'generated') &&
    (entry.favorite === undefined || typeof entry.favorite === 'boolean')
  )
}

/** 读记忆：解析失败、结构不对、被改坏的条目一律丢掉，只保留能用的 */
export function readMemory(target: Storage | null = defaultStorage()): MemoryEntry[] {
  const raw = readJson(MEMORY_STORAGE_KEY, target)
  if (!Array.isArray(raw)) {
    return []
  }
  // 显式挑字段：旧版本存过的 preferences 之类的多余键在读出时就被丢掉
  return raw.filter(isMemoryEntry).slice(0, MEMORY_LIMIT).map((entry) => ({
    dish: entry.dish,
    name: entry.name,
    source: entry.source,
    recipe: entry.recipe,
    at: entry.at,
    favorite: entry.favorite === true,
  }))
}

export function writeMemory(
  entries: MemoryEntry[],
  target: Storage | null = defaultStorage(),
): void {
  writeJson(MEMORY_STORAGE_KEY, entries, target)
}

/**
 * 记忆命中（FR-15）：做过这道菜就返回最近的一条。
 *
 * 识别到做过的菜时直接拿它复用，省掉一次检索/生成——库外菜不再重复消耗大模型额度。
 */
export function findRemembered(entries: MemoryEntry[], dish: string): MemoryEntry | null {
  return entries.find((entry) => entry.dish === dish) ?? null
}

/**
 * 记一笔：同名的菜去重后放到最前，超出上限的旧记录丢掉。
 *
 * 已经收藏的菜再被取一次时保留收藏标记：重新识别不该把收藏降级成普通条目。
 */
export function rememberEntry(entries: MemoryEntry[], entry: MemoryEntry): MemoryEntry[] {
  const existing = entries.find((item) => item.dish === entry.dish)
  const merged: MemoryEntry = {
    ...entry,
    favorite: entry.favorite || existing?.favorite === true,
  }
  const rest = entries.filter((item) => item.dish !== entry.dish)
  return orderMemory([merged, ...rest]).slice(0, MEMORY_LIMIT)
}

/**
 * 收藏的排前面，两段内部各自按最近在前。
 *
 * 按 `at` 重排而不是沿用数组顺序：取消收藏时那一条要回到它按时间本该在的位置，
 * 时间戳相同的条目保持原有先后（sort 在 ES2019 起是稳定排序）。截断时先丢没收藏的旧条目。
 */
export function orderMemory(entries: MemoryEntry[]): MemoryEntry[] {
  return [...entries].sort((left, right) => {
    if (left.favorite !== right.favorite) {
      return left.favorite ? -1 : 1
    }
    return right.at - left.at
  })
}

/** 删掉一条：清空是整表操作，只想去掉某道做错的菜时用它 */
export function removeEntry(entries: MemoryEntry[], dish: string): MemoryEntry[] {
  return entries.filter((entry) => entry.dish !== dish)
}

/** 收藏 / 取消收藏：改完立即重排，收藏的条目当次就跳到最前 */
export function toggleFavorite(entries: MemoryEntry[], dish: string): MemoryEntry[] {
  const next = entries.map((entry) =>
    entry.dish === dish ? { ...entry, favorite: !entry.favorite } : entry,
  )
  return orderMemory(next)
}

/**
 * 记满时的提示：不提示的话，用户只看到「刚取过的菜不见了」。
 * 收藏的菜不参与淘汰，所以全被收藏满了要先取消一个才腾得出位置。
 */
export function memoryLimitNotice(entries: MemoryEntry[]): string | null {
  if (entries.length < MEMORY_LIMIT) {
    return null
  }
  if (entries.every((entry) => entry.favorite)) {
    return (
      '已记满 ' + MEMORY_LIMIT + ' 道，而且都收藏了：新识别的菜记不进来，取消一个收藏就能腾出位置'
    )
  }
  return '已记满 ' + MEMORY_LIMIT + ' 道：新识别的菜会挤掉最旧的一条，收藏的不会被挤掉'
}

/** 相对时间：刚取过的菜一眼能看出是「刚刚」还是「前天」 */
export function formatRelativeTime(at: number, now: number = Date.now()): string {
  const minute = 60_000
  const diff = now - at
  if (diff < minute) {
    return '刚刚'
  }
  if (diff < 60 * minute) {
    return `${Math.floor(diff / minute)} 分钟前`
  }
  if (diff < 24 * 60 * minute) {
    return `${Math.floor(diff / (60 * minute))} 小时前`
  }
  if (diff < 7 * 24 * 60 * minute) {
    return `${Math.floor(diff / (24 * 60 * minute))} 天前`
  }
  const date = new Date(at)
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
}
