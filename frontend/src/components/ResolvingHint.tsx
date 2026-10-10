import { useEffect, useState } from 'react'

import { pendingHint } from '../pending-hint'
import type { PendingKind } from '../pending-hint'

/**
 * 等待提示自己走秒：只有这一行随秒数重渲染，不带着整页一起跳。
 * 文案分三条支路（库内 / 库外 / 不确定），等久了还要说清为什么慢。
 */
export function ResolvingHint({ kind }: { kind: PendingKind }) {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    setSeconds(0)
    const timer = window.setInterval(() => {
      setSeconds((value) => value + 1)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [kind])

  return (
    <p className="empty" role="status">
      {pendingHint(kind, seconds)}
    </p>
  )
}
