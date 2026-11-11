// 工作台只读 API 客户端（TASK-20261026-13）：唯一出 HTTP 口，页面不得自行 fetch。
// GET 专用；快照语义（as_of/stale/partial）由信封携带，本层只校验与转发。
// SAMPLE 演示门（TASK-09a-1）：cockpitSampleGate 激活时，每个取数函数在入口
// 短路返回本地样例信封（src/demo/tradeMindCockpitSample.ts），不发任何请求。
import { fetchEnvelope, fetchJson } from './client'
import { isCockpitSampleActive } from './cockpitSampleGate'
import {
  sampleAccountSeries,
  sampleActivity,
  sampleActivityDetail,
  sampleAlerts,
  sampleBars,
  sampleInstruments,
  sampleInstrumentEvents,
  sampleOverview,
  samplePositions,
  sampleReport,
  sampleRuntime,
  sampleStrategies,
} from '../demo/tradeMindCockpitSample'
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
  if (isCockpitSampleActive()) return Promise.resolve(sampleOverview)
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
  if (isCockpitSampleActive()) return sampleInstruments
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
  if (isCockpitSampleActive()) return Promise.resolve(samplePositions)
  return fetchEnvelope(`${BASE}/positions`, opts)
}

export function fetchStrategies(
  limit = 20,
  opts: ReqOptions = {},
): Promise<Envelope<{ plans: PlanRow[] }>> {
  // 样例固定返回内置 3 行计划，limit 参数不生效（SAMPLE 演示语义）。
  if (isCockpitSampleActive()) return Promise.resolve(sampleStrategies)
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
  if (isCockpitSampleActive()) return Promise.resolve(sampleActivity(kind))
  return fetchEnvelope(`${BASE}/activity?${params.toString()}`, opts)
}

export function fetchActivityDetail(
  kind: ActivityKind,
  itemId: string,
  opts: ReqOptions = {},
): Promise<Envelope<Record<string, unknown>>> {
  if (isCockpitSampleActive()) return Promise.resolve(sampleActivityDetail(kind, itemId))
  return fetchEnvelope(`${BASE}/activity/${kind}/${encodeURIComponent(itemId)}`, opts)
}

export function fetchRuntime(opts: ReqOptions = {}): Promise<Envelope<RuntimeData>> {
  if (isCockpitSampleActive()) return Promise.resolve(sampleRuntime)
  return fetchEnvelope(`${BASE}/runtime`, opts)
}

export function fetchAlerts(opts: ReqOptions = {}): Promise<Envelope<AlertData>> {
  if (isCockpitSampleActive()) return Promise.resolve(sampleAlerts)
  return fetchEnvelope(`${BASE}/alerts`, opts)
}

export type BarPeriod = '1m' | '5m' | '15m' | 'timeshare'

export function fetchBars(
  instrumentId: string,
  period: BarPeriod,
  day?: string,
  opts: ReqOptions = {},
): Promise<Envelope<BarsData>> {
  if (isCockpitSampleActive()) return Promise.resolve(sampleBars(instrumentId, period, day))
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
  if (isCockpitSampleActive()) return Promise.resolve(sampleInstrumentEvents(instrumentId, day))
  const qs = day ? `?day=${encodeURIComponent(day)}` : ''
  return fetchEnvelope(`${BASE}/instruments/${encodeURIComponent(instrumentId)}/events${qs}`, opts)
}

export function fetchAccountSeries(
  days = 30,
  opts: ReqOptions = {},
): Promise<Envelope<AccountSeriesData>> {
  // 样例固定返回内置近 10 日序列，days 参数不生效（SAMPLE 演示语义）。
  if (isCockpitSampleActive()) return Promise.resolve(sampleAccountSeries)
  return fetchEnvelope(`${BASE}/account/series?days=${days}`, opts)
}

export function fetchReport(day: string, opts: ReqOptions = {}): Promise<ReportPayload> {
  // 样例直接返回本地 blob 对象，不请求 /api/workbench/reports。
  if (isCockpitSampleActive()) return Promise.resolve(sampleReport(day))
  return fetchJson(`${BASE}/reports/${encodeURIComponent(day)}`, opts)
}
