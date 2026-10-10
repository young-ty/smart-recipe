import { EMPTY_HINT_NO_CANDIDATE } from '../constants'
import { confidenceVerdict } from '../confidence'
import type { PendingKind } from '../pending-hint'
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
  /** 扒谱：点它才去取菜谱，识别与切换候选都不发请求（FR-17） */
  onRequestRecipe: () => void
  canRequestRecipe: boolean
  /** 这次会走哪条分支，用来把「花不花额度」写清楚 */
  pendingKind: PendingKind
  onSubmitManual: (dish: string) => void
}

/**
 * 按钮下面那句说明。库外菜要用用户自己的 Key 现写，这件事必须在点之前就说清楚：
 * 点完才知道要花额度，跟误点烧掉一次没区别。
 */
const REVEAL_HINT: Record<PendingKind, string> = {
  library: '库里已有这道菜，直接读，不消耗模型额度',
  generate: '库里没有这道菜，会用你自己的模型 Key 现写一份',
  unknown: '库里没有的话，会用你自己的模型 Key 现写一份',
}

/** 区域 B：模型的 top-k 候选；都不对时用手输菜名兜底（FR-10）。 */
export function CandidateList({
  candidates,
  selectedIndex,
  manualDish,
  showManual,
  onSelect,
  onRequestRecipe,
  canRequestRecipe,
  pendingKind,
  onSubmitManual,
}: Props) {
  const hasCandidates = candidates.length > 0
  // 把握不大时先说清楚，别让一个 45% 的猜测看起来像结论
  const verdict = confidenceVerdict(candidates)
  return (
    <section className="card card--grow">
      <PanelHeader title="识别结果" trailing={<span className="hint">点击切换候选</span>} />
      {verdict.note ? (
        <p className={`cand-note cand-note--${verdict.level}`} role="status">
          {verdict.note}
        </p>
      ) : null}
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
      {hasCandidates && (
        <div className="reveal">
          <button
            type="button"
            className="btn btn--primary btn--block"
            disabled={!canRequestRecipe}
            onClick={onRequestRecipe}
          >
            扒谱
          </button>
          <p className="reveal-hint">{REVEAL_HINT[pendingKind]}</p>
        </div>
      )}
      {showManual && <ManualDishForm activeDish={manualDish} onSubmit={onSubmitManual} />}
    </section>
  )
}
