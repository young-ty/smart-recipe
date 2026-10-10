// localStorage 的安全读写。
//
// 菜谱记忆与用户自己的模型 Key 都落在这里，两处需要同一套容错：隐私模式下访问
// localStorage 本身就可能抛异常，配额满时写入也会失败。任何一次失败都只让对应的
// 那件事退化成「本机没存过」，不能影响识别与取菜谱的主链路。

export function defaultStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

/** 读 JSON：键不存在、内容被改坏、存储不可用都返回 null */
export function readStoredJson(key: string, target: Storage | null = defaultStorage()): unknown {
  if (target === null) {
    return null
  }
  try {
    const raw = target.getItem(key)
    return raw === null ? null : JSON.parse(raw)
  } catch {
    return null
  }
}

export function writeStoredJson(
  key: string,
  value: unknown,
  target: Storage | null = defaultStorage(),
): void {
  if (target === null) {
    return
  }
  try {
    target.setItem(key, JSON.stringify(value))
  } catch {
    // 配额满或被禁用：数据只对本次打开有效
  }
}

export function removeStored(key: string, target: Storage | null = defaultStorage()): void {
  if (target === null) {
    return
  }
  try {
    target.removeItem(key)
  } catch {
    // 同上
  }
}
