import { useState } from 'react'

import { ApiError, probeLlmCredentials } from '../api/client'
import {
  EMPTY_LLM_KEY,
  clearLlmKey,
  maskApiKey,
  normalizeLlmKey,
  readLlmKey,
  validateLlmKey,
  writeLlmKey,
} from '../llm-key'
import type { LlmKeySettings } from '../llm-key'
import { PanelHeader } from './PanelHeader'

type Props = {
  /** 静态演示站不联网：卡照旧能填、能存在本机，只是这里生成不了菜谱 */
  online: boolean
}

type ProbeState = { ok: boolean; detail: string } | null

/**
 * 用户自己的大模型 Key（FR-16）。
 *
 * 填了就随请求头带过去，库外菜用用户自己的额度；没填就回落服务端配置的那份。
 * 「测试连接」真的发一次最小调用，把 Key、地址、模型名哪一处填错了当场说清——
 * 否则要等到某道库外菜生成失败才发现，而那时界面只会说「生成服务不可用」。
 */
export function LlmKeyCard({ online }: Props) {
  const [saved, setSaved] = useState<LlmKeySettings | null>(() => readLlmKey())
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<LlmKeySettings>(EMPTY_LLM_KEY)
  const [error, setError] = useState<string | null>(null)
  const [probe, setProbe] = useState<ProbeState>(null)
  const [probing, setProbing] = useState(false)

  const startEditing = () => {
    setDraft(saved ?? EMPTY_LLM_KEY)
    setError(null)
    setEditing(true)
  }

  const save = () => {
    const problem = validateLlmKey(draft)
    if (problem !== null) {
      setError(problem)
      return
    }
    writeLlmKey(draft)
    setSaved(normalizeLlmKey(draft))
    setEditing(false)
    setError(null)
    setProbe(null)
  }

  const remove = () => {
    clearLlmKey()
    setSaved(null)
    setDraft(EMPTY_LLM_KEY)
    setEditing(false)
    setError(null)
    setProbe(null)
  }

  const runProbe = async () => {
    setProbing(true)
    setProbe(null)
    try {
      const body = await probeLlmCredentials()
      setProbe({ ok: body.ok, detail: body.detail })
    } catch (failure) {
      setProbe({
        ok: false,
        detail: failure instanceof ApiError ? failure.message : '测试连接失败',
      })
    } finally {
      setProbing(false)
    }
  }

  return (
    <section className="card key-card">
      <PanelHeader
        title="我的模型 Key"
        trailing={
          editing ? null : (
            <button type="button" className="link-btn" onClick={startEditing}>
              {saved === null ? '填写' : '修改'}
            </button>
          )
        }
      />
      {!online && (
        <p className="key-notice">
          静态演示站不联网：这里能填、也存在这台设备上，但要真生成菜谱得在本地跑完整版。
        </p>
      )}
      {editing ? (
        <form
          className="form"
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="llm-api-key">
              模型 Key
            </label>
            <input
              id="llm-api-key"
              className="field-input"
              type="password"
              autoComplete="off"
              value={draft.apiKey}
              onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="llm-base-url">
              服务地址（可留空）
            </label>
            <input
              id="llm-base-url"
              className="field-input"
              type="text"
              placeholder="https://api.deepseek.com/v1"
              value={draft.baseUrl}
              onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="llm-model">
              模型名（可留空）
            </label>
            <input
              id="llm-model"
              className="field-input"
              type="text"
              placeholder="deepseek-chat"
              value={draft.model}
              onChange={(event) => setDraft({ ...draft, model: event.target.value })}
            />
          </div>
          {error !== null && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary">
              保存
            </button>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => {
                setEditing(false)
                setError(null)
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : saved === null ? (
        <p className="key-empty">
          不填也能用：库外菜走服务端配好的额度。填上就用你自己的，Key 只存在这台设备上。
        </p>
      ) : (
        <div className="key-saved">
          <p className="key-line">
            <span className="key-label">Key</span>
            <code>{maskApiKey(saved.apiKey)}</code>
          </p>
          {saved.baseUrl && (
            <p className="key-line">
              <span className="key-label">地址</span>
              <code>{saved.baseUrl}</code>
            </p>
          )}
          {saved.model && (
            <p className="key-line">
              <span className="key-label">模型</span>
              <code>{saved.model}</code>
            </p>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="btn btn--secondary"
              disabled={!online || probing}
              onClick={() => {
                void runProbe()
              }}
            >
              {probing ? '测试中…' : '测试连接'}
            </button>
            <button type="button" className="link-btn" onClick={remove}>
              清除
            </button>
          </div>
          {probe !== null && (
            <p
              className={probe.ok ? 'key-probe key-probe--ok' : 'key-probe key-probe--failed'}
              role="status"
            >
              {probe.detail}
            </p>
          )}
        </div>
      )}
      <p className="key-caption">
        Key 只存在这台设备上，随请求头发给后端用完即弃，不进数据库、也不写日志。
      </p>
    </section>
  )
}
