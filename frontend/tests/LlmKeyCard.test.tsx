import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...actual,
    probeLlmCredentials: vi.fn(),
    fetchLlmKey: vi.fn(),
    putLlmKey: vi.fn(),
    deleteLlmKey: vi.fn(),
  }
})

import {
  ApiError,
  deleteLlmKey,
  fetchLlmKey,
  probeLlmCredentials,
  putLlmKey,
} from '../src/api/client'
import type { LlmProbeResponse } from '../src/api/client'
import { LlmKeyCard } from '../src/components/LlmKeyCard'
import { LLM_KEY_STORAGE_KEY, readLlmKey } from '../src/llm-key'
import type { AccountUser } from '../src/types'

const FULL_KEY = 'sk-local-demo-key-abcdef'
const ACCOUNT: AccountUser = {
  id: 7,
  email: 'cook@example.com',
  display_name: '厨子',
  created_at: '2026-01-01T00:00:00',
}

/** 未登录（默认）或已登录两种身份各渲染一次，省得每个用例都写一遍 props。 */
function renderCard(props: { online?: boolean; user?: AccountUser | null } = {}) {
  return render(<LlmKeyCard online={props.online ?? true} user={props.user ?? null} />)
}

function accountBody(configured: boolean, masked = '') {
  return { configured, masked, base_url: null, model: null, updated_at: null }
}

beforeEach(() => {
  localStorage.clear()
  vi.mocked(probeLlmCredentials).mockReset()
  vi.mocked(fetchLlmKey).mockReset()
  vi.mocked(putLlmKey).mockReset()
  vi.mocked(deleteLlmKey).mockReset()
  vi.mocked(fetchLlmKey).mockResolvedValue(accountBody(false))
  vi.mocked(putLlmKey).mockImplementation(async (settings) =>
    accountBody(true, settings.apiKey.slice(0, 4) + '••••' + settings.apiKey.slice(-4)),
  )
  vi.mocked(deleteLlmKey).mockResolvedValue(undefined)
})

async function saveKey(user: ReturnType<typeof userEvent.setup>, key: string) {
  await user.click(screen.getByRole('button', { name: '填写' }))
  await user.type(screen.getByLabelText('模型 Key'), key)
  await user.click(screen.getByRole('button', { name: '保存' }))
}

describe('LlmKeyCard 填写与保存', () => {
  it('没填过时说明不填也能用，并给出填写入口', () => {
    renderCard()

    expect(screen.getByRole('heading', { name: '我的模型 Key' })).toBeInTheDocument()
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '填写' })).toBeInTheDocument()
  })

  it('填完保存后只显示掩码，完整 Key 不出现在页面上', async () => {
    const user = userEvent.setup()
    renderCard()

    await saveKey(user, FULL_KEY)

    expect(screen.getByText('sk-l••••cdef')).toBeInTheDocument()
    expect(screen.queryByText(FULL_KEY)).toBeNull()
    expect(screen.queryByDisplayValue(FULL_KEY)).toBeNull()
    expect(readLlmKey()?.apiKey).toBe(FULL_KEY)
  })

  it('地址与模型名一起保存，读出来与保存的一致', async () => {
    const user = userEvent.setup()
    renderCard()

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
    renderCard()

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByRole('alert')).toHaveTextContent('请填模型 Key')
    expect(localStorage.getItem(LLM_KEY_STORAGE_KEY)).toBeNull()
  })

  it('明文 http 公网地址保存不了，免得把 Key 发出去', async () => {
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole('button', { name: '填写' }))
    await user.type(screen.getByLabelText('模型 Key'), FULL_KEY)
    await user.type(screen.getByLabelText('服务地址（可留空）'), 'http://api.example.com/v1')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByRole('alert')).toHaveTextContent('https://')
    expect(localStorage.getItem(LLM_KEY_STORAGE_KEY)).toBeNull()
  })

  it('取消编辑不写入本机', async () => {
    const user = userEvent.setup()
    renderCard()

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

    renderCard()

    expect(screen.getByText('sk-l••••cdef')).toBeInTheDocument()
  })

  it('清除后回到空态，本机也不留', async () => {
    const user = userEvent.setup()
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: FULL_KEY, baseUrl: '', model: '' }),
    )
    renderCard()

    await user.click(screen.getByRole('button', { name: '清除' }))

    expect(readLlmKey()).toBeNull()
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
  })
})

describe('LlmKeyCard 登录后存在账号上', () => {
  it('显示账号里那份的掩码，并说明它存在账号上', async () => {
    vi.mocked(fetchLlmKey).mockResolvedValue(accountBody(true, 'sk-a••••beef'))

    renderCard({ user: ACCOUNT })

    expect(await screen.findByText('sk-a••••beef')).toBeInTheDocument()
    expect(screen.getByText(/存在你的账号里/)).toBeInTheDocument()
  })

  it('登录后保存走账号接口，本机不再留一份', async () => {
    const user = userEvent.setup()
    renderCard({ user: ACCOUNT })
    await screen.findByText(/不填也能用/)

    await saveKey(user, FULL_KEY)

    await waitFor(() => expect(putLlmKey).toHaveBeenCalled())
    expect(vi.mocked(putLlmKey).mock.calls[0][0].apiKey).toBe(FULL_KEY)
    expect(readLlmKey()).toBeNull()
    expect(screen.getByText(/存在你的账号里/)).toBeInTheDocument()
  })

  it('登录时把本机那份搬进账号，搬完清掉本机', async () => {
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: FULL_KEY, baseUrl: '', model: '' }),
    )

    renderCard({ user: ACCOUNT })

    await waitFor(() => expect(putLlmKey).toHaveBeenCalled())
    expect(vi.mocked(putLlmKey).mock.calls[0][0].apiKey).toBe(FULL_KEY)
    await waitFor(() => expect(readLlmKey()).toBeNull())
  })

  it('账号里已经有 Key 时，本机那份直接清掉而不是覆盖账号', async () => {
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: 'sk-stale-local-key-0000', baseUrl: '', model: '' }),
    )
    vi.mocked(fetchLlmKey).mockResolvedValue(accountBody(true, 'sk-a••••beef'))

    renderCard({ user: ACCOUNT })

    expect(await screen.findByText('sk-a••••beef')).toBeInTheDocument()
    expect(putLlmKey).not.toHaveBeenCalled()
    expect(readLlmKey()).toBeNull()
  })

  it('清除走账号接口', async () => {
    const user = userEvent.setup()
    vi.mocked(fetchLlmKey).mockResolvedValue(accountBody(true, 'sk-a••••beef'))
    renderCard({ user: ACCOUNT })
    await screen.findByText('sk-a••••beef')

    await user.click(screen.getByRole('button', { name: '清除' }))

    await waitFor(() => expect(deleteLlmKey).toHaveBeenCalled())
    expect(screen.getByText(/不填也能用/)).toBeInTheDocument()
  })

  it('保存失败时说一句，而不是假装存上了', async () => {
    const user = userEvent.setup()
    vi.mocked(putLlmKey).mockRejectedValue(new ApiError(40101, '登录状态已失效，请重新登录'))
    renderCard({ user: ACCOUNT })
    await screen.findByText(/不填也能用/)

    await saveKey(user, FULL_KEY)

    expect(await screen.findByRole('alert')).toHaveTextContent('登录状态已失效')
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
    renderCard()
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
    renderCard()
    await saveKey(user, FULL_KEY)

    await user.click(screen.getByRole('button', { name: '测试连接' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Key 无效')
  })

  it('后端整个连不上时也能给出一句话，不是空白', async () => {
    const user = userEvent.setup()
    vi.mocked(probeLlmCredentials).mockRejectedValue(
      new ApiError(50001, '无法连接后端服务，请确认服务已启动'),
    )
    renderCard()
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
    renderCard()
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
    renderCard({ online: false })

    expect(screen.getByText(/静态演示站不联网/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(probeLlmCredentials).not.toHaveBeenCalled()
  })

  it('演示站上即使有账号也不去打账号接口', () => {
    renderCard({ online: false, user: ACCOUNT })

    expect(fetchLlmKey).not.toHaveBeenCalled()
  })
})
