import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { EMPTY_HINT_MEMORY } from '../src/constants'
import { RecipeMemoryCard } from '../src/components/RecipeMemoryCard'
import { MEMORY_LIMIT } from '../src/memory'
import { memoryEntry } from './fixtures'

const noop = () => {}

describe('RecipeMemoryCard', () => {
  it('没有记忆时给出空态，清空按钮不可用', () => {
    render(
      <RecipeMemoryCard
        entries={[]}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )

    expect(screen.getByText(EMPTY_HINT_MEMORY)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '清空' })).toBeDisabled()
  })

  it('列出做过的菜与来源，并展示相对时间', () => {
    const entries = [
      memoryEntry({ dish: '红烧肉', source: 'library', at: Date.now() }),
      memoryEntry({
        dish: '土豆肉末拌面',
        name: '土豆肉末拌面',
        source: 'generated',
        at: Date.now() - 3 * 3600_000,
      }),
    ]
    render(
      <RecipeMemoryCard
        entries={entries}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )

    expect(screen.getByRole('button', { name: '复用 红烧肉' })).toBeInTheDocument()
    const generated = screen.getByRole('button', { name: '复用 土豆肉末拌面' })
    expect(generated.textContent).toContain('大模型生成')
    expect(generated.textContent).toContain('3 小时前')
    expect(screen.getByRole('button', { name: '清空' })).toBeEnabled()
  })

  it('点一条即回传该条目，供页面直接复用', async () => {
    const onShow = vi.fn()
    const entry = memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤' })
    const user = userEvent.setup()
    render(
      <RecipeMemoryCard
        entries={[entry]}
        onShow={onShow}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )

    await user.click(screen.getByRole('button', { name: '复用 番茄蛋花汤' }))

    expect(onShow).toHaveBeenCalledWith(entry)
  })

  it('清空按钮回传清空意图', async () => {
    const onClear = vi.fn()
    const user = userEvent.setup()
    render(
      <RecipeMemoryCard
        entries={[memoryEntry()]}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={onClear}
      />,
    )

    await user.click(screen.getByRole('button', { name: '清空' }))
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('星标反映收藏状态，点一下回传收藏意图', async () => {
    const onToggleFavorite = vi.fn()
    const user = userEvent.setup()
    render(
      <RecipeMemoryCard
        entries={[
          memoryEntry({ dish: '红烧肉' }),
          memoryEntry({ dish: '麻婆豆腐', name: '麻婆豆腐', favorite: true }),
        ]}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={onToggleFavorite}
        onClear={noop}
      />,
    )

    const star = screen.getByRole('button', { name: '收藏 红烧肉' })
    expect(star).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: '取消收藏 麻婆豆腐' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    await user.click(star)
    expect(onToggleFavorite).toHaveBeenCalledWith('红烧肉')
  })

  it('删除只回传删除意图，不会顺带复用那一条', async () => {
    const onRemove = vi.fn()
    const onShow = vi.fn()
    const user = userEvent.setup()
    render(
      <RecipeMemoryCard
        entries={[memoryEntry({ dish: '红烧肉' })]}
        onShow={onShow}
        onRemove={onRemove}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )

    await user.click(screen.getByRole('button', { name: '删除 红烧肉' }))

    expect(onRemove).toHaveBeenCalledWith('红烧肉')
    expect(onShow).not.toHaveBeenCalled()
  })

  it('记满 12 条时提示会挤掉谁，没记满就不提', () => {
    const full = Array.from({ length: MEMORY_LIMIT }, (_, i) =>
      memoryEntry({ dish: `菜${i}`, name: `菜${i}` }),
    )
    const { unmount } = render(
      <RecipeMemoryCard
        entries={full}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )

    expect(screen.getByText(/会挤掉最旧的一条/)).toBeInTheDocument()
    unmount()

    render(
      <RecipeMemoryCard
        entries={[memoryEntry()]}
        onShow={noop}
        onRemove={noop}
        onToggleFavorite={noop}
        onClear={noop}
      />,
    )
    expect(screen.queryByText(/记满/)).not.toBeInTheDocument()
  })
})
