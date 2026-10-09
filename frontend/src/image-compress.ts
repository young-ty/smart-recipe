// 上传前在浏览器里把大图压小（设计方案 4.3.1）
//
// 手机实拍一张就是 3 到 8 MB，原样发给后端会吃 413（后端上限 5 MB）。而模型输入只有
// 288×288，传原图纯属浪费带宽和等待时间：长边压到 1600 再按 0.82 编成 JPEG，一张
// 8 MB 的照片通常落到 300 到 600 KB，对分类结果没有任何影响。
//
// 两条底线：压缩失败一律退回原图（这一步是优化，不是必经环节，不能让用户卡在选图），
// 压完反而更大也退回原图（已经压过的小图不值得再编码一次）。

import {
  COMPRESS_MAX_EDGE,
  COMPRESS_QUALITY,
  COMPRESS_THRESHOLD_BYTES,
  MAX_UPLOAD_BYTES,
} from './constants'

/** 解码结果。release 用来放掉 ImageBitmap，没有就省略。 */
export type Decoded = {
  source: CanvasImageSource
  width: number
  height: number
  release?: () => void
}

/** 解码与编码都做成可替换的依赖：jsdom 里没有真的 canvas，测试靠替身驱动。 */
export type CompressDeps = {
  decode: (file: File) => Promise<Decoded>
  encode: (
    source: CanvasImageSource,
    width: number,
    height: number,
    quality: number,
  ) => Promise<Blob>
}

/** 一轮压缩参数。第一轮保住细节，压不到 5 MB 以内再退到第二轮。 */
export type Pass = {
  maxEdge: number
  quality: number
}

export const PASSES: Pass[] = [
  { maxEdge: COMPRESS_MAX_EDGE, quality: COMPRESS_QUALITY },
  { maxEdge: 1080, quality: 0.7 },
]

/** 等比缩放到长边不超过 maxEdge；本来就够小就原样返回。 */
export function fitWithin(width: number, height: number, maxEdge: number) {
  const longest = Math.max(width, height)
  if (!Number.isFinite(longest) || longest <= 0) {
    return { width: 0, height: 0 }
  }
  if (longest <= maxEdge) {
    return { width, height }
  }
  const ratio = maxEdge / longest
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio)),
  }
}

/** 人类可读的体积，用于界面上的「已自动压缩」提示。 */
export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

async function decodeWithBitmap(file: File): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    // imageOrientation 让竖拍照片按 EXIF 转正，否则预览和送进模型的方向都是错的
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
    return {
      source: bitmap,
      width: bitmap.width,
      height: bitmap.height,
      release: () => bitmap.close(),
    }
  }
  return decodeWithImageElement(file)
}

function decodeWithImageElement(file: File): Promise<Decoded> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => {
      URL.revokeObjectURL(url)
      resolve({ source: image, width: image.naturalWidth, height: image.naturalHeight })
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片解码失败'))
    }
    image.src = url
  })
}

function encodeWithCanvas(
  source: CanvasImageSource,
  width: number,
  height: number,
  quality: number,
): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (context === null) {
    return Promise.reject(new Error('当前浏览器不支持画布压缩'))
  }
  context.imageSmoothingQuality = 'high'
  context.drawImage(source, 0, 0, width, height)
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob === null) {
          reject(new Error('图片编码失败'))
          return
        }
        resolve(blob)
      },
      'image/jpeg',
      quality,
    )
  })
}

export const browserDeps: CompressDeps = { decode: decodeWithBitmap, encode: encodeWithCanvas }

/**
 * 压到后端能收的范围内。返回 null 表示「压不出来，用原图」：
 * 解码失败、编码失败、或者压完并没有变小。
 */
export async function compressImage(
  file: File,
  deps: CompressDeps = browserDeps,
  passes: Pass[] = PASSES,
): Promise<File | null> {
  let decoded: Decoded
  try {
    decoded = await deps.decode(file)
  } catch {
    return null
  }
  try {
    let best: File | null = null
    for (const pass of passes) {
      const size = fitWithin(decoded.width, decoded.height, pass.maxEdge)
      if (size.width === 0 || size.height === 0) {
        return best
      }
      let blob: Blob
      try {
        blob = await deps.encode(decoded.source, size.width, size.height, pass.quality)
      } catch {
        return best
      }
      // 名字沿用用户的文件名：它只是界面上的标签，后端按魔数判断真实格式
      const candidate = new File([blob], file.name, {
        type: 'image/jpeg',
        lastModified: file.lastModified,
      })
      if (candidate.size >= (best ?? file).size) {
        return best
      }
      best = candidate
      if (candidate.size <= MAX_UPLOAD_BYTES) {
        return candidate
      }
    }
    return best
  } finally {
    decoded.release?.()
  }
}

export type PreparedUpload = {
  file: File
  /** 压缩说明，没压就是 null。只用于界面提示，不参与业务判断 */
  note: string | null
}

/** 值不值得压。够小的图直接同步上传，别为了一次无谓的 await 让界面闪一下。 */
export function needsCompression(file: File): boolean {
  return file.size > COMPRESS_THRESHOLD_BYTES
}

/** 上传前的统一准备：够小就原样，够大就先压一遍再说。 */
export async function prepareUploadFile(
  file: File,
  deps: CompressDeps = browserDeps,
): Promise<PreparedUpload> {
  if (!needsCompression(file)) {
    return { file, note: null }
  }
  const compressed = await compressImage(file, deps)
  if (compressed === null) {
    return { file, note: null }
  }
  return {
    file: compressed,
    note: `已自动压缩 ${formatBytes(file.size)} → ${formatBytes(compressed.size)}`,
  }
}
