// 工作台只读 API 客户端（TASK-20261026-13）：唯一出 HTTP 口，页面不得自行 fetch。
// GET 专用；快照语义（as_of/stale/partial）由信封携带，本层只校验与转发。
import { fetchEnvelope, fetchJson } from './client'
import type { Envelope } from '../types/common'
import type {
  AccountSeriesData,
  ActivityKind,
  ActivityPage,
  AlertData,
  BarsData,
  InstrumentEventsData,
  OverviewData,
  PlanRow,
  PositionsData,
  ReportPayload,
  RuntimeData,
} from '../types/workbench'
import type { InstrumentInfo } from '../types/instrument'

const BASE = '/api/workbench'

export interface ReqOptions {
  timeoutMs?: number
  signal?: AbortSignal
}

export function fetchOverview(opts: ReqOptions = {}): Promise<Envelope<OverviewData>> {
  return fetchEnvelope(`${BASE}/overview`, opts)
}

export interface InstrumentQuery {
  q?: string
  signal?: AbortSignal
  timeoutMs?: number
}

/** 标的目录：非信封响应（顶层 instruments），单独校验。 */
export async function fetchInstruments(
  query: InstrumentQuery = {},
): Promise<{ instruments: InstrumentInfo[]; source_notes: Record<string, string> }> {
  const params = new URLSearchParams()
  if (query.q) params.set('q', query.q)
  const qs = params.toString()
  const body = await fetchJson<unknown>(`${BASE}/instruments${qs ? `?${qs}` : ''}`, {
    signal: query.signal,
    timeoutMs: query.timeoutMs,
  })
  if (
    typeof body !== 'object' ||
    body === null ||
    !Array.isArray((body as { instruments?: unknown }).instruments)
  ) {
    throw Object.assign(new Error('服务响应格式异常（标的目录缺失）'), { name: 'HttpError' })
  }
  return body as { instruments: InstrumentInfo[]; source_notes: Record<string, string> }
}

export function fetchPositions(opts: ReqOptions = {}): Promise<Envelope<PositionsData>> {
  return fetchEnvelope(`${BASE}/positions`, opts)
}

export function fetchStrategies(
  limit = 20,
  opts: ReqOptions = {},
): Promise<Envelope<{ plans: PlanRow[] }>> {
  return fetchEnvelope(`${BASE}/strategies?limit=${limit}`, opts)
}

export function fetchActivity(
  kind: ActivityKind,
  filters: { instrumentId?: string; day?: string; limit?: number; cursor?: string } = {},
  opts: ReqOptions = {},
): Promise<Envelope<ActivityPage>> {
  const params = new URLSearchParams({ kind })
  if (filters.instrumentId) params.set('instrument_id', filters.instrumentId)
  if (filters.day) params.set('day', filters.day)
  if (filters.limit) params.set('limit', String(filters.limit))
  if (filters.cursor) params.set('cursor', filters.cursor)
  return fetchEnvelope(`${BASE}/activity?${params.toString()}`, opts)
}

export function fetchActivityDetail(
  kind: ActivityKind,
  itemId: string,
  opts: ReqOptions = {},
): Promise<Envelope<Record<string, unknown>>> {
  return fetchEnvelope(`${BASE}/activity/${kind}/${encodeURIComponent(itemId)}`, opts)
}

export function fetchRuntime(opts: ReqOptions = {}): Promise<Envelope<RuntimeData>> {
  return fetchEnvelope(`${BASE}/runtime`, opts)
}

export function fetchAlerts(opts: ReqOptions = {}): Promise<Envelope<AlertData>> {
  return fetchEnvelope(`${BASE}/alerts`, opts)
}

export type BarPeriod = '1m' | '5m' | '15m' | 'timeshare'

export function fetchBars(
  instrumentId: string,
  period: BarPeriod,
  day?: string,
  opts: ReqOptions = {},
): Promise<Envelope<BarsData>> {
  const params = new URLSearchParams({ period })
  if (day) params.set('day', day)
  return fetchEnvelope(
    `${BASE}/instruments/${encodeURIComponent(instrumentId)}/bars?${params.toString()}`,
    opts,
  )
}

export function fetchInstrumentEvents(
  instrumentId: string,
  day?: string,
  opts: ReqOptions = {},
): Promise<Envelope<InstrumentEventsData>> {
  const qs = day ? `?day=${encodeURIComponent(day)}` : ''
  return fetchEnvelope(`${BASE}/instruments/${encodeURIComponent(instrumentId)}/events${qs}`, opts)
}

export function fetchAccountSeries(
  days = 30,
  opts: ReqOptions = {},
): Promise<Envelope<AccountSeriesData>> {
  return fetchEnvelope(`${BASE}/account/series?days=${days}`, opts)
}

export function fetchReport(day: string, opts: ReqOptions = {}): Promise<ReportPayload> {
  return fetchJson(`${BASE}/reports/${encodeURIComponent(day)}`, opts)
}
