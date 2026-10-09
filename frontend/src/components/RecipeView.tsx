import { PROVISIONAL_TAG } from '../constants'
import type { IngredientLine, RecipeOut, RecipeSource } from '../types'
import { StepList } from './StepList'

type Props = {
  recipe: RecipeOut
  /** 类别名待确认时打标记，避免把占位名当成正式菜名 */
  provisionalName: boolean
  source: RecipeSource | null
}

function lineLabel(line: IngredientLine): string {
  return line.amount ? `${line.name} · ${line.amount}` : line.name
}

/** 来源提示：生成的菜谱落库后仍要标出来，不能和人工整理的种子数据混在一起。 */
function sourceNote(recipe: RecipeOut, source: RecipeSource | null): string {
  if (recipe.origin === 'generated') {
    return recipe.id == null ? ' · 由大模型生成，仅供参考' : ' · 大模型生成后自动入库'
  }
  // 早先存进本机记忆的菜谱没有 origin 字段，退回按请求来源标注
  return source === 'generated' ? ' · 由大模型生成，仅供参考' : ''
}

/** 区域 C：菜谱正文。库内与生成两条分支共用同一形状，只有来源提示不同。 */
export function RecipeView({ recipe, provisionalName, source }: Props) {
  const rating = recipe.rating == null ? '—' : recipe.rating.toFixed(1)
  const note = sourceNote(recipe, source)

  return (
    <article className="recipe">
      <div className="recipe-head">
        <h3 className="recipe-name">{recipe.name}</h3>
        {provisionalName ? <span className="tag tag--provisional">{PROVISIONAL_TAG}</span> : null}
      </div>
      <p className="recipe-meta">
        {recipe.cook_time_minutes} 分钟 · 难度 {recipe.difficulty} · ★ {rating} · {recipe.servings} 人份
        {note}
      </p>
      {recipe.description ? <p className="recipe-desc">{recipe.description}</p> : null}

      <p className="section-label">主料</p>
      <div className="tags">
        {recipe.required.map((line) => (
          <span className="tag" key={`required-${line.name}`}>
            {lineLabel(line)}
          </span>
        ))}
      </div>

      <p className="section-label">辅料</p>
      <div className="tags">
        {recipe.optional.map((line) => (
          <span className="tag tag--optional" key={`optional-${line.name}`}>
            {lineLabel(line)}
          </span>
        ))}
      </div>

      <p className="section-label">做法</p>
      <StepList steps={recipe.steps} />

      <div className="video">
        <div className="video-text">
          <p className="video-title">视频教程 · {recipe.name}</p>
          <p className="video-url">{recipe.video_url}</p>
        </div>
        <a className="video-btn" href={recipe.video_url} target="_blank" rel="noreferrer noopener">
          打开视频
        </a>
      </div>
    </article>
  )
}
