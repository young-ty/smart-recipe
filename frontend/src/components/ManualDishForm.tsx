import { useState } from 'react'

type Props = {
  /** 当前生效的手输菜名；为 null 表示正按候选解析 */
  activeDish: string | null
  onSubmit: (dish: string) => void
}

/** FR-10：候选都不对（或识别不出候选）时直接输入菜名，走同一条菜名 → 菜谱链路。 */
export function ManualDishForm({ activeDish, onSubmit }: Props) {
  const [draft, setDraft] = useState('')
  const ready = draft.trim().length > 0

  return (
    <form
      className="manual"
      onSubmit={(event) => {
        event.preventDefault()
        if (!ready) {
          return
        }
        onSubmit(draft.trim())
      }}
    >
      <label className="manual-label" htmlFor="manual-dish-input">
        识别不准？手动输入菜名
      </label>
      <div className="manual-row">
        <input
          id="manual-dish-input"
          className="manual-input"
          type="text"
          value={draft}
          maxLength={30}
          placeholder="例如：红烧肉"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="btn btn--secondary manual-btn" disabled={!ready}>
          用这个菜名
        </button>
      </div>
      {activeDish !== null && (
        <p className="manual-active">当前按手输菜名解析：{activeDish}</p>
      )}
    </form>
  )
}
