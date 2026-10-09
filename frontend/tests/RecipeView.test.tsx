import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RecipeView } from '../src/components/RecipeView'
import type { RecipeOut } from '../src/types'

const recipe: RecipeOut = {
  id: 109,
  origin: 'seed',
  name: '红烧肉',
  cook_time_minutes: 90,
  difficulty: '中等',
  rating: 4.7,
  servings: 4,
  description: '小火慢煨，收汁到挂勺。',
  required: [
    { name: '五花肉', amount: '500 克' },
    { name: '冰糖', amount: '20 克' },
  ],
  optional: [{ name: '八角', amount: '2 颗' }],
  steps: ['五花肉切块焯水', '炒糖色后下肉', '加水小火煨 60 分钟'],
  video_url: 'https://www.bilibili.com/search?keyword=%E7%BA%A2%E7%83%A7%E8%82%89%20%E5%81%9A%E6%B3%95',
}

describe('RecipeView', () => {
  it('按主料 / 辅料 / 做法三段渲染，用量跟在名称后', () => {
    render(<RecipeView recipe={recipe} provisionalName={false} source="library" />)

    expect(screen.getByText('红烧肉')).toBeInTheDocument()
    expect(screen.getByText('五花肉 · 500 克')).toHaveClass('tag')
    expect(screen.getByText('八角 · 2 颗')).toHaveClass('tag', 'tag--optional')
    expect(screen.getByText('主料')).toBeInTheDocument()
    expect(screen.getByText('做法')).toBeInTheDocument()
  })

  it('展示时长、难度、评分与人份', () => {
    render(<RecipeView recipe={recipe} provisionalName={false} source="library" />)
    expect(screen.getByText('90 分钟 · 难度 中等 · ★ 4.7 · 4 人份')).toBeInTheDocument()
  })

  it('步骤渲染为有序列表并保持顺序', () => {
    render(<RecipeView recipe={recipe} provisionalName={false} source="library" />)
    const items = screen.getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual(recipe.steps)
  })

  it('生成结果不给评分，且显式标注来源', () => {
    render(
      <RecipeView
        recipe={{ ...recipe, id: null, rating: null, origin: 'generated' }}
        provisionalName={false}
        source="generated"
      />,
    )

    expect(screen.getByText(/★ — · 4 人份 · 由大模型生成，仅供参考/)).toBeInTheDocument()
  })

  it('生成后自动入库的菜谱也标出来源，不冒充人工整理', () => {
    render(
      <RecipeView
        recipe={{ ...recipe, id: 10000, rating: null, origin: 'generated' }}
        provisionalName={false}
        source="library"
      />,
    )

    expect(screen.getByText(/★ — · 4 人份 · 大模型生成后自动入库/)).toBeInTheDocument()
  })

  it('人工整理的库内菜谱不带生成标记', () => {
    render(<RecipeView recipe={recipe} provisionalName={false} source="library" />)
    expect(screen.getByText('90 分钟 · 难度 中等 · ★ 4.7 · 4 人份')).toBeInTheDocument()
  })

  it('早期记忆条目缺 origin 字段时按请求来源兜底标注', () => {
    const legacy = { ...recipe, id: null, rating: null, origin: undefined } as unknown as RecipeOut
    render(<RecipeView recipe={legacy} provisionalName={false} source="generated" />)
    expect(screen.getByText(/由大模型生成，仅供参考/)).toBeInTheDocument()
  })

  it('名称待确认时打标记，避免把占位名当正式菜名', () => {
    render(<RecipeView recipe={recipe} provisionalName source="library" />)
    expect(screen.getByText('名称待确认')).toHaveClass('tag', 'tag--provisional')
  })

  it('视频入口用后端拼接的搜索链接', () => {
    render(<RecipeView recipe={recipe} provisionalName={false} source="library" />)
    const link = screen.getByRole('link', { name: '打开视频' })
    expect(link).toHaveAttribute('href', recipe.video_url)
    expect(link).toHaveAttribute('target', '_blank')
  })
})
