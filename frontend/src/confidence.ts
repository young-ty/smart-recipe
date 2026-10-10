// 候选的把握度判断（FR-03）：顶名置信度太低、或者前两名咬得很紧时，
// 界面要先提醒用户确认，不能把一个 45% 的猜测当成确定答案给出去。
//
// 判断放在前端而不是后端：模型输出的置信度原样返回，怎么解读是展示层的事，
// 阈值改动不需要动接口契约。

import type { DishCandidate } from './types'

/** 顶名低于它就先别下结论：真实权重上误判案例的顶名普遍落在 0.4 到 0.5 之间 */
export const LOW_CONFIDENCE_THRESHOLD = 0.5

/** 顶名与第二名的差距小于它，说明模型分不开这两道菜 */
export const CLOSE_RACE_MARGIN = 0.1

export type ConfidenceLevel = 'ok' | 'low' | 'close'

export type ConfidenceVerdict = {
  level: ConfidenceLevel
  /** 给用户看的提示；ok 时为 null */
  note: string | null
}

const OK: ConfidenceVerdict = { level: 'ok', note: null }

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

/**
 * 只看前两名：候选按置信度降序返回，后面的名次与「能不能确定是这道菜」无关。
 *
 * 只有一条候选时不谈「咬得紧」，只按绝对置信度判。
 */
export function confidenceVerdict(candidates: DishCandidate[]): ConfidenceVerdict {
  const top = candidates[0]
  if (!top) {
    return OK
  }

  const topPercent = percent(top.confidence)
  if (top.confidence < LOW_CONFIDENCE_THRESHOLD) {
    return {
      level: 'low',
      note: `识别把握不大，最高只有 ${topPercent}，请确认是不是这道菜`,
    }
  }

  const second = candidates[1]
  // 置信度是浮点：0.6 减 0.5 算出来是 0.099999…，不收一下就正好在界值上翻面
  const gap = second
    ? Math.round((top.confidence - second.confidence) * 10000) / 10000
    : Infinity
  if (gap < CLOSE_RACE_MARGIN) {
    return {
      level: 'close',
      note: `前两名咬得很紧（${topPercent} / ${percent(second.confidence)}），请确认是哪一道`,
    }
  }

  return OK
}
