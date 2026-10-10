// 解析等待期的分流文案：库里已有就直说在读库，库外的直说要等大模型，
// 拿不到覆盖清单或手输菜名时不猜分支，只说「正在查询」。

import type { DishItem } from './types'

export type PendingKind = 'library' | 'generate' | 'unknown'

/**
 * 后端 /dishes 只回可承接的类别，收成集合后按候选下标判分支是 O(1)。
 *
 * 静态演示站的清单由快照汇总而来，库里没有的菜写成 0，这里一并滤掉；
 * 真后端不会下发 0（承接表里 recipe_id 为空的行本来就查不出来）。
 */
export function coveredIndexSet(items: DishItem[]): Set<number> {
  return new Set(items.filter((item) => item.library_recipe_id > 0).map((item) => item.index))
}

/**
 * 这次解析会走哪条分支：
 * 候选带下标且在承接表里是库内命中，不在表里是要生成；手输菜名没有下标、
 * 覆盖清单没取到时都归为「不确定」——没依据的话不写死结论。
 */
export function pendingKind(index: number | null, covered: ReadonlySet<number> | null): PendingKind {
  if (index === null || covered === null) {
    return 'unknown'
  }
  return covered.has(index) ? 'library' : 'generate'
}

const BASE: Record<PendingKind, string> = {
  library: '库里已有这道菜，正在读取菜谱…',
  generate: '库里没有这道菜，正在让大模型现写…',
  unknown: '正在查询菜谱…',
}

/** 等过这个秒数才补一句为什么慢：几毫秒的响应不该闪出提示 */
export const SLOW_SECONDS = 3

export function pendingHint(kind: PendingKind, elapsedSeconds: number): string {
  const seconds = Math.max(0, Math.floor(elapsedSeconds))
  const base = BASE[kind]
  if (seconds < 1) {
    return base
  }
  if (kind === 'unknown' && seconds >= SLOW_SECONDS) {
    return base + '（已等 ' + seconds + ' 秒；库里没有的话会改由大模型现写，得多等几秒）'
  }
  return base + '（已等 ' + seconds + ' 秒）'
}
