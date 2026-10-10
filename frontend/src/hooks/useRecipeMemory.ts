import { useCallback, useEffect, useState } from 'react'

import {
  ANON_MEMORY_SCOPE,
  findRemembered,
  mergeMemories,
  normalizeMemory,
  readMemory,
  rememberEntry,
  removeEntry,
  toggleFavorite,
  writeMemory,
} from '../memory'
import type { MemoryEntry } from '../memory'

export type RecipeMemoryController = {
  entries: MemoryEntry[]
  remember: (entry: MemoryEntry) => void
  /** 记忆命中：识别到做过的菜时直接复用，不再请求后端 */
  find: (dish: string) => MemoryEntry | null
  /** 删掉一条：某道菜记错了或不想再留着时用 */
  remove: (dish: string) => void
  /** 收藏 / 取消收藏：收藏的排在前面，记满时也不被挤掉 */
  toggleFavorite: (dish: string) => void
  /** 整表替换：账号同步合并完回写本机时用 */
  replaceAll: (entries: MemoryEntry[]) => void
  clear: () => void
}

/**
 * 本机菜谱记忆（FR-15）：初值直接来自 localStorage，之后每次改动写回。
 *
 * `scope` 是当前的记忆区块（未登录传匿名区，登录后传账号 id，见 FR-16）。
 * 它一变就整份换掉：登录 / 退出 / 换账号看到的不是同一份记忆。
 *
 * 读写都在 memory.ts 里做了容错，这里不再重复判断——存储不可用时记忆为空，
 * 页面照常工作，只是「下次还能看到」这一条失效。
 */
export function useRecipeMemory(scope: string = ANON_MEMORY_SCOPE): RecipeMemoryController {
  const [activeScope, setActiveScope] = useState(scope)
  const [entries, setEntries] = useState<MemoryEntry[]>(() => readMemory(undefined, scope))

  // 换区块时在渲染期同步重置，而不是放进 effect：同一次提交里后面那些同步逻辑
  // （useMemorySync）读到的就必须已经是新区块的数据，晚一帧会把上一个账号的记忆推上去。
  // 未登录时记下的那几条跟着进账号：换设备登录前先在手机网页上试过一回，
  // 登录后不该像没做过一样。反向（账号 → 匿名区）不带，退出后看到的就是这台设备自己的记录。
  if (activeScope !== scope) {
    const carried =
      activeScope === ANON_MEMORY_SCOPE && scope !== ANON_MEMORY_SCOPE
        ? mergeMemories(readMemory(undefined, ANON_MEMORY_SCOPE), readMemory(undefined, scope))
        : readMemory(undefined, scope)
    setActiveScope(scope)
    setEntries(carried)
  }

  useEffect(() => {
    writeMemory(entries, undefined, activeScope)
  }, [entries, activeScope])

  const remember = useCallback((entry: MemoryEntry) => {
    setEntries((previous) => rememberEntry(previous, entry))
  }, [])

  const clear = useCallback(() => setEntries([]), [])

  const remove = useCallback((dish: string) => {
    setEntries((previous) => removeEntry(previous, dish))
  }, [])

  const toggle = useCallback((dish: string) => {
    setEntries((previous) => toggleFavorite(previous, dish))
  }, [])

  const replaceAll = useCallback((next: MemoryEntry[]) => {
    setEntries(normalizeMemory(next))
  }, [])

  const find = useCallback((dish: string) => findRemembered(entries, dish), [entries])

  return { entries, remember, find, remove, toggleFavorite: toggle, replaceAll, clear }
}
