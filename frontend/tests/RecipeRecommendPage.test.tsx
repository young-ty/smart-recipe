import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/api/client')>()
  return {
    ...actual,
    recognizeImage: vi.fn(),
    resolveRecipe: vi.fn(),
    fetchHealth: vi.fn(),
    fetchDishes: vi.fn(),
    fetchMe: vi.fn(),
    fetchMemories: vi.fn(),
    putMemories: vi.fn(),
    fetchLlmKey: vi.fn(),
    putLlmKey: vi.fn(),
    deleteLlmKey: vi.fn(),
    loginAccount: vi.fn(),
    registerAccount: vi.fn(),
    logoutAccount: vi.fn(),
    probeLlmCredentials: vi.fn(),
  }
})

import {
  ApiError,
  fetchDishes,
  fetchHealth,
  fetchMe,
  fetchMemories,
  fetchLlmKey,
  loginAccount,
  logoutAccount,
  putLlmKey,
  probeLlmCredentials,
  putMemories,
  recognizeImage,
  resolveRecipe,
} from '../src/api/client'
import { EMPTY_HINT_MEMORY } from '../src/constants'
import { LLM_KEY_STORAGE_KEY } from '../src/llm-key'
import { RecipeRecommendPage } from '../src/pages/RecipeRecommendPage'
import type { AccountUser } from '../src/types'
import {
  AMBIGUOUS_RECOGNIZED,
  DISHES,
  HEALTH,
  LIBRARY,
  RECOGNIZED,
  generatedRecipe,
  memoryEntry,
  picture,
} from './fixtures'
import type { ResolveResponse } from '../src/types'
import { MEMORY_STORAGE_KEY, memoryStorageKey } from '../src/memory'

// 演示图「梅菜扣肉」在库存里没有菜谱，用来验证「库外 + 未配 Key」的降级提示
const UNCOVERED_INDEX = 83

function uploadPicture(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
}

/** 菜谱面板内的查询：来源徽标与记忆条目可能同名，断言要限定在面板里 */
function recipePanel() {
  const section = screen.getByTestId('recipe-scroll').closest('section') as HTMLElement
  return within(section)
}

beforeEach(() => {
  // 菜谱记忆落在 localStorage，用例之间要互不影响
  localStorage.clear()
  vi.mocked(recognizeImage).mockReset()
  vi.mocked(resolveRecipe).mockReset()
  vi.mocked(fetchHealth).mockReset()
  vi.mocked(fetchDishes).mockReset()
  vi.mocked(fetchMe).mockReset()
  vi.mocked(fetchMemories).mockReset()
  vi.mocked(putMemories).mockReset()
  vi.mocked(loginAccount).mockReset()
  vi.mocked(logoutAccount).mockReset()
  vi.mocked(probeLlmCredentials).mockReset()
  vi.mocked(fetchHealth).mockResolvedValue(HEALTH)
  vi.mocked(fetchDishes).mockResolvedValue(DISHES)
  vi.mocked(resolveRecipe).mockResolvedValue(LIBRARY)
  // 默认按未登录走：账号相关的用例各自改这两条
  vi.mocked(fetchMe).mockResolvedValue({ user: null })
  vi.mocked(fetchMemories).mockResolvedValue({ entries: [] })
  vi.mocked(putMemories).mockImplementation(async (entries) => ({ entries }))
  vi.mocked(logoutAccount).mockResolvedValue(undefined)
  // 未登录时不会打账号里的 Key 接口；登录相关的用例各自覆盖
  vi.mocked(fetchLlmKey).mockResolvedValue({
    configured: false,
    masked: '',
    base_url: null,
    model: null,
    updated_at: null,
  })
  vi.mocked(putLlmKey).mockResolvedValue({
    configured: true,
    masked: 'sk-l••••cdef',
    base_url: null,
    model: null,
    updated_at: null,
  })
})

/** 识别出候选之后，菜谱要用户点一下「扒谱」才会去取 */
async function revealRecipe(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '扒谱' }))
}

describe('RecipeRecommendPage 初始状态', () => {
  it('健康检查返回前显示检测中占位', () => {
    vi.mocked(fetchHealth).mockReturnValue(new Promise(() => {}))

    render(<RecipeRecommendPage />)

    expect(screen.getByRole('heading', { name: '拍照识别' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '识别结果' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '菜谱与做法' })).toBeInTheDocument()
    expect(screen.getByText('识别后这里显示候选菜品与置信度')).toBeInTheDocument()
    expect(screen.getByText('左边选好菜名后点「扒谱」，这里显示菜谱与做法')).toBeInTheDocument()
    expect(screen.getByText('模型: 检测中…')).toBeInTheDocument()
    expect(screen.getByText('模型状态检测中')).toBeInTheDocument()
  })

  it('页头写明使用场景：拍照记录、一键扒谱', () => {
    render(<RecipeRecommendPage />)

    expect(screen.getByRole('heading', { name: '基于深度学习的智能菜谱识别' })).toBeInTheDocument()
    expect(screen.getByText('拍照记录一道好菜 → 认出菜名 → 一键扒谱')).toBeInTheDocument()
  })

  it('首次识别前就展示健康检查下发的真实模型与类别口径', async () => {
    render(<RecipeRecommendPage />)

    expect(
      await screen.findByText('模型: dish-convnext-base-208-onnx · 208 类 · 类别清单已交付'),
    ).toBeInTheDocument()
    expect(screen.getByText('真实模型推理')).toBeInTheDocument()
    expect(recognizeImage).not.toHaveBeenCalled()
  })

  it('模型降级且类别清单缺失时，两件事都要标出来', async () => {
    vi.mocked(fetchHealth).mockResolvedValue({
      ...HEALTH,
      model: {
        ...HEALTH.model,
        loaded: false,
        degraded: true,
        labels_available: false,
        version: 'mock-dish-convnext-base-208',
      },
    })

    render(<RecipeRecommendPage />)

    expect(
      await screen.findByText('模型: mock-dish-convnext-base-208 · 208 类 · 类别名称待确认 · 当前为模拟结果'),
    ).toBeInTheDocument()
    expect(screen.getByText('模拟 CV 模型推理')).toBeInTheDocument()
  })

  it('页头不再有数据说明条，来源披露收敛到图注与角标', async () => {
    const { container } = render(<RecipeRecommendPage />)

    await screen.findByText(/^模型: /)
    expect(container.querySelector('.banner')).toBeNull()
    expect(screen.queryByText(/^数据说明 ·/)).toBeNull()
  })

  it('健康检查失败时保持占位且不崩溃', async () => {
    vi.mocked(fetchHealth).mockRejectedValue(
      new ApiError(50001, '无法连接后端服务，请确认服务已启动'),
    )

    render(<RecipeRecommendPage />)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(screen.getByText('模型: 检测中…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始识别' })).toBeDisabled()
  })

  it('上传窗口固定在左侧分区，其余两栏依次是候选与菜谱', () => {
    vi.mocked(fetchHealth).mockReturnValue(new Promise(() => {}))

    const { container } = render(<RecipeRecommendPage />)

    const columns = Array.from(container.querySelectorAll('.app > .col'))
    expect(columns.map((node) => node.className)).toEqual([
      'col col--photo',
      'col col--candidates',
      'col col--recipe',
    ])
    // 左栏卡片自上而下：标题 → 上传窗口 → 按钮 → 模型脚注；中间不再有示例图行
    const card = columns[0].querySelector('.card') as HTMLElement
    expect(Array.from(card.children).map((node) => node.className)).toEqual([
      'panel-head',
      'dropzone',
      'actions',
      'caption',
    ])
  })

  it('菜谱记忆固定在右栏菜谱面板下方，左栏只留给拍照', () => {
    vi.mocked(fetchHealth).mockReturnValue(new Promise(() => {}))

    const { container } = render(<RecipeRecommendPage />)

    const photoCol = container.querySelector('.col--photo') as HTMLElement
    const recipeCol = container.querySelector('.col--recipe') as HTMLElement
    expect(recipeCol.querySelector('.memory-card')).not.toBeNull()
    expect(photoCol.querySelector('.memory-card')).toBeNull()
    // 右栏自上而下是菜谱面板与记忆卡
    expect(Array.from(recipeCol.children).map((node) => node.className)).toEqual([
      'card card--grow',
      'card memory-card',
    ])
  })

  it('未选择图片时「开始识别」不可点击，左栏只有上传窗口', () => {
    const { container } = render(<RecipeRecommendPage />)

    expect(screen.getByRole('button', { name: '开始识别' })).toBeDisabled()
    expect(container.querySelector('.samples')).toBeNull()
    expect(screen.queryByRole('button', { name: /^示例图/ })).toBeNull()
  })
})

describe('RecipeRecommendPage 识别与菜谱链路', () => {
  it('识别后选中 top-1 并展示库内菜谱', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    const runButton = screen.getByRole('button', { name: '开始识别' })
    expect(runButton).toBeEnabled()
    await user.click(runButton)

    const top1 = await screen.findByRole('radio', { name: '红烧肉 88.5%' })
    expect(top1).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '梅菜扣肉 0.7%' })).toHaveAttribute(
      'aria-checked',
      'false',
    )
    expect(recognizeImage).toHaveBeenCalledTimes(1)

    await revealRecipe(user)
    await waitFor(() => expect(resolveRecipe).toHaveBeenCalledWith('红烧肉', 77))
    expect(await screen.findByRole('heading', { level: 3, name: '红烧肉' })).toBeInTheDocument()
    expect(recipePanel().getByText('库内菜谱')).toBeInTheDocument()
    expect(screen.getByText('五花肉 · 500 克')).toBeInTheDocument()
    expect(screen.getByText('五花肉切块焯水')).toBeInTheDocument()
  })

  it('顶名置信度低时，在候选上方提醒先确认', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(AMBIGUOUS_RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    const note = await screen.findByRole('status')
    expect(note).toHaveTextContent('识别把握不大')
    expect(note).toHaveTextContent('45.9%')
  })

  it('置信度够高且拉开距离时，不出现把握提示', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByRole('radio', { name: '红烧肉 88.5%' })

    expect(screen.queryByRole('status')).toBeNull()
  })

  it('切到未覆盖的候选时展示后端给出的原因，而不是空白', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockImplementation(async (dish, index) =>
      index === UNCOVERED_INDEX
        ? {
            dish,
            source: 'unavailable',
            degraded: true,
            recipe: null,
            reason: '库内没有这道菜的菜谱，且生成服务未配置',
          }
        : LIBRARY,
    )
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await revealRecipe(user)
    await screen.findByRole('heading', { level: 3, name: '红烧肉' })

    await user.click(screen.getByRole('radio', { name: '梅菜扣肉 0.7%' }))
    await revealRecipe(user)

    expect(await screen.findByText('库内没有这道菜的菜谱，且生成服务未配置')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 3, name: '红烧肉' })).not.toBeInTheDocument()
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

  it('识别失败时就地提示，不清空已选图片', async () => {
    vi.mocked(recognizeImage).mockRejectedValue(new ApiError(50001, '无法连接后端服务，请确认服务已启动'))
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    expect(await screen.findByText('无法连接后端服务，请确认服务已启动')).toBeInTheDocument()
    expect(screen.getByAltText('已选择的成品菜照片')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始识别' })).toBeEnabled()
  })

  it('左栏不再内置演示图，选本地图片仍能跑通整条链路', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    // 示例图入口已撤下：既没有那几个按钮，也没有承载它们的容器
    expect(container.querySelector('.samples')).toBeNull()
    expect(screen.queryByRole('button', { name: /^示例图/ })).toBeNull()
    expect(screen.queryByRole('button', { name: '示例图 红烧肉' })).toBeNull()

    uploadPicture(container, picture())
    expect(await screen.findByText('已选择本地图片 · shot.png')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '开始识别' }))
    expect(await screen.findByRole('radio', { name: '红烧肉 88.5%' })).toBeInTheDocument()
  })

  it('重置后回到初始态，模型信息保留', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByRole('radio', { name: '红烧肉 88.5%' })

    await user.click(screen.getByRole('button', { name: '重置' }))

    expect(screen.getByText('识别后这里显示候选菜品与置信度')).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: '红烧肉 88.5%' })).not.toBeInTheDocument()
    expect(screen.getByText('模型: dish-convnext-base-208-onnx · 208 类 · 类别清单已交付')).toBeInTheDocument()
  })
})

describe('RecipeRecommendPage 手动纠正菜名（FR-10）', () => {
  const LABEL = '识别不准？手动输入菜名'
  const manualResolve = async (dish: string): Promise<ResolveResponse> =>
    dish === '麻婆豆腐'
      ? {
          dish,
          source: 'generated',
          degraded: false,
          recipe: generatedRecipe('麻婆豆腐'),
          reason: null,
        }
      : {
          dish,
          source: 'unavailable',
          degraded: true,
          recipe: null,
          reason: '库内没有这道菜的菜谱，且生成服务未配置',
        }

  it('没选图片时不出现手输入口', () => {
    vi.mocked(fetchHealth).mockReturnValue(new Promise(() => {}))

    render(<RecipeRecommendPage />)

    expect(screen.queryByLabelText(LABEL)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '用这个菜名' })).not.toBeInTheDocument()
  })

  it('候选都不对时按手输菜名取菜谱，并取消候选高亮', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockImplementation(manualResolve)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByRole('radio', { name: '红烧肉 88.5%' })
    await revealRecipe(user)
    expect(await screen.findByText('库内没有这道菜的菜谱，且生成服务未配置')).toBeInTheDocument()

    await user.type(screen.getByLabelText(LABEL), '麻婆豆腐')
    await user.click(screen.getByRole('button', { name: '用这个菜名' }))

    expect(await screen.findByRole('heading', { level: 3, name: '麻婆豆腐' })).toBeInTheDocument()
    // 手输没有类别下标，请求里只有菜名；结果来自大模型生成
    expect(resolveRecipe).toHaveBeenLastCalledWith('麻婆豆腐')
    expect(recipePanel().getByText('大模型生成')).toBeInTheDocument()
    expect(screen.getByText('当前按手输菜名解析：麻婆豆腐')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '红烧肉 88.5%' })).toHaveAttribute('aria-checked', 'false')
  })

  it('手输后再点候选会切回该候选的类别下标', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockImplementation(manualResolve)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByRole('radio', { name: '红烧肉 88.5%' })

    await user.type(screen.getByLabelText(LABEL), '麻婆豆腐')
    await user.click(screen.getByRole('button', { name: '用这个菜名' }))
    await screen.findByRole('heading', { level: 3, name: '麻婆豆腐' })

    await user.click(screen.getByRole('radio', { name: '梅菜扣肉 0.7%' }))

    await revealRecipe(user)
    await waitFor(() => expect(resolveRecipe).toHaveBeenLastCalledWith('梅菜扣肉', 83))
    expect(screen.queryByText('当前按手输菜名解析：麻婆豆腐')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '梅菜扣肉 0.7%' })).toHaveAttribute('aria-checked', 'true')
  })

  it('识别不出任何候选时仍可手输菜名查菜谱', async () => {
    vi.mocked(recognizeImage).mockResolvedValue({ ...RECOGNIZED, candidates: [] })
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    expect(await screen.findByText('识别后这里显示候选菜品与置信度')).toBeInTheDocument()

    await user.type(screen.getByLabelText(LABEL), '红烧肉')
    await user.click(screen.getByRole('button', { name: '用这个菜名' }))

    expect(await screen.findByRole('heading', { level: 3, name: '红烧肉' })).toBeInTheDocument()
    expect(resolveRecipe).toHaveBeenLastCalledWith('红烧肉')
  })
})

describe('RecipeRecommendPage 菜谱记忆（FR-15）', () => {
  it('取到菜谱后记一笔，点记忆条目直接复用、不再请求后端', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await revealRecipe(user)
    await screen.findByRole('heading', { level: 3, name: '红烧肉' })

    const item = await screen.findByRole('button', { name: '复用 红烧肉' })
    const calls = vi.mocked(resolveRecipe).mock.calls.length

    await user.click(item)

    expect(screen.getByText(/来自本机记忆/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: '红烧肉' })).toBeInTheDocument()
    expect(vi.mocked(resolveRecipe).mock.calls).toHaveLength(calls)
    expect(localStorage.getItem(MEMORY_STORAGE_KEY)).toContain('红烧肉')
  })

  it('识别到做过的菜时直接复用记忆，一个请求都不发', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '红烧肉', at: Date.now() })]),
    )
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    await revealRecipe(user)
    expect(await screen.findByRole('heading', { level: 3, name: '红烧肉' })).toBeInTheDocument()
    expect(resolveRecipe).not.toHaveBeenCalled()
    expect(screen.getByText(/来自本机记忆/)).toBeInTheDocument()
  })

  it('打开页面就能看到上次记下的菜', () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤' })]),
    )
    vi.mocked(fetchHealth).mockReturnValue(new Promise(() => {}))

    render(<RecipeRecommendPage />)

    expect(screen.getByRole('button', { name: '复用 番茄蛋花汤' })).toBeInTheDocument()
  })

  it('清空后记忆列表回到空态', async () => {
    localStorage.setItem(MEMORY_STORAGE_KEY, JSON.stringify([memoryEntry()]))
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(screen.getByRole('button', { name: '清空' }))

    expect(screen.queryByRole('button', { name: '复用 红烧肉' })).not.toBeInTheDocument()
    expect(screen.getByText(EMPTY_HINT_MEMORY)).toBeInTheDocument()
  })

  it('删掉一条后列表里不再出现，存储里也清了', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤' })]),
    )
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(screen.getByRole('button', { name: '删除 番茄蛋花汤' }))

    expect(screen.queryByRole('button', { name: '复用 番茄蛋花汤' })).not.toBeInTheDocument()
    expect(screen.getByText(EMPTY_HINT_MEMORY)).toBeInTheDocument()
    expect(localStorage.getItem(MEMORY_STORAGE_KEY)).not.toContain('番茄蛋花汤')
  })

  it('收藏后排到最前，标记写进本机存储', async () => {
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([
        memoryEntry({ dish: '番茄蛋花汤', name: '番茄蛋花汤', at: Date.now() }),
        memoryEntry({ dish: '红烧肉', at: Date.now() - 3600_000 }),
      ]),
    )
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(screen.getByRole('button', { name: '收藏 红烧肉' }))

    const items = screen.getAllByRole('button', { name: /^复用 / })
    expect(items[0]).toHaveAccessibleName('复用 红烧肉')
    expect(localStorage.getItem(MEMORY_STORAGE_KEY)).toContain('"favorite":true')
  })
})

describe('RecipeRecommendPage 等待提示（库内 / 生成分流）', () => {
  it('库内候选等待时说清在读库', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockReturnValue(new Promise(() => {}))
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    await revealRecipe(user)
    expect(await recipePanel().findByText('库里已有这道菜，正在读取菜谱…')).toBeInTheDocument()
  })

  it('切到库外候选时说清在等大模型现写', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockReturnValue(new Promise(() => {}))
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))
    await user.click(await screen.findByRole('radio', { name: '梅菜扣肉 0.7%' }))

    await revealRecipe(user)
    expect(await recipePanel().findByText(/正在让大模型现写/)).toBeInTheDocument()
  })

  it('覆盖清单没取到时退回通用提示，不猜分支', async () => {
    vi.mocked(fetchDishes).mockRejectedValue(new Error('清单取不到'))
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    vi.mocked(resolveRecipe).mockReturnValue(new Promise(() => {}))
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    await revealRecipe(user)
    expect(await recipePanel().findByText('正在查询菜谱…')).toBeInTheDocument()
  })
})

describe('RecipeRecommendPage 账号', () => {
  const ACCOUNT: AccountUser = {
    id: 7,
    email: 'hakura@example.com',
    display_name: 'hakura',
    created_at: '2026-10-10T00:00:00',
  }

  it('未登录时页头给登录入口，识别与取菜谱照旧能用', async () => {
    vi.mocked(recognizeImage).mockResolvedValue(RECOGNIZED)
    const user = userEvent.setup()
    const { container } = render(<RecipeRecommendPage />)

    expect(await screen.findByRole('button', { name: '登录 / 注册' })).toBeInTheDocument()

    uploadPicture(container, picture())
    await user.click(screen.getByRole('button', { name: '开始识别' }))

    expect(await screen.findByRole('radio', { name: '红烧肉 88.5%' })).toBeInTheDocument()
  })

  it('点登录打开弹窗', async () => {
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(await screen.findByRole('button', { name: '登录 / 注册' }))

    expect(screen.getByRole('dialog', { name: '登录' })).toBeInTheDocument()
  })

  it('登录成功后页头换成账号名，并把服务端那份记忆并进来', async () => {
    vi.mocked(loginAccount).mockResolvedValue({ user: ACCOUNT })
    vi.mocked(fetchMemories).mockResolvedValue({
      entries: [memoryEntry({ dish: '服务端的菜', name: '服务端的菜' })],
    })
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(await screen.findByRole('button', { name: '登录 / 注册' }))
    await user.type(screen.getByLabelText('邮箱'), 'hakura@example.com')
    await user.type(screen.getByLabelText('密码'), 'password123')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('hakura')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '复用 服务端的菜' })).toBeInTheDocument()
    expect(putMemories).toHaveBeenCalled()
  })

  it('刷新后仍认得出登录态，并自动同步记忆', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ user: ACCOUNT })
    render(<RecipeRecommendPage />)

    expect(await screen.findByText('hakura')).toBeInTheDocument()
    await waitFor(() => expect(fetchMemories).toHaveBeenCalled())
  })

  it('退出后回到登录入口，本机记忆留着', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ user: ACCOUNT })
    localStorage.setItem(
      MEMORY_STORAGE_KEY,
      JSON.stringify([memoryEntry({ dish: '本机的菜', name: '本机的菜' })]),
    )
    const user = userEvent.setup()
    render(<RecipeRecommendPage />)

    await user.click(await screen.findByRole('button', { name: '退出' }))

    expect(await screen.findByRole('button', { name: '登录 / 注册' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复用 本机的菜' })).toBeInTheDocument()
  })

  it('登录后本机记忆按账号分区：另一个账号看不到这个账号记的菜', async () => {
    localStorage.setItem(
      memoryStorageKey('7'),
      JSON.stringify([memoryEntry({ dish: '账号的菜', name: '账号的菜' })]),
    )
    vi.mocked(fetchMe).mockResolvedValue({ user: ACCOUNT })

    render(<RecipeRecommendPage />)

    expect(await screen.findByRole('button', { name: '复用 账号的菜' })).toBeInTheDocument()
    // 记在这个账号自己那块，不落到匿名区
    expect(localStorage.getItem(memoryStorageKey('7'))).toContain('账号的菜')
    expect(localStorage.getItem(MEMORY_STORAGE_KEY) ?? '').not.toContain('账号的菜')
  })
})

describe('RecipeRecommendPage 模型 Key', () => {
  it('页面上有填写入口', async () => {
    render(<RecipeRecommendPage />)

    expect(await screen.findByRole('heading', { name: '我的模型 Key' })).toBeInTheDocument()
  })

  it('填好的 Key 显示成掩码，不会原样露在页面上', async () => {
    localStorage.setItem(
      LLM_KEY_STORAGE_KEY,
      JSON.stringify({ apiKey: 'sk-user-key-1234567890', baseUrl: '', model: '' }),
    )
    render(<RecipeRecommendPage />)

    expect(await screen.findByText('sk-u••••7890')).toBeInTheDocument()
    expect(screen.queryByText('sk-user-key-1234567890')).toBeNull()
  })
})
