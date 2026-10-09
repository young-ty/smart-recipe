import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

type Props = {
  targetRef: RefObject<HTMLDivElement | null>
}

/** 列表项超出一屏时出现的「向下浏览」悬浮按钮（设计方案 4.6）。 */
export function ScrollToBottomButton({ targetRef }: Props) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const element = targetRef.current
    if (!element) {
      return
    }
    const update = () => {
      setVisible(element.scrollHeight - element.scrollTop - element.clientHeight > 24)
    }
    update()
    element.addEventListener('scroll', update)
    const observer =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(element)
    return () => {
      element.removeEventListener('scroll', update)
      observer?.disconnect()
    }
  }, [targetRef])

  if (!visible) {
    return null
  }

  return (
    <button
      type="button"
      className="fab"
      aria-label="向下浏览"
      onClick={() => {
        const element = targetRef.current
        element?.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
      }}
    >
      ↓
    </button>
  )
}