import { useEffect, useRef, useState } from 'react'

import {
  MIN_PASSWORD_LENGTH,
  authErrorMessage,
  validateDisplayName,
  validateEmail,
  validatePassword,
} from '../account'
import type { AuthMode } from '../account'

type Values = { email: string; password: string; displayName: string }

type Props = {
  open: boolean
  /** 静态演示站没有后端：说明原因并挡住提交，不做一次注定失败的请求 */
  available: boolean
  onClose: () => void
  onSubmit: (mode: AuthMode, values: Values) => Promise<void>
}

const TITLE: Record<AuthMode, string> = { login: '登录', register: '注册' }

/**
 * 登录 / 注册弹窗（FR-16）。
 *
 * 校验先在前端做一遍，错在哪一条当场说清；服务端回来的错误（邮箱已注册、密码不对、
 * 试得太频繁）由 `authErrorMessage` 翻成同一套口径显示在同一条位置上。
 */
export function AuthDialog({ open, available, onClose, onSubmit }: Props) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const emailRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    setError(null)
    setBusy(false)
    emailRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) {
      return
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) {
    return null
  }

  const switchMode = (next: AuthMode) => {
    setMode(next)
    setError(null)
  }

  const submit = async () => {
    const problem =
      validateEmail(email) ??
      validatePassword(password, mode) ??
      (mode === 'register' ? validateDisplayName(displayName) : null)
    if (problem !== null) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onSubmit(mode, { email, password, displayName })
      setEmail('')
      setPassword('')
      setDisplayName('')
      onClose()
    } catch (failure) {
      setError(authErrorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="modal-title" id="auth-dialog-title">
          {TITLE[mode]}
        </h2>
        {available && (
          <p className="modal-note">
            账号用来存两样东西：做过的菜谱（换设备也还在），以及你自己的模型 Key
            （加密后落库，界面上只显示掩码）。不登录也能用，只是记忆只留在这一台设备上。
          </p>
        )}
        {!available && (
          <p className="modal-notice">
            静态演示站不带后端服务，登录与同步在这里用不了；在本地跑完整版就能用。
          </p>
        )}
        <form
          className="form"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="auth-email">
              邮箱
            </label>
            <input
              id="auth-email"
              ref={emailRef}
              className="field-input"
              type="email"
              autoComplete="email"
              value={email}
              disabled={!available || busy}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="auth-password">
              密码
            </label>
            <input
              id="auth-password"
              className="field-input"
              type="password"
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              value={password}
              disabled={!available || busy}
              onChange={(event) => setPassword(event.target.value)}
            />
            {mode === 'register' && (
              <span className="field-hint">至少 {MIN_PASSWORD_LENGTH} 位</span>
            )}
          </div>
          {mode === 'register' && (
            <div className="field">
              <label className="field-label" htmlFor="auth-display-name">
                显示名（可留空）
              </label>
              <input
                id="auth-display-name"
                className="field-input"
                type="text"
                autoComplete="nickname"
                value={displayName}
                disabled={!available || busy}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </div>
          )}
          {error !== null && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
          <div className="form-actions">
            <button type="submit" className="btn btn--primary" disabled={!available || busy}>
              {busy ? '处理中…' : TITLE[mode]}
            </button>
            <button type="button" className="btn btn--secondary" onClick={onClose}>
              取消
            </button>
          </div>
        </form>
        <p className="modal-switch">
          {mode === 'login' ? (
            <>
              还没有账号？
              <button
                type="button"
                className="link-btn"
                onClick={() => switchMode('register')}
              >
                注册一个
              </button>
            </>
          ) : (
            <>
              已经有账号了？
              <button type="button" className="link-btn" onClick={() => switchMode('login')}>
                去登录
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  )
}
