import { useEffect, useState } from 'react'

import { fetchDishes } from '../api/client'
import { coveredIndexSet } from '../pending-hint'

/**
 * 库内覆盖清单：只为等待文案服务，取不到就返回 null，页面退回「正在查询菜谱…」。
 *
 * 清单是静态的（补库频率很低），挂载时取一次即可，不参与解析链路。
 */
export function useDishCoverage(): Set<number> | null {
  const [covered, setCovered] = useState<Set<number> | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchDishes()
      .then((body) => {
        if (!cancelled) {
          setCovered(coveredIndexSet(body.items))
        }
      })
      .catch(() => {
        // 拿不到清单不影响识别与取菜谱，只是等待文案少一层信息
      })
    return () => {
      cancelled = true
    }
  }, [])

  return covered
}
