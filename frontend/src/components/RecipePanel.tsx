import { useRef } from 'react'

import { EMPTY_HINT_IDLE, RESOLVING_HINT, SOURCE_LABEL } from '../constants'
import { formatRelativeTime } from '../memory'
import type { ResolveState } from '../hooks/useRecipePage'
import { PanelHeader } from './PanelHeader'
import { RecipeView } from './RecipeView'
import { ScrollToBottomButton } from './ScrollToBottomButton'

type Props = {
  resolve: ResolveState
  provisionalName: boolean
}

/** 区域 C：菜谱与做法。四种状态各自有明确文案，不把「查不到」伪装成空白。 */
export function RecipePanel({ resolve, provisionalName }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const badge = resolve.source ? SOURCE_LABEL[resolve.source] : ''

  let body
  if (resolve.error) {
    body = <p className="empty">{resolve.error}</p>
  } else if (resolve.recipe) {
    body = (
      <RecipeView recipe={resolve.recipe} provisionalName={provisionalName} source={resolve.source} />
    )
  } else if (resolve.loading) {
    body = <p className="empty">{RESOLVING_HINT}</p>
  } else if (resolve.reason) {
    body = <p className="empty">{resolve.reason}</p>
  } else {
    body = <p className="empty">{EMPTY_HINT_IDLE}</p>
  }

  return (
    <section className="card card--grow">
      <PanelHeader title="菜谱与做法" trailing={<span className="badge">{badge}</span>} />
      <div className="recipe-scroll" ref={scrollRef} data-testid="recipe-scroll">
        {resolve.rememberedAt !== null && (
          <p className="memory-note">
            来自本机记忆 · {formatRelativeTime(resolve.rememberedAt)}（未重新请求菜谱）
          </p>
        )}
        {body}
      </div>
      <ScrollToBottomButton targetRef={scrollRef} />
    </section>
  )
}
