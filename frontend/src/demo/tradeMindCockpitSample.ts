/**
 * 工作台驾驶舱（TradeMind Cockpit）SAMPLE 演示数据（TASK-09a-1）。
 *
 * 覆盖 workbenchApi 全部导出的本地样例信封；仅当 cockpitSampleGate 激活时由
 * workbenchApi 短路返回，绝不发出网络请求。全部数值为虚构模拟盘样例
 * （A股、人民币、金额一律字符串），不代表任何真实账户或行情。
 * 快照对齐 traderCockpitSample：2026-10-29 15:00 收盘（UTC+8）。
 *
 * 注：日报 JSON 产物本身不含 markdown 字段（Markdown 是 ?fmt=md 独立路由），
 * 因此样例 report 载荷同样不含 markdown 字段。
 */
import type { Envelope } from '../types/common'
import type {
  AccountSeriesData,
  ActivityKind,
  ActivityPage,
  ActivityItem,
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

const AS_OF = '2026-10-29T15:00:00+08:00'

/** 统一样例信封：快照固定 2026-10-29 15:00 收盘，source 标记 SAMPLE。 */
function env<T>(data: T, overrides: Partial<Envelope<T>> = {}): Envelope<T> {
  return {
    schema_version: 1,
    account_alias: 'SAMPLE',
    as_of: AS_OF,
    generated_at: AS_OF,
    data_status: 'ok',
    source: 'SAMPLE',
    age_seconds: 0,
    stale_after_seconds: null,
    partial: false,
    missing_reasons: [],
    data,
    ...overrides,
  }
}

// ---------------------------------------------------------------- overview
// 与 traderCockpitSample 对齐：权益 50000.00 = 现金 20755.00 + 持仓 29245.00。
export const sampleOverview: Envelope<OverviewData> = env({
  account: {
    cash: '20755.00',
    reserved_cash: '0.00',
    available_cash: '20755.00',
    equity: '50000.00',
    valuation_complete: true,
    positions_count: 2,
  },
  today: {
    trading_pnl: '229.00',
    realized_pnl: '0.00',
    trading_fees: '0.00',
    net_external_cash_flow: '0.00',
    net_after_settled_model_estimate: '216.94',
    model_cost_settled: '12.06',
    model_cost_pending: '0.00',
    report_period_status: 'end_of_day_snapshot',
    report_generated_at: '2026-10-29T15:00:21+08:00',
  },
  runtime_brief: { phase: 'paper_session_closed', halted: false, heartbeat_age_seconds: 35 },
})

// --------------------------------------------------------------- positions
export const samplePositions: Envelope<PositionsData> = env({
  positions: [
    {
      instrument_id: '600036',
      quantity: '400',
      available_quantity: '400',
      avg_cost: '38.50',
      last_price: '39.85',
      market_value: '15940.00',
      unrealized_pnl: '540.00',
      currency: 'CNY',
    },
    {
      instrument_id: '601318',
      quantity: '300',
      available_quantity: '300',
      avg_cost: '45.20',
      last_price: '44.35',
      market_value: '13305.00',
      unrealized_pnl: '-255.00',
      currency: 'CNY',
    },
  ],
  valuation_complete: true,
})

// -------------------------------------------------------------- strategies
// 行结构对齐后端 public_plan 白名单键（daily.py）。
const SAMPLE_PLANS: PlanRow[] = [
  {
    plan_id: 'SAMPLE-plan-20261029-001',
    instrument_id: '510300',
    model: 'SAMPLE-flash',
    prompt_version: 'v0-sample',
    created_at: '2026-10-29T15:00:05+08:00',
    available_at: '2026-10-29T15:00:05+08:00',
    valid_until: '2026-11-02T15:00:00+08:00',
    trend: '样例：动量分 82，距买入阈值 85 差 3 分',
    risk: '样例：单票上限 40% 权益，组合日内回撤 ≥ 2% 触发复核',
    critic: '样例：复核通过，等待下一轮动窗口',
    reason: '样例：今日（周四）非调仓日，仅监控不交易',
    allowed_actions: ['hold', 'buy'],
    entry_conditions: ['2026-11-02 动量分 ≥ 85（样例条件）'],
    exit_conditions: ['跌破 MA20（样例条件）'],
    invalidation_conditions: ['开盘高开超 3%（样例条件）'],
    holding_horizon_seconds: 604800,
  },
  {
    plan_id: 'SAMPLE-plan-20261029-002',
    instrument_id: '600036',
    model: 'SAMPLE-flash',
    prompt_version: 'v0-sample',
    created_at: '2026-10-29T15:00:05+08:00',
    available_at: '2026-10-29T15:00:05+08:00',
    valid_until: '2026-11-02T15:00:00+08:00',
    trend: '样例：价格沿 MA20 上方运行，10-29 放量上攻未触及 40.00 观察线',
    risk: '样例：已持有 400 股，仓位 31.9% 权益，低于 40% 上限',
    critic: '样例：维持持有',
    reason: '样例：未触发止损/止盈，继续持有',
    allowed_actions: ['hold'],
    entry_conditions: [],
    exit_conditions: ['跌破 35.80 止损线（样例条件）'],
    invalidation_conditions: [],
    holding_horizon_seconds: 1209600,
  },
  {
    plan_id: 'SAMPLE-plan-20261029-003',
    instrument_id: '601318',
    model: 'SAMPLE-flash',
    prompt_version: 'v0-sample',
    created_at: '2026-10-29T15:00:05+08:00',
    available_at: '2026-10-29T15:00:05+08:00',
    valid_until: '2026-11-02T15:00:00+08:00',
    trend: '样例：浮亏但未触发止损',
    risk: '样例：浮亏 -255.00 元，止损线 40.70 未触及',
    critic: '样例：维持持有',
    reason: '样例：持有加分维持，观察后续走势',
    allowed_actions: ['hold'],
    entry_conditions: [],
    exit_conditions: ['跌破 40.70 止损线（样例条件）'],
    invalidation_conditions: [],
    holding_horizon_seconds: 1209600,
  },
]

export const sampleStrategies: Envelope<{ plans: PlanRow[] }> = env({ plans: SAMPLE_PLANS })

// ---------------------------------------------------------------- activity
const SAMPLE_ACTIVITY: Record<ActivityKind, ActivityItem[]> = {
  decision: [
    {
      id: 'SAMPLE-decision-20261029-0931-01',
      decision_id: 'SAMPLE-decision-20261029-0931-01',
      instrument_id: '600036',
      action: 'hold',
      reason: '样例：放量观察线 40.00 未触及，维持持仓（SAMPLE）',
      created_at: '2026-10-29T09:31:02+08:00',
      trace_id: 'SAMPLE-trace-20261029-01',
      kind: 'decision',
      seq: 1,
    },
    {
      id: 'SAMPLE-decision-20261029-1130-02',
      decision_id: 'SAMPLE-decision-20261029-1130-02',
      instrument_id: '510300',
      action: 'hold',
      reason: '样例：午间复核候选分未达阈值，不调仓（SAMPLE）',
      created_at: '2026-10-29T11:30:40+08:00',
      trace_id: 'SAMPLE-trace-20261029-02',
      kind: 'decision',
      seq: 2,
    },
  ],
  order: [
    {
      id: 'SAMPLE-order-20261027-1456-01',
      order_id: 'SAMPLE-order-20261027-1456-01',
      instrument_id: '512880',
      side: 'sell',
      status: 'filled',
      quantity: '2000',
      filled_quantity: '2000',
      limit_price: '1.1030',
      created_at: '2026-10-27T14:56:10+08:00',
      executed_at: '2026-10-27T14:57:30+08:00',
      kind: 'order',
    },
    {
      id: 'SAMPLE-order-20260928-1014-01',
      order_id: 'SAMPLE-order-20260928-1014-01',
      instrument_id: '600036',
      side: 'buy',
      status: 'filled',
      quantity: '200',
      filled_quantity: '200',
      limit_price: '38.65',
      created_at: '2026-09-28T10:14:55+08:00',
      executed_at: '2026-09-28T10:15:03+08:00',
      kind: 'order',
    },
  ],
  fill: [
    {
      id: 'SAMPLE-fill-20261027-1457-01',
      fill_id: 'SAMPLE-fill-20261027-1457-01',
      order_id: 'SAMPLE-order-20261027-1456-01',
      instrument_id: '512880',
      side: 'sell',
      status: 'filled',
      quantity: '2000',
      avg_fill_price: '1.1020',
      fee: '2.20',
      amount: '2204.00',
      executed_at: '2026-10-27T14:57:30+08:00',
      kind: 'fill',
    },
    {
      id: 'SAMPLE-fill-20260928-1015-01',
      fill_id: 'SAMPLE-fill-20260928-1015-01',
      order_id: 'SAMPLE-order-20260928-1014-01',
      instrument_id: '600036',
      side: 'buy',
      status: 'filled',
      quantity: '200',
      avg_fill_price: '38.64',
      fee: '5.00',
      amount: '7728.00',
      executed_at: '2026-09-28T10:15:03+08:00',
      kind: 'fill',
    },
  ],
  cash: [
    {
      id: 'SAMPLE-cash-20261027-1600-01',
      amount: '2204.00',
      reason: '样例：512880 卖出资金交收（SAMPLE）',
      effective_at: '2026-10-27T16:00:00+08:00',
      kind: 'cash',
      seq: 2,
    },
    {
      id: 'SAMPLE-cash-20260928-1015-02',
      amount: '-7733.00',
      reason: '样例：600036 买入成交扣款，含佣金（SAMPLE）',
      effective_at: '2026-09-28T10:15:03+08:00',
      kind: 'cash',
      seq: 1,
    },
  ],
}

export function sampleActivity(kind: ActivityKind): Envelope<ActivityPage> {
  return env({ kind, items: SAMPLE_ACTIVITY[kind], next_cursor: null, has_more: false })
}

export function sampleActivityDetail(
  kind: ActivityKind,
  itemId: string,
): Envelope<Record<string, unknown>> {
  return env({
    id: itemId,
    kind,
    sample_note: '样例详情（SAMPLE）：完整字段以真实后端返回为准',
    snapshot_at: AS_OF,
  })
}

// ----------------------------------------------------------------- runtime
export const sampleRuntime: Envelope<RuntimeData> = env({
  supervisor: {
    running: true,
    phase: 'paper',
    started_at: '2026-10-29T09:15:00+08:00',
    note: '样例（SAMPLE）',
  },
  intraday_health: {
    health_status: 'ok',
    status_message_zh: '样例：行情与账户链路正常（SAMPLE）',
    checked_at: AS_OF,
  },
  observer: { running: true, pid: null, heartbeat_age_seconds: 3 },
  kill_switch: { engaged: false, reason: null, updated_at: '2026-10-29T09:15:00+08:00' },
  model_budget_today: { settled_today: '12.06', pending_today: '0.00' },
  capabilities: { controls_available: false, observer_read_only: true },
})

// ------------------------------------------------------------------ alerts
export const sampleAlerts: Envelope<AlertData> = env({
  alerts: [
    {
      type: 'position_drawdown',
      instrument_id: '601318',
      reason: '样例：601318 浮亏 -255.00 元，未触发止损（SAMPLE）',
      severity: 'warn',
    },
    {
      type: 'session_note',
      instrument_id: null,
      reason: '样例：非调仓日，候选动量分未达阈值（SAMPLE）',
      severity: 'warn',
    },
  ],
})

// -------------------------------------------------------------------- bars
// 样例分时：600036 手工十根（14:51–15:00，收盘 39.85 与持仓 last 对齐）；
// 其余样例标的按基准收盘价线性合成，仅作界面演示。
const SAMPLE_CLOSE: Record<string, number> = { '600036': 39.85, '601318': 44.35, '510300': 4.05 }
const SAMPLE_MINUTES = ['14:51', '14:52', '14:53', '14:54', '14:55', '14:56', '14:57', '14:58', '14:59', '15:00']

const SAMPLE_BARS_600036: Record<string, unknown>[] = [
  { bar_time: '2026-10-29T14:51:00+08:00', open: '39.62', high: '39.66', low: '39.60', close: '39.64', volume: '382000', amount: '15138660.00', complete: true },
  { bar_time: '2026-10-29T14:52:00+08:00', open: '39.64', high: '39.68', low: '39.62', close: '39.66', volume: '356000', amount: '14115400.00', complete: true },
  { bar_time: '2026-10-29T14:53:00+08:00', open: '39.66', high: '39.67', low: '39.61', close: '39.63', volume: '298000', amount: '11812720.00', complete: true },
  { bar_time: '2026-10-29T14:54:00+08:00', open: '39.63', high: '39.65', low: '39.59', close: '39.60', volume: '311000', amount: '12319820.00', complete: true },
  { bar_time: '2026-10-29T14:55:00+08:00', open: '39.60', high: '39.62', low: '39.55', close: '39.56', volume: '367000', amount: '14529860.00', complete: true },
  { bar_time: '2026-10-29T14:56:00+08:00', open: '39.56', high: '39.58', low: '39.52', close: '39.53', volume: '402000', amount: '15893100.00', complete: true },
  { bar_time: '2026-10-29T14:57:00+08:00', open: '39.53', high: '39.55', low: '39.50', close: '39.52', volume: '358000', amount: '14148160.00', complete: true },
  { bar_time: '2026-10-29T14:58:00+08:00', open: '39.52', high: '39.60', low: '39.50', close: '39.58', volume: '445000', amount: '17599750.00', complete: true },
  { bar_time: '2026-10-29T14:59:00+08:00', open: '39.58', high: '39.86', low: '39.56', close: '39.82', volume: '1204000', amount: '47859000.00', complete: true },
  { bar_time: '2026-10-29T15:00:00+08:00', open: '39.82', high: '39.92', low: '39.80', close: '39.85', volume: '2286000', amount: '91142820.00', complete: true },
]

/** 其余样例标的：由基准收盘价线性回推的开盘价合成十根，确定性、无随机。 */
function synthesizedBars(instrumentId: string): Record<string, unknown>[] {
  const close = SAMPLE_CLOSE[instrumentId] ?? 10.0
  const open = Math.round(close * 0.994 * 1000) / 1000
  return SAMPLE_MINUTES.map((minute, i) => {
    const c = Math.round((open + ((close - open) * i) / (SAMPLE_MINUTES.length - 1)) * 1000) / 1000
    const volume = String(900000 - i * 40000 + (i === SAMPLE_MINUTES.length - 1 ? 1800000 : 0))
    return {
      bar_time: `2026-10-29T${minute}:00+08:00`,
      open: c.toFixed(3),
      high: (c + 0.004).toFixed(3),
      low: (c - 0.004).toFixed(3),
      close: c.toFixed(3),
      volume,
      amount: (c * Number(volume)).toFixed(2),
      complete: true,
    }
  })
}

export function sampleBars(instrumentId: string, period: string, day?: string): Envelope<BarsData> {
  const bars = instrumentId === '600036' ? SAMPLE_BARS_600036 : synthesizedBars(instrumentId)
  return env({
    instrument_id: instrumentId,
    period,
    bars,
    gap_notes: [],
    coverage: { from: '2026-10-29T14:51:00+08:00', to: '2026-10-29T15:00:00+08:00', count: bars.length },
    capabilities: { intraday_bars: true, daily_bars: true },
    note: `样例分时（SAMPLE）：仅 14:51–15:00 十根演示数据${day ? `，请求日 ${day} 未建模，一律返回本快照` : ''}，非完整交易日`,
  })
}

// ------------------------------------------------------- instrumentEvents
// 10-29 当日 600036 仅一条策略意图（09:31 观察线 40.00，与 traderCockpitSample 对齐）；
// 512880 在 10-27 有卖出成交标记；其余返回空事件。
const SAMPLE_MARK_SEMANTICS: Record<string, string> = {
  fill: '成交标记：按成交均价标注（SAMPLE）',
  order_submitted: '订单提交标记（SAMPLE）',
  intent: '策略意图标记：非订单、非成交（SAMPLE）',
}

export function sampleInstrumentEvents(
  instrumentId: string,
  day?: string,
): Envelope<InstrumentEventsData> {
  const events =
    day === '2026-10-27' && instrumentId === '512880'
      ? [{ time: '2026-10-27T14:57:30+08:00', type: 'fill' as const, action: 'sell', side: 'sell', status: 'filled', price: '1.1020', quantity: '2000', id: 'SAMPLE-fill-20261027-1457-01' }]
      : day === '2026-10-29' && instrumentId === '600036'
        ? [{ time: '2026-10-29T09:31:02+08:00', type: 'intent' as const, action: 'hold', side: null, status: null, price: '40.00', quantity: null, id: 'SAMPLE-decision-20261029-0931-01' }]
        : []
  return env({
    instrument_id: instrumentId,
    day: day ?? '2026-10-29',
    events,
    mark_semantics: SAMPLE_MARK_SEMANTICS,
  })
}

// ------------------------------------------------------------ accountSeries
// 近 10 个交易日权益（末 10 天取自 traderCockpitSample.equitySeries）。
const EQUITY_DAILY: { date: string; equity: string; trading_pnl: string; model_cost: string }[] = [
  { date: '2026-10-16', equity: '49748.00', trading_pnl: '75.50', model_cost: '6.32' },
  { date: '2026-10-19', equity: '49663.50', trading_pnl: '-84.50', model_cost: '5.87' },
  { date: '2026-10-20', equity: '49757.00', trading_pnl: '93.50', model_cost: '7.04' },
  { date: '2026-10-21', equity: '49842.50', trading_pnl: '85.50', model_cost: '8.11' },
  { date: '2026-10-22', equity: '49918.00', trading_pnl: '75.50', model_cost: '6.45' },
  { date: '2026-10-23', equity: '49845.50', trading_pnl: '-72.50', model_cost: '9.23' },
  { date: '2026-10-26', equity: '49662.50', trading_pnl: '-183.00', model_cost: '11.40' },
  { date: '2026-10-27', equity: '49569.00', trading_pnl: '-93.50', model_cost: '15.86' },
  { date: '2026-10-28', equity: '49771.00', trading_pnl: '202.00', model_cost: '12.07' },
  { date: '2026-10-29', equity: '50000.00', trading_pnl: '229.00', model_cost: '12.06' },
]

export const sampleAccountSeries: Envelope<AccountSeriesData> = env({
  days: EQUITY_DAILY.length,
  equity_daily: EQUITY_DAILY,
  trading_pnl_daily: EQUITY_DAILY.map((r) => ({ date: r.date, trading_pnl: r.trading_pnl })),
  model_cost_daily: EQUITY_DAILY.map((r) => ({ date: r.date, model_cost: r.model_cost })),
  cash_intraday_today: [
    { time: '2026-10-29T09:30:00+08:00', cash: '20755.00' },
    { time: '2026-10-29T11:30:00+08:00', cash: '20755.00' },
    { time: '2026-10-29T14:00:00+08:00', cash: '20755.00' },
    { time: '2026-10-29T15:00:00+08:00', cash: '20755.00' },
  ],
  semantics: {
    equity: '日终总权益（元，字符串）（SAMPLE）',
    trading_pnl: '当日交易盈亏 = 期末权益 − 期初权益 − 净外部资金流（SAMPLE）',
    model_cost: '模型费当日已结算估算：配置估算，非供应商账单（SAMPLE）',
    cash: '盘中现金快照；今日无交易，全天不变（SAMPLE）',
  },
  note: '样例（SAMPLE）：近 10 个交易日，非真实账户曲线',
})

// ------------------------------------------------------------------ report
// 对齐 daily.py 日报 JSON 产物结构；请求日不建模，一律返回本快照并回填 date。
// 真实产物不含 markdown 字段（Markdown 走 ?fmt=md 独立路由），样例同样不含。
const SAMPLE_REPORT_POSITIONS = [
  { instrument_id: '600036', quantity: '400', avg_cost: '38.50', last_price: '39.85', market_value: '15940.00', unrealized_pnl: '540.00', currency: 'CNY' },
  { instrument_id: '601318', quantity: '300', avg_cost: '45.20', last_price: '44.35', market_value: '13305.00', unrealized_pnl: '-255.00', currency: 'CNY' },
]

export function sampleReport(day: string): ReportPayload {
  return {
    schema_version: 1,
    account_id: 'SAMPLE-PAPER-CNY-1',
    date: day,
    timezone: 'Asia/Shanghai',
    generated_at: '2026-10-29T15:00:21+08:00',
    as_of: AS_OF,
    period_status: 'end_of_day_snapshot',
    intraday_health: { health_status: 'ok', status_message_zh: '样例：全链路正常（SAMPLE）' },
    opening: {
      cash: '20755.00',
      reserved_cash: '0.00',
      available_cash: '20755.00',
      positions: SAMPLE_REPORT_POSITIONS,
      equity: '49771.00',
      valuation_complete: true,
    },
    ending: {
      cash: '20755.00',
      reserved_cash: '0.00',
      available_cash: '20755.00',
      positions: SAMPLE_REPORT_POSITIONS,
      equity: '50000.00',
      valuation_complete: true,
    },
    net_external_cash_flow: '0.00',
    opening_includes_initial_deposit: true,
    trading_pnl: '229.00',
    realized_pnl: '0.00',
    trading_fees: '0.00',
    net_after_settled_model_estimate: '216.94',
    net_after_model_reservation_bound: '216.94',
    model_cost: {
      currency: 'CNY',
      semantics: 'configured_estimates_not_provider_invoice',
      settled_estimate: '12.06',
      pending_reservation: '0.00',
      requested_count: 18,
      audit_rows: 16,
      audit_attempts: 18,
      audit_scope: 'account',
      calls: [],
      audit_unknown_cost_rows: 0,
    },
    trades: [],
    strategies: SAMPLE_PLANS,
    decisions: [
      { decision_id: 'SAMPLE-decision-20261029-0931-01', created_at: '2026-10-29T09:31:02+08:00', instrument_id: '600036', action: 'hold', reason: '样例：观察线未触及，维持持仓（SAMPLE）', prompt_id: 'SAMPLE-prompt-01' },
      { decision_id: 'SAMPLE-decision-20261029-1130-02', created_at: '2026-10-29T11:30:40+08:00', instrument_id: '510300', action: 'hold', reason: '样例：候选分未达阈值（SAMPLE）', prompt_id: 'SAMPLE-prompt-01' },
    ],
    warnings: ['样例数据（SAMPLE）：仅供界面演示，不代表真实账户或真实行情。'],
    scope: 'SAMPLE_paper_account_ui_demo_only',
  }
}

// -------------------------------------------------------------- instruments
// 三只样例标的（与 traderCockpitSample 候选/持仓一致）；名称带 SAMPLE 标记，
// name_source 记 unknown，不冒充已核验白名单。
export const sampleInstruments: {
  instruments: InstrumentInfo[]
  source_notes: Record<string, string>
} = {
  instruments: [
    {
      instrument_id: '600036',
      display_name: '招商银行（SAMPLE）',
      full_name: null,
      market: 'SH',
      kind: 'stock',
      currency: 'CNY',
      name_source: 'unknown',
      aliases: [],
      purchase_check: { status: 'ok', reasons: [], one_lot_cost: '3985.00' },
      capabilities: { tradable: true, settlement_rule: 'T+1（样例）', lot_size: 100, intraday_bars: true, daily_bars: true },
    },
    {
      instrument_id: '601318',
      display_name: '中国平安（SAMPLE）',
      full_name: null,
      market: 'SH',
      kind: 'stock',
      currency: 'CNY',
      name_source: 'unknown',
      aliases: [],
      purchase_check: { status: 'ok', reasons: [], one_lot_cost: '4435.00' },
      capabilities: { tradable: true, settlement_rule: 'T+1（样例）', lot_size: 100, intraday_bars: true, daily_bars: true },
    },
    {
      instrument_id: '510300',
      display_name: '沪深300ETF（SAMPLE）',
      full_name: null,
      market: 'SH',
      kind: 'fund',
      currency: 'CNY',
      name_source: 'unknown',
      aliases: [],
      purchase_check: { status: 'ok', reasons: [], one_lot_cost: '405.00' },
      capabilities: { tradable: true, settlement_rule: 'T+1（样例）', lot_size: 100, intraday_bars: true, daily_bars: true },
    },
  ],
  source_notes: {
    '600036': '样例目录（SAMPLE）：非真实白名单登记',
    '601318': '样例目录（SAMPLE）：非真实白名单登记',
    '510300': '样例目录（SAMPLE）：非真实白名单登记',
  },
}
