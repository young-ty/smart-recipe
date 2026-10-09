import { useCallback, useEffect, useState } from 'react'

import { findRemembered, readMemory, rememberEntry, writeMemory } from '../memory'
import type { MemoryEntry } from '../memory'

export type RecipeMemoryController = {
  entries: MemoryEntry[]
  remember: (entry: MemoryEntry) => void
  /** 记忆命中：识别到做过的菜时直接复用，不再请求后端 */
  find: (dish: string) => MemoryEntry | null
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

  const find = useCallback((dish: string) => findRemembered(entries, dish), [entries])

  return { entries, remember, find, clear }
}
