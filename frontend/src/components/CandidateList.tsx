import { EMPTY_HINT_NO_CANDIDATE } from '../constants'
import type { DishCandidate } from '../types'
import { CandidateButton } from './CandidateButton'
import { ManualDishForm } from './ManualDishForm'
import { PanelHeader } from './PanelHeader'

type Props = {
  candidates: DishCandidate[]
  selectedIndex: number | null
  manualDish: string | null
  /** 选过图片后才给手输入口，避免首次打开就出现与识别无关的输入框 */
  showManual: boolean
  onSelect: (index: number) => void
  onSubmitManual: (dish: string) => void
}

/** 区域 B：模型的 top-k 候选；都不对时用手输菜名兜底（FR-10）。 */
export function CandidateList({
  candidates,
  selectedIndex,
  manualDish,
  showManual,
  onSelect,
  onSubmitManual,
}: Props) {
  const hasCandidates = candidates.length > 0
  return (
    <section className="card card--grow">
      <PanelHeader title="识别结果" trailing={<span className="hint">点击切换候选</span>} />
      <div
        className="candidates"
        role={hasCandidates ? 'radiogroup' : undefined}
        aria-label={hasCandidates ? '候选菜品' : undefined}
      >
        {hasCandidates ? (
          candidates.map((candidate) => (
            <CandidateButton
              key={candidate.index}
              candidate={candidate}
              selected={candidate.index === selectedIndex}
              onSelect={onSelect}
            />
          ))
        ) : (
          <p className="empty">{EMPTY_HINT_NO_CANDIDATE}</p>
        )}
      </div>
      {showManual && <ManualDishForm activeDish={manualDish} onSubmit={onSubmitManual} />}
    </section>
  )
}
