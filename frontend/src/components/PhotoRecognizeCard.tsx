import { useState } from 'react'

import type { DemoSampleOption } from '../demo'
import type { RecognizeStatus } from '../hooks/useRecipePage'
import { PanelHeader } from './PanelHeader'
import { UploadDropzone } from './UploadDropzone'

type Props = {
  previewUrl: string | null
  status: RecognizeStatus
  error: string | null
  caption: string
  badge: string
  statusText: string | null
  onSelectFile: (file: File) => void
  onRecognize: () => void
  onReset: () => void
  /** 静态演示站才有值：点示例图直接看结果，不用自己找照片 */
  samples?: DemoSampleOption[] | null
  activeSampleId?: string | null
  /** 这张图想演示什么；自己上传的照片会说明候选来自预置快照 */
  notice?: string | null
  onSelectSample?: (id: string) => void
}

export function PhotoRecognizeCard({
  previewUrl,
  status,
  error,
  caption,
  badge,
  statusText,
  onSelectFile,
  onRecognize,
  onReset,
  samples = null,
  activeSampleId = null,
  notice = null,
  onSelectSample,
}: Props) {
  const [localError, setLocalError] = useState<string | null>(null)
  const busy = status === 'recognizing'
  const message = error ?? localError

  const handleSelectFile = (file: File) => {
    setLocalError(null)
    onSelectFile(file)
  }

  const handleReset = () => {
    setLocalError(null)
    onReset()
  }

  return (
    <section className="card">
      <PanelHeader title="拍照识别" trailing={<span className="badge">{badge}</span>} />
      <UploadDropzone
        previewUrl={previewUrl}
        busy={busy}
        errorMessage={message}
        statusText={statusText}
        onSelectFile={handleSelectFile}
        onReject={setLocalError}
      />
      {samples && samples.length > 0 ? (
        <div className="samples">
          <p className="samples-label">演示图：点一张直接看结果</p>
          <div className="samples-row">
            {samples.map((sample) => (
              <button
                key={sample.id}
                type="button"
                title={sample.note}
                className={sample.id === activeSampleId ? 'sample is-active' : 'sample'}
                onClick={() => onSelectSample?.(sample.id)}
              >
                {sample.caption}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || !previewUrl}
          onClick={onRecognize}
        >
          {busy ? '识别中…' : '开始识别'}
        </button>
        <button type="button" className="btn btn--secondary" onClick={handleReset}>
          重置
        </button>
      </div>
      {notice ? <p className="notice">{notice}</p> : null}
      <p className="caption">{caption}</p>
    </section>
  )
}
