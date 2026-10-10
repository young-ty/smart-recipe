import { useEffect, useRef, useState } from 'react'

import { fetchMemories, putMemories } from '../api/client'
import { mergeMemories, normalizeMemory } from '../memory'
import type { MemoryEntry } from '../memory'
import type { AccountUser } from '../types'

export type MemorySyncState = 'idle' | 'syncing' | 'synced' | 'failed'

export type MemoryStore = {
  entries: MemoryEntry[]
  replaceAll: (entries: MemoryEntry[]) => void
}

/**
 * 账号级记忆同步（FR-16）。
 *
 * 登录后先合再写：本机与服务端各有一份，只有前端同时握着两边，所以合并放在这里，
 * 服务端只做去重、排序与截断。合并结果一次推送、两边落定，之后本机每有变动再推一次。
 * 退出登录不清本机记忆：那是这台设备用过什么的记录，与账号无关。
 */
export function useMemorySync(user: AccountUser | null, store: MemoryStore): MemorySyncState {
  const [state, setState] = useState<MemorySyncState>('idle')
  const entriesRef = useRef(store.entries)
  entriesRef.current = store.entries
  const replaceAllRef = useRef(store.replaceAll)
  replaceAllRef.current = store.replaceAll
  // 最近一次推上去的载荷。合并会把结果回写本机，那次改动不该再触发一遍同样的推送，
  // 用内容比对挡掉，比「靠时序判断」可靠：不依赖 React 什么时候重渲染。
  const pushedRef = useRef('')
  const userId = user?.id ?? null

  useEffect(() => {
    if (userId === null) {
      pushedRef.current = ''
      setState('idle')
      return
    }
    // 水合还没回来时，下面的推送 effect 会先跑一次。先把「本机现有的」记为已推送，
    // 否则会把还没跟服务端合并过的记忆先推上去一遍（服务端那份就被这次覆盖了）。
    pushedRef.current = JSON.stringify(normalizeMemory(entriesRef.current))
    let cancelled = false
    setState('syncing')
    fetchMemories()
      .then((remote) => {
        if (cancelled) {
          return null
        }
        const merged = mergeMemories(entriesRef.current, remote.entries)
        pushedRef.current = JSON.stringify(merged)
        replaceAllRef.current(merged)
        return putMemories(merged)
      })
      .then((result) => {
        if (!cancelled && result !== null) {
          setState('synced')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState('failed')
        }
      })
    return () => {
      cancelled = true
    }
  }, [userId])

  useEffect(() => {
    if (userId === null) {
      return
    }
    const payload = JSON.stringify(normalizeMemory(store.entries))
    if (payload === pushedRef.current) {
      return
    }
    pushedRef.current = payload
    let cancelled = false
    setState('syncing')
    putMemories(normalizeMemory(store.entries))
      .then(() => {
        if (!cancelled) {
          setState('synced')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState('failed')
        }
      })
    return () => {
      cancelled = true
    }
  }, [userId, store.entries])

  return state
}
