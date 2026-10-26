// 展示格式化（TASK-20261026-13 共享基线）：null 永远显示 '—'，绝不显示 0。
const SHANGHAI_TZ = 'Asia/Shanghai'

export function fmtDash(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

export function fmtMoney(value: string | null | undefined, unit = '元'): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${value} ${unit}`
}

/** A股红涨绿跌：负值 down（绿），正值 up（红），其余 flat。 */
export function signClass(value: string | number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || value === '') return 'flat'
  const text = String(value)
  if (text.startsWith('-')) return 'down'
  if (text.startsWith('+') || (Number(text) > 0 && text !== '0')) return 'up'
  return 'flat'
}

/** ISO(UTC) → 上海时间 'MM-dd HH:mm:ss'；无法解析返回 null。 */
export function shanghaiTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TZ, month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}

export function ageLabel(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null
  if (seconds < 60) return `${seconds} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${Math.floor(seconds / 3600)} 小时前`
}

/** 上海日历日 YYYY-MM-DD（日报切日口径，不用浏览器本地时区）。 */
export function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHANGHAI_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
  return parts // en-CA 即 YYYY-MM-DD
}
