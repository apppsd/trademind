// 工作台各端点数据块类型（对齐 26-05/26-06 后端实现；金额一律字符串）。
import type { Envelope } from './common'

export interface AccountBlock {
  cash: string | null
  reserved_cash: string | null
  available_cash: string | null
  equity: string | null
  valuation_complete: boolean
  positions_count: number
}

/** 今日交易口径块（复用日报产物；缺产物时为 null，由 missing_reasons 说明）。 */
export interface TodayBrief {
  trading_pnl: string | null
  realized_pnl: string | null
  trading_fees: string | null
  net_external_cash_flow: string | null
  net_after_settled_model_estimate: string | null
  model_cost_settled: string | null
  model_cost_pending: string | null
  report_period_status: string | null
  report_generated_at: string | null
}

export interface RuntimeBrief {
  phase: string | null
  halted: boolean | null
  heartbeat_age_seconds: number | null
}

export interface OverviewData {
  account: AccountBlock | null
  today: TodayBrief | null
  runtime_brief: RuntimeBrief
}

export interface PositionRow {
  instrument_id: string
  quantity: string
  available_quantity: string
  avg_cost: string
  last_price: string | null
  market_value: string
  unrealized_pnl: string | null
  currency: string | null
}

export interface PositionsData {
  positions: PositionRow[]
  valuation_complete: boolean
}

/** 策略计划行由后端 public_plan 决定；页面按需收窄，不在共享层臆造字段。 */
export type PlanRow = Record<string, unknown>

export type ActivityKind = 'decision' | 'order' | 'fill' | 'cash'

/** 活动行（后端 _public 白名单脱敏键；未含键以实际为准，页面用安全读取）。 */
export interface ActivityItem {
  id: string
  decision_id?: string
  order_id?: string
  fill_id?: string
  instrument_id?: string
  action?: string
  side?: string
  status?: string
  quantity?: string
  filled_quantity?: string
  price?: string
  limit_price?: string
  avg_fill_price?: string
  fee?: string
  amount?: string
  created_at?: string
  executed_at?: string
  effective_at?: string
  valid_until?: string
  reason?: string
  trace_id?: string
  kind?: string
  seq?: number
  [k: string]: unknown
}

export interface ActivityPage {
  kind: ActivityKind
  items: ActivityItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface ObserverStatus {
  running: boolean
  pid: number | null
  heartbeat_age_seconds: number | null
}

export interface RuntimeData {
  supervisor: Record<string, unknown> | null
  intraday_health: Record<string, unknown> | null
  observer: ObserverStatus | null
  kill_switch: { engaged: boolean; reason: string | null; updated_at: string } | null
  model_budget_today: { settled_today: string | null; pending_today: string | null } | null
  capabilities: { controls_available: boolean; observer_read_only: boolean }
}

export interface AlertItem {
  type: string
  instrument_id: string | null
  reason: string
  severity: 'danger' | 'warn'
}

export interface AlertData {
  alerts: AlertItem[]
}

/** 分钟/K线行（bar_time/open/high/low/close/volume/amount；聚合行含 complete）。 */
export type BarRow = Record<string, unknown>

export interface BarsData {
  instrument_id: string
  period: string
  bars: BarRow[]
  gap_notes?: { from: string; to: string; reason: string }[]
  coverage?: { from: string; to: string; count: number }
  capabilities: { intraday_bars: boolean; daily_bars: boolean; daily_bars_reason?: string }
  note?: string
}

export interface EventMark {
  time: string
  type: 'fill' | 'order_submitted' | 'intent'
  action?: string | null
  side?: string | null
  status?: string | null
  price: string | null
  quantity: string | null
  id?: string
}

export interface InstrumentEventsData {
  instrument_id: string
  day: string
  events: EventMark[]
  mark_semantics: Record<string, string>
}

/** 账户序列行（日级权益/盈亏/模型费与当日分钟现金曲线）。 */
export type SeriesRow = Record<string, unknown>

export interface AccountSeriesData {
  days: number
  equity_daily: SeriesRow[]
  trading_pnl_daily: SeriesRow[]
  model_cost_daily: SeriesRow[]
  cash_intraday_today: SeriesRow[]
  semantics?: Record<string, string>
  note?: string
}

export type ReportPayload = Record<string, unknown>

export type { Envelope }
