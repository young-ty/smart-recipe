import { useState } from 'react'

import { ApiError, probeLlmCredentials } from '../api/client'
import {
  EMPTY_LLM_KEY,
  validateLlmKey,
} from '../llm-key'
import type { LlmKeySettings } from '../llm-key'
import { useLlmKey } from '../hooks/useLlmKey'
import type { AccountUser } from '../types'
import { PanelHeader } from './PanelHeader'

type Props = {
  /** 静态演示站不联网：卡照旧能填、能存在本机，只是这里生成不了菜谱 */
  online: boolean
  user: AccountUser | null
}

type ProbeState = { ok: boolean; detail: string } | null

/**
 * 用户自己的大模型 Key（FR-16、FR-17）。
 *
 * 服务端不预置任何 Key：填了才有库外菜，没填就是「识别 + 库内菜谱」。
 * 未登录时 Key 存在这台设备上、随请求头带过去；登录后存进账号（加密落库，只回掩码）。
 * 「测试连接」真的发一次最小调用，把 Key、地址、模型名哪一处填错了当场说清——
 * 否则要等到某道库外菜生成失败才发现，而那时界面只会说「生成服务不可用」。
 */
export function LlmKeyCard({ online, user }: Props) {
  const key = useLlmKey(user, online)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<LlmKeySettings>(EMPTY_LLM_KEY)
  const [error, setError] = useState<string | null>(null)
  const [probe, setProbe] = useState<ProbeState>(null)
  const [probing, setProbing] = useState(false)

  const startEditing = () => {
    setDraft(
      key.view.configured
        ? { apiKey: '', baseUrl: key.view.baseUrl, model: key.view.model }
        : EMPTY_LLM_KEY,
    )
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    const problem = validateLlmKey(draft)
    if (problem !== null) {
      setError(problem)
      return
    }
    try {
      await key.save(draft)
      setEditing(false)
      setError(null)
      setProbe(null)
    } catch {
      // 具体原因由 hook 写进 key.error，这里只负责停在编辑态
    }
  }

  const remove = async () => {
    try {
      await key.remove()
      setDraft(EMPTY_LLM_KEY)
      setEditing(false)
      setError(null)
      setProbe(null)
    } catch {
      // 同上：错误已经在 hook 里落成一句话
    }
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
              {key.view.configured ? '修改' : '填写'}
            </button>
          )
        }
      />
      {!online && (
        <p className="key-notice">
          静态演示站不联网：这里能填、也存在这台设备上，但库外菜要真生成得在本地跑完整版。
        </p>
      )}
      {editing ? (
        <form
          className="form"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
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
              placeholder={key.view.configured ? '重新填一份会覆盖原来的' : ''}
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
            <button type="submit" className="btn btn--primary" disabled={key.saving}>
              {key.saving ? '保存中…' : '保存'}
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
      ) : key.checking ? (
        <p className="key-empty">读取账号里的模型 Key…</p>
      ) : !key.view.configured ? (
        <p className="key-empty">
          不填也能用：识别与库内菜谱都不需要它。库里没有的菜要现写做法，就得自己带一把 Key
          {key.view.storedOn === 'account' ? '，存进账号后换设备也能接着用' : '，未登录时只存在这台设备上'}。
        </p>
      ) : (
        <div className="key-saved">
          <p className="key-line">
            <span className="key-label">Key</span>
            <code>{key.view.masked}</code>
          </p>
          {key.view.baseUrl && (
            <p className="key-line">
              <span className="key-label">地址</span>
              <code>{key.view.baseUrl}</code>
            </p>
          )}
          {key.view.model && (
            <p className="key-line">
              <span className="key-label">模型</span>
              <code>{key.view.model}</code>
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
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                void remove()
              }}
            >
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
      {key.error !== null && (
        <p className="field-error" role="alert">
          {key.error}
        </p>
      )}
      <p className="key-caption">
        {key.view.storedOn === 'account'
          ? 'Key 存在你的账号里，服务端加密后落库、只回掩码，明文只在调用模型的那一刻出现在内存里。'
          : 'Key 只存在这台设备上，随请求头发给后端、用完即弃。登录后可以存进账号，换设备也能接着用。'}
      </p>
    </section>
  )
}
