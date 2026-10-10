import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'

import { ALLOWED_MIME_TYPES, MAX_UPLOAD_BYTES, UPLOAD_HARD_LIMIT_BYTES } from '../constants'
import { needsCompression, prepareUploadFile } from '../image-compress'

type Props = {
  previewUrl: string | null
  busy: boolean
  errorMessage: string | null
  statusText: string | null
  onSelectFile: (file: File) => void
  onReject: (message: string) => void
}

/**
 * 校验类型与硬上限，返回可直接展示的中文提示（设计方案 4.3.1）。
 *
 * 这里只管「能不能处理」：5 MB 到 25 MB 之间的照片放行，交给上传前的压缩去救，
 * 压不下去的再让后端按 5 MB 上限退回。25 MB 以上连解码都可能把页面卡住，直接劝退。
 */
export function validateFile(file: File): string | null {
  if (file.type && !ALLOWED_MIME_TYPES.includes(file.type)) {
    return '不支持的图片格式，请上传 jpg / png / webp'
  }
  if (file.size > UPLOAD_HARD_LIMIT_BYTES) {
    return `图片超过 ${UPLOAD_HARD_LIMIT_BYTES / (1024 * 1024)} MB，请换一张更小的照片`
  }
  return null
}

export function UploadDropzone({
  previewUrl,
  busy,
  errorMessage,
  statusText,
  onSelectFile,
  onReject,
}: Props) {
  const [dragging, setDragging] = useState(false)
  const [compressNote, setCompressNote] = useState<string | null>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  // 相机与相册拆成两个 input：capture 一旦挂上，手机上就只剩拍照、翻不到已有的照片。
  // 两个入口各占一个，用户想现拍还是想选都行
  const cameraRef = useRef<HTMLInputElement>(null)
  // 压缩是异步的：用户连选两张时，只认最后一次的结果，避免迟到的提示串台
  const selectionRef = useRef(0)

  const accept = async (file: File | undefined) => {
    if (!file) {
      return
    }
    const problem = validateFile(file)
    if (problem) {
      setCompressNote(null)
      onReject(problem)
      return
    }
    const token = selectionRef.current + 1
    selectionRef.current = token
    if (!needsCompression(file)) {
      // 常见情况：相册里已经压过的小图。同步走完，不为了一个必然空转的 await 让界面闪一下
      setCompressNote(null)
      onSelectFile(file)
      return
    }
    const prepared = await prepareUploadFile(file)
    if (token !== selectionRef.current) {
      return
    }
    setCompressNote(prepared.note)
    onSelectFile(prepared.file)
  }

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    accept(event.target.files?.[0])
    event.target.value = ''
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (busy) {
      return
    }
    accept(event.dataTransfer.files?.[0])
  }

  const className = ['dropzone', dragging ? 'is-dragover' : '', busy ? 'is-busy' : '']
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={className}
      aria-busy={busy}
      onClick={(event) => {
        // 两个 input 是程序化点开的，它们的 click 会冒泡回这一层；
        // 不加这个判断就会「点拍照 → 又开一次相册」来回触发
        if (busy || event.target === galleryRef.current || event.target === cameraRef.current) {
          return
        }
        galleryRef.current?.click()
      }}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <input
        ref={galleryRef}
        type="file"
        accept={ALLOWED_MIME_TYPES.join(',')}
        hidden
        onChange={handleChange}
      />
      <input
        ref={cameraRef}
        type="file"
        accept={ALLOWED_MIME_TYPES.join(',')}
        capture="environment"
        hidden
        onChange={handleChange}
      />
      {previewUrl ? (
        <img className="preview" src={previewUrl} alt="已选择的成品菜照片" />
      ) : (
        <>
          <p className={errorMessage ? 'dropzone-text is-error' : 'dropzone-text'}>
            {errorMessage ? (
              errorMessage
            ) : (
              <>
                <strong>拍一张</strong> 或拖拽成品菜照片
                <br />
                <span className="muted-inline">
                  jpg / png / webp · 超过 {MAX_UPLOAD_BYTES / (1024 * 1024)} MB 自动压缩
                </span>
              </>
            )}
          </p>
          {errorMessage ? null : (
            <div className="shutter">
              <button
                type="button"
                className="shutter-btn"
                disabled={busy}
                onClick={(event) => {
                  // 两个按钮都在拖拽区里：点它们不能让外面那层再开一次相册
                  event.stopPropagation()
                  cameraRef.current?.click()
                }}
              >
                拍照
              </button>
              <button
                type="button"
                className="shutter-btn shutter-btn--ghost"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation()
                  galleryRef.current?.click()
                }}
              >
                从相册选
              </button>
            </div>
          )}
        </>
      )}
      {previewUrl && errorMessage ? <p className="dropzone-text is-error">{errorMessage}</p> : null}
      {previewUrl && (statusText || compressNote) ? (
        <p className="muted-inline">{[statusText, compressNote].filter(Boolean).join(' · ')}</p>
      ) : null}
    </div>
  )
}
