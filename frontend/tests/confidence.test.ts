import { describe, expect, it } from 'vitest'

import {
  CLOSE_RACE_MARGIN,
  LOW_CONFIDENCE_THRESHOLD,
  confidenceVerdict,
} from '../src/confidence'
import type { DishCandidate } from '../src/types'

function candidate(name: string, confidence: number, index = 0): DishCandidate {
  return { index, name, confidence, provisional_name: false }
}

describe('confidenceVerdict', () => {
  it('还没识别时不给提示', () => {
    expect(confidenceVerdict([])).toEqual({ level: 'ok', note: null })
  })

  it('顶名够高且和第二名拉开距离时不打扰用户', () => {
    const verdict = confidenceVerdict([candidate('红烧肉', 0.885), candidate('梅菜扣肉', 0.007)])

    expect(verdict.level).toBe('ok')
    expect(verdict.note).toBeNull()
  })

  it('最低的一档：顶名不到阈值就提醒确认', () => {
    const verdict = confidenceVerdict([candidate('担担面', 0.459), candidate('炒面', 0.401)])

    expect(verdict.level).toBe('low')
    expect(verdict.note).toContain('45.9%')
  })

  it('阈值之上但前两名差得太小，仍然要提醒', () => {
    const top = 0.62
    const second = top - CLOSE_RACE_MARGIN / 2
    const verdict = confidenceVerdict([candidate('红烧肉', top), candidate('梅菜扣肉', second)])

    expect(verdict.level).toBe('close')
    expect(verdict.note).toContain('62.0%')
    expect(verdict.note).toContain('57.0%')
  })

  it('差距正好等于界值时算分得开，不提示', () => {
    const verdict = confidenceVerdict([
      candidate('红烧肉', LOW_CONFIDENCE_THRESHOLD + CLOSE_RACE_MARGIN),
      candidate('梅菜扣肉', LOW_CONFIDENCE_THRESHOLD),
    ])

    expect(verdict.level).toBe('ok')
  })

  it('只有一条候选时只看绝对置信度，不谈咬得紧', () => {
    expect(confidenceVerdict([candidate('可乐鸡翅', 0.9)]).level).toBe('ok')
    expect(confidenceVerdict([candidate('可乐鸡翅', 0.2)]).level).toBe('low')
  })

  it('最低一档优先于咬得紧：两个都命中时按更严重的那条提示', () => {
    const verdict = confidenceVerdict([candidate('担担面', 0.3), candidate('炒面', 0.29)])

    expect(verdict.level).toBe('low')
  })
})
