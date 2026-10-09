import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { CandidateButton } from '../src/components/CandidateButton'
import type { DishCandidate } from '../src/types'

const candidate: DishCandidate = {
  index: 77,
  name: '红烧肉',
  confidence: 0.819,
  provisional_name: true,
}

describe('CandidateButton', () => {
  it('渲染菜名、类别下标与置信度百分比', () => {
    render(<CandidateButton candidate={candidate} selected onSelect={() => {}} />)

    expect(screen.getByText('红烧肉')).toBeInTheDocument()
    expect(screen.getByText('#77')).toBeInTheDocument()
    expect(screen.getByText('81.9%')).toBeInTheDocument()
    const fill = document.querySelector('.bar-fill') as HTMLElement
    expect(fill.style.width).toBe('81.9%')
  })

  it('以 radio 语义暴露选中状态', () => {
    const { rerender } = render(
      <CandidateButton candidate={candidate} selected={false} onSelect={() => {}} />,
    )
    const button = screen.getByRole('radio')
    expect(button).toHaveAttribute('aria-checked', 'false')
    expect(button).toHaveAttribute('aria-label', '红烧肉 81.9%')
    expect(button.className).not.toContain('is-selected')

    rerender(<CandidateButton candidate={candidate} selected onSelect={() => {}} />)
    expect(screen.getByRole('radio')).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio').className).toContain('is-selected')
  })

  it('点击与键盘操作都回传类别下标', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<CandidateButton candidate={candidate} selected={false} onSelect={onSelect} />)

    await user.click(screen.getByRole('radio'))
    screen.getByRole('radio').focus()
    await user.keyboard('{Enter}')

    expect(onSelect).toHaveBeenCalledTimes(2)
    expect(onSelect).toHaveBeenCalledWith(77)
  })
})
