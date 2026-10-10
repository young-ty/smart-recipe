import { displayNameOf } from '../account'
import type { MemorySyncState } from '../hooks/useMemorySync'
import type { AccountUser } from '../types'

type Props = {
  user: AccountUser | null
  checking: boolean
  syncState: MemorySyncState
  onOpenAuth: () => void
  onLogout: () => void
}

const SYNC_LABEL: Record<MemorySyncState, string | null> = {
  idle: null,
  syncing: '记忆同步中…',
  synced: '记忆已同步',
  failed: '记忆同步失败',
}

/** 页头右上角：登录入口与登录后的账号名。未登录时整页功能照旧可用。 */
export function AccountBar({ user, checking, syncState, onOpenAuth, onLogout }: Props) {
  if (checking) {
    // 先不写「未登录」：健康检查与登录态是两回事，闪一下再变很晃眼
    return <span className="account-hint">账号检测中…</span>
  }
  if (user === null) {
    return (
      <button type="button" className="btn btn--secondary account-btn" onClick={onOpenAuth}>
        登录 / 注册
      </button>
    )
  }
  const sync = SYNC_LABEL[syncState]
  return (
    <div className="account-signed">
      <span className="account-name" title={user.email}>
        {displayNameOf(user)}
      </span>
      {sync !== null && <span className="account-sync">{sync}</span>}
      <button type="button" className="link-btn" onClick={onLogout}>
        退出
      </button>
    </div>
  )
}
