import { describe, expect, it } from 'vitest'

import { coveredIndexSet, pendingHint, pendingKind } from '../src/pending-hint'
import { DISHES } from './fixtures'

describe('coveredIndexSet', () => {
  it('把承接清单收成下标集合', () => {
    const covered = coveredIndexSet(DISHES.items)

    expect(covered.has(77)).toBe(true)
    expect(covered.has(83)).toBe(false)
    expect(covered.size).toBe(DISHES.items.length)
  })

  it('承接菜谱为 0 的类别不算库内（演示站的清单会这么标）', () => {
    const covered = coveredIndexSet([
      { index: 77, name: '红烧肉', provisional_name: false, library_recipe_id: 109 },
      { index: 83, name: '梅菜扣肉', provisional_name: false, library_recipe_id: 0 },
    ])

    expect(covered.has(77)).toBe(true)
    expect(covered.has(83)).toBe(false)
  })
})

describe('pendingKind', () => {
  const covered = coveredIndexSet(DISHES.items)

  it('下标在承接表里是读库', () => {
    expect(pendingKind(77, covered)).toBe('library')
  })

  it('下标不在承接表里是要生成', () => {
    expect(pendingKind(83, covered)).toBe('generate')
  })

  it('手输菜名没有下标，归为不确定', () => {
    expect(pendingKind(null, covered)).toBe('unknown')
  })

  it('覆盖清单没取到时归为不确定，不猜分支', () => {
    expect(pendingKind(83, null)).toBe('unknown')
    expect(pendingKind(77, null)).toBe('unknown')
  })
})

describe('pendingHint', () => {
  it('刚开始等时只说在做什么，不报秒数', () => {
    expect(pendingHint('library', 0)).toBe('库里已有这道菜，正在读取菜谱…')
    expect(pendingHint('generate', 0)).toBe('库里没有这道菜，正在让大模型现写…')
    expect(pendingHint('unknown', 0)).toBe('正在查询菜谱…')
  })

  it('等满一秒起带上已等时长', () => {
    expect(pendingHint('generate', 4)).toBe('库里没有这道菜，正在让大模型现写…（已等 4 秒）')
  })

  it('不确定分支等过三秒补一句：慢是因为可能在等大模型', () => {
    expect(pendingHint('unknown', 2)).toBe('正在查询菜谱…（已等 2 秒）')
    expect(pendingHint('unknown', 3)).toContain('会改由大模型现写')
  })

  it('已知要生成的分支不再重复解释', () => {
    expect(pendingHint('generate', 9)).toBe('库里没有这道菜，正在让大模型现写…（已等 9 秒）')
  })

  it('秒数取整，负值按 0 处理', () => {
    expect(pendingHint('library', 2.6)).toBe('库里已有这道菜，正在读取菜谱…（已等 2 秒）')
    expect(pendingHint('library', -3)).toBe('库里已有这道菜，正在读取菜谱…')
  })
})
