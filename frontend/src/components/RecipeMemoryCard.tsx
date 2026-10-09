import { EMPTY_HINT_MEMORY, SOURCE_LABEL } from '../constants'
import { formatRelativeTime } from '../memory'
import type { MemoryEntry } from '../memory'
import { PanelHeader } from './PanelHeader'

type Props = {
  entries: MemoryEntry[]
  onShow: (entry: MemoryEntry) => void
  onClear: () => void
}

/**
 * 区域 C 下方：本机菜谱记忆（FR-15）。
 *
 * 列出做过的菜，点一条即复用——不再请求后端、也不再要求重新上传照片；
 * 识别到做过的菜时还会自动命中这里，跳过检索与生成。
 */
export function RecipeMemoryCard({ entries, onShow, onClear }: Props) {
  return (
    <section className="card memory-card">
      <PanelHeader
        title="菜谱记忆"
        trailing={
          <button
            type="button"
            className="link-btn"
            onClick={onClear}
            disabled={entries.length === 0}
          >
            清空
          </button>
        }
      />
      <div className="memory-list">
        {entries.length > 0 ? (
          entries.map((entry) => {
            return (
              <button
                key={entry.dish}
                type="button"
                className="memory-item"
                aria-label={'复用 ' + entry.name}
                onClick={() => onShow(entry)}
              >
                <span className="memory-name">
                  {entry.name}
                  <small>{SOURCE_LABEL[entry.source]}</small>
                </span>
                <span className="memory-time">{formatRelativeTime(entry.at)}</span>
              </button>
            )
          })
        ) : (
          <p className="empty memory-empty">{EMPTY_HINT_MEMORY}</p>
        )}
      </div>
    </section>
  )
}
