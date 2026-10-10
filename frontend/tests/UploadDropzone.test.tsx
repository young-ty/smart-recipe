import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ComponentProps } from 'react'

vi.mock('../src/image-compress', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/image-compress')>()
  return { ...actual, prepareUploadFile: vi.fn() }
})

import { MAX_UPLOAD_BYTES, UPLOAD_HARD_LIMIT_BYTES } from '../src/constants'
import { UploadDropzone, validateFile } from '../src/components/UploadDropzone'
import { prepareUploadFile, type PreparedUpload } from '../src/image-compress'

type ZoneProps = ComponentProps<typeof UploadDropzone>

const MEGABYTE = 1024 * 1024

/** 只改 size 的替身：真造一个 25 MB 的 File 太浪费，而这里只关心大小判定。 */
function fakeSize(size: number, type = 'image/jpeg', name = 'IMG_2043.jpg') {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function photo(bytes: number, name = 'IMG_2043.jpg') {
  return new File([new Uint8Array(bytes)], name, { type: 'image/jpeg' })
}

beforeEach(() => {
  vi.mocked(prepareUploadFile).mockReset()
  vi.mocked(prepareUploadFile).mockImplementation(async (file) => ({ file, note: null }))
})

function setup(initial: Partial<ZoneProps> = {}) {
  const onSelectFile = vi.fn()
  const onReject = vi.fn()
  const props: ZoneProps = {
    previewUrl: null,
    busy: false,
    errorMessage: null,
    statusText: null,
    onSelectFile,
    onReject,
    ...initial,
  }
  const view = render(<UploadDropzone {...props} />)
  const inputs = Array.from(
    view.container.querySelectorAll('input[type="file"]'),
  ) as HTMLInputElement[]
  return {
    onSelectFile,
    onReject,
    inputs,
    input: inputs[0],
    update: (next: Partial<ZoneProps>) => view.rerender(<UploadDropzone {...props} {...next} />),
  }
}

describe('validateFile', () => {
  it('格式不在白名单里就直接退回', () => {
    expect(validateFile(fakeSize(1024, 'image/heic'))).toContain('jpg / png / webp')
  })

  it('5 MB 以上仍然放行，交给上传前的压缩去救', () => {
    expect(validateFile(fakeSize(MAX_UPLOAD_BYTES + 1))).toBeNull()
  })

  it('超过硬上限才拒绝，并说明是多大', () => {
    expect(validateFile(fakeSize(UPLOAD_HARD_LIMIT_BYTES))).toBeNull()
    expect(validateFile(fakeSize(UPLOAD_HARD_LIMIT_BYTES + 1))).toContain('25 MB')
  })
})

describe('UploadDropzone', () => {
  it('拍照与相册是两个入口，capture 只挂在相机那个 input 上', () => {
    const { inputs } = setup()

    expect(inputs).toHaveLength(2)
    // 相册入口不能带 capture，否则手机上只剩拍照、翻不到已有照片
    expect(inputs[0].getAttribute('capture')).toBeNull()
    expect(inputs[1].getAttribute('capture')).toBe('environment')
  })

  it('点「拍照」开相机，点「从相册选」开相册，互不串台', async () => {
    const user = userEvent.setup()
    const { inputs } = setup()
    const [gallery, camera] = inputs
    const galleryClick = vi.spyOn(gallery, 'click')
    const cameraClick = vi.spyOn(camera, 'click')

    await user.click(screen.getByRole('button', { name: '拍照' }))
    expect(cameraClick).toHaveBeenCalledTimes(1)
    // 外层整块也能开相册，点里面的按钮不能让它再开一次
    expect(galleryClick).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '从相册选' }))
    expect(galleryClick).toHaveBeenCalledTimes(1)
    expect(cameraClick).toHaveBeenCalledTimes(1)
  })

  it('选中的大图先压缩再交给上层，提示写在状态行里', async () => {
    const compressed = photo(400 * 1024)
    vi.mocked(prepareUploadFile).mockResolvedValue({
      file: compressed,
      note: '已自动压缩 6.0 MB → 400 KB',
    })
    const { input, onSelectFile, update } = setup()

    fireEvent.change(input, { target: { files: [fakeSize(6 * MEGABYTE)] } })

    await waitFor(() => expect(onSelectFile).toHaveBeenCalledTimes(1))
    expect(onSelectFile.mock.calls[0][0]).toBe(compressed)

    update({ previewUrl: 'blob:1', statusText: '已选择本地图片 · IMG_2043.jpg' })
    expect(
      screen.getByText('已选择本地图片 · IMG_2043.jpg · 已自动压缩 6.0 MB → 400 KB'),
    ).toBeInTheDocument()
  })

  it('够小的图同步放行，连压缩都不试，状态行只有文件名', async () => {
    const { input, onSelectFile, update } = setup()

    await act(async () => {
      fireEvent.change(input, { target: { files: [photo(1024)] } })
    })

    expect(onSelectFile).toHaveBeenCalledTimes(1)
    expect(prepareUploadFile).not.toHaveBeenCalled()
    update({ previewUrl: 'blob:1', statusText: '已选择本地图片 · IMG_2043.jpg' })
    expect(screen.getByText('已选择本地图片 · IMG_2043.jpg')).toBeInTheDocument()
  })

  it('格式不对直接退回，不进入压缩', async () => {
    const { input, onReject } = setup()

    fireEvent.change(input, { target: { files: [fakeSize(1024, 'image/heic', 'a.heic')] } })

    await waitFor(() => expect(onReject).toHaveBeenCalledTimes(1))
    expect(onReject.mock.calls[0][0]).toContain('jpg / png / webp')
    expect(prepareUploadFile).not.toHaveBeenCalled()
  })

  it('超过硬上限时退回，也不进入压缩', async () => {
    const { input, onReject } = setup()

    fireEvent.change(input, { target: { files: [fakeSize(UPLOAD_HARD_LIMIT_BYTES + 1)] } })

    await waitFor(() => expect(onReject).toHaveBeenCalledTimes(1))
    expect(prepareUploadFile).not.toHaveBeenCalled()
  })

  it('连选两张时只认最后一张的结果，迟到的提示不串台', async () => {
    let releaseFirst: (value: PreparedUpload) => void = () => undefined
    const firstPending = new Promise<PreparedUpload>((resolve) => {
      releaseFirst = resolve
    })
    const second = photo(300 * 1024, 'second.jpg')
    vi.mocked(prepareUploadFile)
      .mockImplementationOnce(() => firstPending)
      .mockImplementationOnce(async () => ({ file: second, note: '已自动压缩 8.0 MB → 300 KB' }))
    const { input, onSelectFile, update } = setup()

    await act(async () => {
      fireEvent.change(input, { target: { files: [fakeSize(8 * MEGABYTE, 'image/jpeg', 'a.jpg')] } })
      fireEvent.change(input, { target: { files: [fakeSize(8 * MEGABYTE, 'image/jpeg', 'b.jpg')] } })
    })
    expect(onSelectFile).toHaveBeenCalledTimes(1)
    expect(onSelectFile.mock.calls[0][0]).toBe(second)

    await act(async () => {
      releaseFirst({ file: photo(3000, 'a.jpg'), note: '已自动压缩 8.0 MB → 400 KB' })
      await firstPending
    })
    expect(onSelectFile).toHaveBeenCalledTimes(1)

    update({ previewUrl: 'blob:1', statusText: '已选择本地图片 · second.jpg' })
    expect(screen.getByText(/300 KB/)).toBeInTheDocument()
    expect(screen.queryByText(/400 KB/)).toBeNull()
  })
})
