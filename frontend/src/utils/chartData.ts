import type { BarRow } from '../types/workbench'

export function chartNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** Insert a null display point at each missing span; never invent an OHLC fact. */
export function withMinuteGaps(rows: readonly BarRow[], minutes = 1): BarRow[] {
  const out: BarRow[] = []
  for (const row of rows) {
    const previous = out.at(-1)
    const at = Date.parse(String(row.bar_time))
    const before = previous ? Date.parse(String(previous.bar_time)) : NaN
    if (Number.isFinite(before) && at - before > minutes * 60_000) {
      out.push({ bar_time: new Date(before + minutes * 60_000).toISOString(), gap: true })
    }
    out.push(row.complete === false ? { bar_time: row.bar_time, gap: true } : row)
  }
  return out
}

/** Match an event to its actual minute bucket, never to a missing interval. */
export function eventBarIndex(rows: readonly BarRow[], time: string, minutes = 1): number {
  const at = Date.parse(time)
  return rows.findIndex(row => {
    const start = Date.parse(String(row.bar_time))
    return row.gap !== true && Number.isFinite(at) && at >= start && at < start + minutes * 60_000
  })
}
