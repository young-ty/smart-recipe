import { describe, expect, it } from 'vitest'

// 布局契约测试：jsdom 不解析 CSS，所以这里把样式表按原文读进来，守住断点与列位。
import css from '../src/styles/app.css?raw'

function mediaBlock(query: string): string {
  const start = css.indexOf(`@media (${query})`)
  expect(start, `未找到断点 ${query}`).toBeGreaterThan(-1)
  const rest = css.slice(start)
  const end = rest.indexOf('@media', 1)
  return end === -1 ? rest : rest.slice(0, end)
}

const base = css.slice(0, css.indexOf('@media'))

describe('三栏布局与响应式契约', () => {
  it('宽屏按 照片 / 候选 / 菜谱 三栏并排', () => {
    expect(base).toMatch(
      /grid-template-columns: minmax\([^)]*\) minmax\([^)]*\) minmax\([^)]*\);/,
    )
  })

  it('中屏退化为左右两栏，照片列留在左侧并跨两行', () => {
    const block = mediaBlock('max-width: 1120px')

    expect(block).toContain('grid-template-columns: minmax(220px, 1fr) minmax(0, 1.6fr);')
    expect(block).toMatch(/\.col--photo\s*\{[^}]*grid-column: 1;/)
    expect(block).toMatch(/\.col--photo\s*\{[^}]*grid-row: 1 \/ span 2;/)
    expect(block).toMatch(/\.col--candidates\s*\{[^}]*grid-column: 2;/)
    expect(block).toMatch(/\.col--recipe\s*\{[^}]*grid-column: 2;/)
  })

  it('只有手机宽度（< 640 px）才叠成单栏', () => {
    expect(mediaBlock('max-width: 640px')).toContain('grid-template-columns: 1fr;')
    expect(mediaBlock('max-width: 1120px')).not.toContain('grid-template-columns: 1fr;')
  })

  it('顶部数据说明条已撤下，骨架不再按视口写死高度', () => {
    expect(css).not.toMatch(/\.banner\s*\{/)
    expect(base).not.toMatch(/height:\s*[^;]*100vh/)
    expect(base).toMatch(/\.app\s*\{[^}]*flex: 1;/)
  })

  it('页头多占一行时骨架跟着变矮，不把三栏顶出视口', () => {
    expect(base).toMatch(/#root\s*\{[^}]*display: flex;/)
    expect(base).toMatch(/#root\s*\{[^}]*flex-direction: column;/)
    expect(base).toMatch(/\.topbar\s*\{[^}]*width: 100%;/)
    expect(base).toMatch(/\.app\s*\{[^}]*width: 100%;/)
  })

  it('左栏卡片按内容高度排，窗口不够高时整栏滚动而不是压扁卡片', () => {
    // 卡片被压扁后内部的动作按钮会伸到卡片外，被下一张卡片盖住点不到
    expect(base).toMatch(/\.col--photo\s*\{[^}]*overflow-y: auto;/)
    expect(base).toMatch(/\.col--photo > \.card\s*\{[^}]*flex: none;/)
  })

  it('手机单栏不参与骨架的 flex 分配', () => {
    const block = mediaBlock('max-width: 640px')
    expect(block).toMatch(/\.app\s*\{[^}]*flex: none;/)
    expect(block).not.toMatch(/\.app\s*\{[^}]*height: auto;/)
  })
})
