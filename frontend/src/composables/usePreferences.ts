// 本地偏好（TASK-20261026-13）：仅收藏/显示排序/周期/减少动效。
// 绝不存储 token、凭据或原始 API 响应；读写失败静默降级（隐私模式/禁用存储）。
import { ref, watch, type Ref } from 'vue'

const PREFIX = 'wb:'

function readKey(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key)
  } catch {
    return null // 存储不可用：偏好退化为会话内状态
  }
}

function writeKey(key: string, value: string): void {
  try {
    localStorage.setItem(PREFIX + key, value)
  } catch {
    /* 忽略写入失败 */
  }
}

function persistedRef(key: string, fallback: string): Ref<string> {
  const value = ref(readKey(key) ?? fallback)
  // sync flush：偏好是小字符串，同步落盘保证行为确定（无组件上下文时 watch 也不会延迟）。
  watch(value, (v) => writeKey(key, v), { flush: 'sync' })
  return value
}

/** 收藏标的（逗号分隔 instrument_id）。变更偏好不产生任何交易操作。 */
export function useFavoriteInstruments() {
  const raw = persistedRef('favorites', '')
  const favorites = ref<Set<string>>(new Set(raw.value ? raw.value.split(',') : []))

  function toggle(instrumentId: string): void {
    const next = new Set(favorites.value)
    if (next.has(instrumentId)) next.delete(instrumentId)
    else next.add(instrumentId)
    favorites.value = next
    raw.value = [...next].join(',')
  }

  return { favorites, toggle }
}

/** 行情周期偏好（分时/5m/15m）。 */
export function usePreferredPeriod(fallback = 'timeshare') {
  return persistedRef('period', fallback)
}

/** 减少动效：系统偏好或用户显式覆盖（'system' | 'on' | 'off'）。 */
export function useReducedMotionPreference() {
  const pref = persistedRef('reduced-motion', 'system')
  function prefersReducedMotion(): boolean {
    if (pref.value === 'on') return true
    if (pref.value === 'off') return false
    return typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  }
  return { pref, prefersReducedMotion }
}
