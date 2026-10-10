import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return { ...actual, probeLlmCredentials: vi.fn() }
})

import { ApiError, probeLlmCredentials } from '../src/api/client'
import type { LlmProbeResponse } from '../src/api/client'
import { LlmKeyCard } from '../src/components/LlmKeyCard'
import { LLM_KEY_STORAGE_KEY, readLlmKey } from '../src/llm-key'

const FULL_KEY = 'sk-local-demo-key-abcdef'

beforeEach(() => {
  localStorage.clear()
  vi.mocked(probeLlmCredentials).mockReset()
})

async function saveKey(user: ReturnType<typeof userEvent.setup>, key: string) {
  await user.click(screen.getByRole('button', { name: '填写' }))
  await user.type(screen.getByLabelText('模型 Key'), key)
  await user.click(screen.getByRole('button', { name: '保存' }))
}

describe('LlmKeyCard 填写与保存', () => {
  it('没填过时说明不填也能用，并给出填写入口', () => {
    render(<LlmKeyCard online />)

    expect(screen.getByRole('heading', { name: '我的模型 Key' })).toBeInTheDocument()
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '填写' })).toBeInTheDocument()
  })

  it('填完保存后只显示掩码，完整 Key 不出现在页面上', async () => {
    const user = userEvent.setup()
    render(<LlmKeyCard online />)

    await saveKey(user, FULL_KEY)

    expect(screen.getByText('sk-l••••cdef')).toBeInTheDocument()
    expect(screen.queryByText(FULL_KEY)).toBeNull()
    expect(screen.queryByDisplayValue(FULL_KEY)).toBeNull()
    expect(readLlmKey()?.apiKey).toBe(FULL_KEY)
  })

  it('地址与模型名一起保存，读出来与保存的一致', async () => {
    const user = userEvent.setup()
    render(<LlmKeyCard online />)

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.type(screen.getByLabelText('模型 Key'), FULL_KEY)
    await user.type(screen.getByLabelText('服务地址（可留空）'), 'https://api.deepseek.com/v1/')
    await user.type(screen.getByLabelText('模型名（可留空）'), 'deepseek-chat')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(readLlmKey()).toEqual({
      apiKey: FULL_KEY,
      baseUrl: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
    })
    expect(screen.getByText('https://api.deepseek.com/v1')).toBeInTheDocument()
    expect(screen.getByText('deepseek-chat')).toBeInTheDocument()
  })

  it('Key 空着保存时当场提示，不写进本机', async () => {
    const user = userEvent.setup()
    render(<LlmKeyCard online />)

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByRole('alert')).toHaveTextContent('请填模型 Key')
    expect(localStorage.getItem(LLM_KEY_STORAGE_KEY)).toBeNull()
  })

  it('明文 http 公网地址保存不了，免得把 Key 发出去', async () => {
    const user = userEvent.setup()
    render(<LlmKeyCard online />)

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.type(screen.getByLabelText('模型 Key'), FULL_KEY)
    await user.type(screen.getByLabelText('服务地址（可留空）'), 'http://api.example.com/v1')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByRole('alert')).toHaveTextContent('https://')
    expect(localStorage.getItem(LLM_KEY_STORAGE_KEY)).toBeNull()
  })

  it('取消编辑不写入本机', async () => {
    const user = userEvent.setup()
    render(<LlmKeyCard online />)

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.type(screen.getByLabelText('模型 Key'), FULL_KEY)
    await user.click(screen.getByRole('button', { name: '取消' }))

    expect(localStorage.getItem(LLM_KEY_STORAGE_KEY)).toBeNull()
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
  })

  it('打开页面时读得到上次填的 Key', () => {
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: FULL_KEY, baseUrl: '', model: '' }),
    )

    render(<LlmKeyCard online />)

    expect(screen.getByText('sk-l••••cdef')).toBeInTheDocument()
  })

  it('清除后回到空态，本机也不留', async () => {
    const user = userEvent.setup()
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: FULL_KEY, baseUrl: '', model: '' }),
    )
    render(<LlmKeyCard online />)

    await user.click(screen.getByRole('button', { name: '清除' }))

    expect(readLlmKey()).toBeNull()
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
  })
})

describe('LlmKeyCard 测试连接', () => {
  it('连接正常时把服务端那句话显示出来', async () => {
    const user = userEvent.setup()
    vi.mocked(probeLlmCredentials).mockResolvedValue({
      ok: true,
      configured: true,
      model: 'deepseek-chat',
      detail: '连接正常，模型 deepseek-chat 可用',
    })
    render(<LlmKeyCard online />)
    await saveKey(user, FULL_KEY)

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(probeLlmCredentials).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent('连接正常，模型 deepseek-chat 可用')
  })

  it('Key 错时说清是 Key 的问题', async () => {
    const user = userEvent.setup()
    vi.mocked(probeLlmCredentials).mockResolvedValue({
      ok: false,
      configured: true,
      model: 'deepseek-chat',
      detail: 'Key 无效，或这个 Key 没有调用该模型的权限',
    })
    render(<LlmKeyCard online />)
    await saveKey(user, FULL_KEY)

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Key 无效')
  })

  it('后端整个连不上时也能给出一句话，不是空白', async () => {
    const user = userEvent.setup()
    vi.mocked(probeLlmCredentials).mockRejectedValue(
      new ApiError(50001, '无法连接后端服务，请确认服务已启动'),
    )
    render(<LlmKeyCard online />)
    await saveKey(user, FULL_KEY)

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(await screen.findByRole('status')).toHaveTextContent('无法连接后端服务')
  })

  it('探活期间按钮显示进行中，避免重复点', async () => {
    const user = userEvent.setup()
    let release: (value: LlmProbeResponse) => void = () => {}
    vi.mocked(probeLlmCredentials).mockReturnValue(
      new Promise<LlmProbeResponse>((resolve) => {
        release = resolve
      }),
    )
    render(<LlmKeyCard online />)
    await saveKey(user, FULL_KEY)

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    const busy = screen.getByRole('button', { name: '测试中…' })
    expect(busy).toBeDisabled()

    release({ ok: true, configured: true, model: 'm', detail: '连接正常' })
    await waitFor(() => expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled())
  })
})

describe('LlmKeyCard 在静态演示站上', () => {
  it('说明这里生成不了菜谱，并把测试连接挡住', async () => {
    const user = userEvent.setup()
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: FULL_KEY, baseUrl: '', model: '' }),
    )
    render(<LlmKeyCard online={false} />)

    expect(screen.getByText(/静态演示站不联网/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(probeLlmCredentials).not.toHaveBeenCalled()
  })
})
