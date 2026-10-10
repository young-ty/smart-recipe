import { useCallback, useEffect, useState } from 'react'

import {
  findRemembered,
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
  clear: () => void
}

/**
 * 本机菜谱记忆（FR-15）：初值直接来自 localStorage，之后每次改动写回。
 *
 * 读写都在 memory.ts 里做了容错，这里不再重复判断——存储不可用时记忆为空，
 * 页面照常工作，只是「下次还能看到」这一条失效。
 */
export function useRecipeMemory(): RecipeMemoryController {
  const [entries, setEntries] = useState<MemoryEntry[]>(() => readMemory())

  useEffect(() => {
    writeMemory(entries)
  }, [entries])

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

  const find = useCallback((dish: string) => findRemembered(entries, dish), [entries])

  return { entries, remember, find, remove, toggleFavorite: toggle, clear }
}
