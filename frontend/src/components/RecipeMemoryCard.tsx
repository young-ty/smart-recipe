import { EMPTY_HINT_MEMORY, SOURCE_LABEL } from '../constants'
import { formatRelativeTime, memoryLimitNotice } from '../memory'
import type { MemoryEntry } from '../memory'
import { PanelHeader } from './PanelHeader'

type Props = {
  entries: MemoryEntry[]
  onShow: (entry: MemoryEntry) => void
  onRemove: (dish: string) => void
  onToggleFavorite: (dish: string) => void
  onClear: () => void
}

/**
 * 区域 C 下方：本机菜谱记忆（FR-15）。
 *
 * 列出做过的菜，点一条即复用——不再请求后端、也不再要求重新上传照片；
 * 识别到做过的菜时还会自动命中这里，跳过检索与生成。
 * 条目上带收藏与删除：收藏的排在前面、记满时也不会被挤掉，记错的单条删得掉。
 */
export function RecipeMemoryCard({ entries, onShow, onRemove, onToggleFavorite, onClear }: Props) {
  const limitNotice = memoryLimitNotice(entries)

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
          entries.map((entry) => (
            <div
              key={entry.dish}
              className={entry.favorite ? 'memory-item memory-item--fav' : 'memory-item'}
            >
              <button
                type="button"
                className="memory-open"
                aria-label={'复用 ' + entry.name}
                onClick={() => onShow(entry)}
              >
                <span className="memory-name">
                  {entry.name}
                  <small>{SOURCE_LABEL[entry.source]}</small>
                </span>
                <span className="memory-time">{formatRelativeTime(entry.at)}</span>
              </button>
              <button
                type="button"
                className="memory-icon"
                aria-pressed={entry.favorite}
                aria-label={(entry.favorite ? '取消收藏 ' : '收藏 ') + entry.name}
                onClick={() => onToggleFavorite(entry.dish)}
              >
                {entry.favorite ? '★' : '☆'}
              </button>
              <button
                type="button"
                className="memory-icon memory-icon--danger"
                aria-label={'删除 ' + entry.name}
                onClick={() => onRemove(entry.dish)}
              >
                ×
              </button>
            </div>
          ))
        ) : (
          <p className="empty memory-empty">{EMPTY_HINT_MEMORY}</p>
        )}
      </div>
      {limitNotice !== null && <p className="memory-limit">{limitNotice}</p>}
    </section>
  )
}
