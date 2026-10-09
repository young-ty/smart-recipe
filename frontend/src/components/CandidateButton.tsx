import type { DishCandidate } from '../types'

type Props = {
  candidate: DishCandidate
  selected: boolean
  onSelect: (index: number) => void
}

/** 单个候选菜品：名称 + 类别下标 + 置信度条（数值全部来自后端）。 */
export function CandidateButton({ candidate, selected, onSelect }: Props) {
  const percent = (candidate.confidence * 100).toFixed(1)
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={`${candidate.name} ${percent}%`}
      className={selected ? 'cand is-selected' : 'cand'}
      onClick={() => onSelect(candidate.index)}
    >
      <span className="cand-name">
        {candidate.name}
        <small>#{candidate.index}</small>
      </span>
      <span className="bar">
        <span className="bar-fill" style={{ width: `${percent}%` }} />
      </span>
      <span className="cand-pct">{percent}%</span>
    </button>
  )
}
