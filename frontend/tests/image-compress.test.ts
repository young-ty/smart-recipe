import { afterEach, describe, expect, it, vi } from 'vitest'

import { COMPRESS_THRESHOLD_BYTES, MAX_UPLOAD_BYTES } from '../src/constants'
import {
  browserDeps,
  compressImage,
  fitWithin,
  formatBytes,
  needsCompression,
  prepareUploadFile,
  type CompressDeps,
} from '../src/image-compress'

/** 替掉 Image：jsdom 不会真的去加载 blob，加载时机由测试控制。 */
class FakeImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 1200
  naturalHeight = 900
  private current = ''

  get src() {
    return this.current
  }

  set src(next: string) {
    this.current = next
    queueMicrotask(() => this.onload?.())
  }
}

/** 加载失败的 Image：只触发 onerror。 */
class BrokenImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 0
  naturalHeight = 0

  set src(_next: string) {
    queueMicrotask(() => this.onerror?.())
  }
}

type FakeOptions = {
  width?: number
  height?: number
  /** 每次编码产出的字节数，按调用顺序取，用尽后重复最后一个 */
  outputs?: number[]
  decodeError?: boolean
  encodeError?: boolean
}

/** 用替身驱动解码与编码：jsdom 里没有真的 canvas，压缩逻辑照样能验。 */
function fakeDeps(options: FakeOptions = {}) {
  const { width = 4000, height = 3000, outputs = [400 * 1024] } = options
  const release = vi.fn()
  const decode = vi.fn(async () => {
    if (options.decodeError) {
      throw new Error('解码失败')
    }
    return { source: {} as CanvasImageSource, width, height, release }
  })
  const encode = vi.fn(async () => {
    if (options.encodeError) {
      throw new Error('编码失败')
    }
    const index = Math.min(encode.mock.calls.length - 1, outputs.length - 1)
    return new Blob([new Uint8Array(outputs[index])], { type: 'image/jpeg' })
  })
  return { decode, encode, release }
}

function photo(bytes: number, name = 'IMG_2043.jpg') {
  return new File([new Uint8Array(bytes)], name, { type: 'image/jpeg' })
}

const MEGABYTE = 1024 * 1024

describe('fitWithin', () => {
  it('长边已经够小就原样返回', () => {
    expect(fitWithin(800, 600, 1600)).toEqual({ width: 800, height: 600 })
  })

  it('长边正好等于上限时不缩放', () => {
    expect(fitWithin(1600, 900, 1600)).toEqual({ width: 1600, height: 900 })
  })

  it('横图按长边等比缩小', () => {
    expect(fitWithin(4032, 3024, 1600)).toEqual({ width: 1600, height: 1200 })
  })

  it('竖图按长边等比缩小', () => {
    expect(fitWithin(3024, 4032, 1600)).toEqual({ width: 1200, height: 1600 })
  })

  it('极端宽高比也至少留 1 像素，不缩成 0', () => {
    expect(fitWithin(4000, 2, 1600)).toEqual({ width: 1600, height: 1 })
  })

  it('尺寸无效时返回 0，交给调用方放弃压缩', () => {
    expect(fitWithin(0, 0, 1600)).toEqual({ width: 0, height: 0 })
    expect(fitWithin(Number.NaN, 100, 1600)).toEqual({ width: 0, height: 0 })
  })
})

describe('formatBytes', () => {
  it('1 MB 以上按 MB 保留一位小数', () => {
    expect(formatBytes(6 * MEGABYTE)).toBe('6.0 MB')
    expect(formatBytes(1.5 * MEGABYTE)).toBe('1.5 MB')
  })

  it('不足 1 MB 按 KB 取整，也不显示成 0 KB', () => {
    expect(formatBytes(400 * 1024)).toBe('400 KB')
    expect(formatBytes(100)).toBe('1 KB')
  })
})

describe('compressImage', () => {
  it('按第一轮参数缩放编码，文件名沿用用户的', async () => {
    const deps = fakeDeps({ outputs: [400 * 1024] })
    const result = await compressImage(photo(6 * MEGABYTE), deps)

    expect(result?.name).toBe('IMG_2043.jpg')
    expect(result?.type).toBe('image/jpeg')
    expect(deps.encode).toHaveBeenCalledTimes(1)
    expect(deps.encode.mock.calls[0].slice(1)).toEqual([1600, 1200, 0.82])
  })

  it('第一轮就压到 5 MB 以内时不再压第二轮', async () => {
    const deps = fakeDeps({ outputs: [400 * 1024, 100 * 1024] })
    await compressImage(photo(6 * MEGABYTE), deps)

    expect(deps.encode).toHaveBeenCalledTimes(1)
  })

  it('第一轮仍超 5 MB 时退到更狠的第二轮', async () => {
    const deps = fakeDeps({ outputs: [7 * MEGABYTE, 400 * 1024] })
    const result = await compressImage(photo(30 * MEGABYTE), deps)

    expect(deps.encode).toHaveBeenCalledTimes(2)
    expect(deps.encode.mock.calls[1].slice(1)).toEqual([1080, 810, 0.7])
    expect(result?.size).toBe(400 * 1024)
    expect(result?.size).toBeLessThanOrEqual(MAX_UPLOAD_BYTES)
  })

  it('压完反而更大时用原图，不做负功', async () => {
    const deps = fakeDeps({ outputs: [3 * MEGABYTE] })
    expect(await compressImage(photo(2 * MEGABYTE), deps)).toBeNull()
  })

  it('两轮都压不进 5 MB 时给出更小的那个，让后端去判定', async () => {
    const deps = fakeDeps({ outputs: [7 * MEGABYTE, 6 * MEGABYTE] })
    const result = await compressImage(photo(30 * MEGABYTE), deps)

    expect(deps.encode).toHaveBeenCalledTimes(2)
    expect(result?.size).toBe(6 * MEGABYTE)
    expect(result?.size).toBeGreaterThan(MAX_UPLOAD_BYTES)
  })

  it('解码失败返回 null，连编码都不试', async () => {
    const deps = fakeDeps({ decodeError: true })
    expect(await compressImage(photo(6 * MEGABYTE), deps)).toBeNull()
    expect(deps.encode).not.toHaveBeenCalled()
  })

  it('第一轮编码失败返回 null，第二轮失败则保留第一轮结果', async () => {
    const failed = fakeDeps({ encodeError: true })
    expect(await compressImage(photo(6 * MEGABYTE), failed)).toBeNull()

    const encode = vi.fn()
    encode.mockResolvedValueOnce(new Blob([new Uint8Array(7 * MEGABYTE)]))
    encode.mockRejectedValueOnce(new Error('编码失败'))
    const deps: CompressDeps = {
      decode: vi.fn(async () => ({ source: {} as CanvasImageSource, width: 4000, height: 3000 })),
      encode,
    }
    const result = await compressImage(photo(30 * MEGABYTE), deps)

    expect(encode).toHaveBeenCalledTimes(2)
    expect(result?.size).toBe(7 * MEGABYTE)
  })

  it('尺寸解不出来时放弃压缩', async () => {
    const deps = fakeDeps({ width: 0, height: 0 })
    expect(await compressImage(photo(6 * MEGABYTE), deps)).toBeNull()
    expect(deps.encode).not.toHaveBeenCalled()
  })

  it('无论成败都放掉解码出来的位图', async () => {
    const ok = fakeDeps({ outputs: [400 * 1024] })
    await compressImage(photo(6 * MEGABYTE), ok)
    expect(ok.release).toHaveBeenCalledTimes(1)

    const bad = fakeDeps({ encodeError: true })
    await compressImage(photo(6 * MEGABYTE), bad)
    expect(bad.release).toHaveBeenCalledTimes(1)
  })
})

describe('prepareUploadFile', () => {
  it('只有超过阈值才算需要压', () => {
    expect(needsCompression(photo(1024))).toBe(false)
    expect(needsCompression(photo(COMPRESS_THRESHOLD_BYTES))).toBe(false)
    expect(needsCompression(photo(COMPRESS_THRESHOLD_BYTES + 1))).toBe(true)
  })

  it('小图原样返回，连解码都不做', async () => {
    const deps = fakeDeps()
    const file = photo(COMPRESS_THRESHOLD_BYTES)

    const prepared = await prepareUploadFile(file, deps)

    expect(prepared.file).toBe(file)
    expect(prepared.note).toBeNull()
    expect(deps.decode).not.toHaveBeenCalled()
  })

  it('大图压完给出体积对比的提示', async () => {
    const deps = fakeDeps({ outputs: [400 * 1024] })
    const prepared = await prepareUploadFile(photo(6 * MEGABYTE), deps)

    expect(prepared.file.size).toBe(400 * 1024)
    expect(prepared.note).toBe('已自动压缩 6.0 MB → 400 KB')
  })

  it('压不出来时退回原图，也不给提示', async () => {
    const deps = fakeDeps({ decodeError: true })
    const file = photo(6 * MEGABYTE)

    const prepared = await prepareUploadFile(file, deps)

    expect(prepared.file).toBe(file)
    expect(prepared.note).toBeNull()
  })
})

describe('browserDeps', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('优先用 createImageBitmap，并带上是「按 EXIF 转正」的选项', async () => {
    const close = vi.fn()
    const createImageBitmap = vi.fn(async () => ({ width: 3024, height: 4032, close }))
    vi.stubGlobal('createImageBitmap', createImageBitmap)

    const decoded = await browserDeps.decode(photo(1024))

    expect(createImageBitmap).toHaveBeenCalledWith(expect.any(File), {
      imageOrientation: 'from-image',
    })
    expect([decoded.width, decoded.height]).toEqual([3024, 4032])
    decoded.release?.()
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('没有 createImageBitmap 时退回 img 标签，加载完释放对象地址', async () => {
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:1')
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.stubGlobal('createImageBitmap', undefined)
    vi.stubGlobal('Image', FakeImage)

    const decoded = await browserDeps.decode(photo(1024))

    expect([decoded.width, decoded.height]).toEqual([1200, 900])
    expect(revoke).toHaveBeenCalledWith('blob:1')
  })

  it('img 加载失败时抛错，让上层退回原图', async () => {
    vi.spyOn(URL, 'revokeObjectURL')
    vi.stubGlobal('createImageBitmap', undefined)
    vi.stubGlobal('Image', BrokenImage)

    await expect(browserDeps.decode(photo(1024))).rejects.toThrow('图片解码失败')
  })

  it('画布拿不到 2d 上下文时明确报错', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)

    await expect(browserDeps.encode({} as CanvasImageSource, 100, 100, 0.8)).rejects.toThrow(
      '当前浏览器不支持画布压缩',
    )
  })

  it('按目标尺寸画到画布再编码成 JPEG', async () => {
    const context = { imageSmoothingQuality: 'low', drawImage: vi.fn() }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as never)
    const toBlob = vi
      .spyOn(HTMLCanvasElement.prototype, 'toBlob')
      .mockImplementation((callback: BlobCallback) => {
        callback(new Blob(['x'], { type: 'image/jpeg' }))
      })

    const blob = await browserDeps.encode({} as CanvasImageSource, 1600, 1200, 0.82)

    expect(blob.type).toBe('image/jpeg')
    expect(context.imageSmoothingQuality).toBe('high')
    expect(context.drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 1600, 1200)
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), 'image/jpeg', 0.82)
  })

  it('编码器给回空 blob 时当成失败', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      imageSmoothingQuality: 'low',
      drawImage: vi.fn(),
    } as never)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(
      (callback: BlobCallback) => {
        callback(null)
      },
    )

    await expect(browserDeps.encode({} as CanvasImageSource, 10, 10, 0.5)).rejects.toThrow(
      '图片编码失败',
    )
  })

  it('默认依赖就是浏览器实现：整条链路能出一个压好的 File', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 4032, height: 3024, close: vi.fn() })))
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      imageSmoothingQuality: 'low',
      drawImage: vi.fn(),
    } as never)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(
      (callback: BlobCallback) => {
        callback(new Blob([new Uint8Array(400 * 1024)], { type: 'image/jpeg' }))
      },
    )

    const prepared = await prepareUploadFile(photo(6 * MEGABYTE))

    expect(prepared.file.type).toBe('image/jpeg')
    expect(prepared.note).toBe('已自动压缩 6.0 MB → 400 KB')
  })
})
