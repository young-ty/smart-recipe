import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { PhotoRecognizeCard } from '../src/components/PhotoRecognizeCard'
import type { DemoSampleOption } from '../src/demo'

const SAMPLES: DemoSampleOption[] = [
  {
    id: 'braised-pork',
    caption: '红烧肉',
    note: '库内命中',
    fileName: 'dish-braised-pork.jpg',
    url: '/demo/dishes/dish-braised-pork.jpg',
  },
  {
    id: 'omurice',
    caption: '蛋包饭',
    note: '大模型生成后落库',
    fileName: 'dish-omurice.jpg',
    url: '/demo/dishes/dish-omurice.jpg',
  },
]

function renderCard(props: Partial<Parameters<typeof PhotoRecognizeCard>[0]> = {}) {
  return render(
    <PhotoRecognizeCard
      previewUrl={null}
      status="idle"
      error={null}
      caption="模型: dish-convnext-base-208-onnx"
      badge="预置模型输出"
      statusText={null}
      onSelectFile={() => {}}
      onRecognize={() => {}}
      onReset={() => {}}
      {...props}
    />,
  )
}

describe('拍照识别卡片', () => {
  it('传了示例图就展示出来，点一张把 id 回传出去', async () => {
    const onSelectSample = vi.fn()
    renderCard({
      samples: SAMPLES,
      activeSampleId: 'omurice',
      notice: '大模型生成后落库',
      onSelectSample,
    })

    const active = screen.getByRole('button', { name: '蛋包饭' })
    expect(active).toHaveClass('is-active')

    await userEvent.click(screen.getByRole('button', { name: '红烧肉' }))
    expect(onSelectSample).toHaveBeenCalledWith('braised-pork')
  })

  it('没传示例图（联调版本）就看不到示例图入口', () => {
    renderCard()
    expect(screen.queryByText('演示图：点一张直接看结果')).toBeNull()
  })

  it('有说明文字时展示在卡片里', () => {
    renderCard({ notice: '注意：这张照片没有跑模型' })
    expect(screen.getByText('注意：这张照片没有跑模型')).toBeInTheDocument()
  })
})
