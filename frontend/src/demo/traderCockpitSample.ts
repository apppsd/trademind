/**
 * 交易驾驶舱（Trader Cockpit）演示样例数据。
 *
 * 全部数值为虚构的模拟盘样例（A股、人民币元），仅用于前端界面开发与状态演示，
 * 不代表任何真实账户或真实行情，不得接入真实交易链路。
 * 数据快照：2026-10-29 15:00 收盘（UTC+8）。
 */

interface LabeledMetric {
  value: number
  label: string
}
interface EquityPoint {
  t: string
  v: number
}
interface Candidate {
  code: string
  name: string
  score: number
  bucket: 'observe' | 'plan'
  note?: string
}
interface PlanSummary {
  title: string
  lines: string[]
  nonRebalanceDay: boolean
}
interface Holding {
  code: string
  name: string
  qty: number
  cost: number
  last: number
  pnl: number
}
interface ClosedTrade {
  code: string
  name: string
  qty: number
  pnl: number
  closedAt: string
}
interface FeeBreakdown {
  commission: number
  stamp: number
  other: number
  total: number
}
interface Bar {
  t: string
  o: number
  h: number
  l: number
  c: number
  v: number
}
interface PricePoint {
  t: string
  price: number
}
interface InstrumentDetail {
  code: string
  name: string
  bars: Bar[]
  ma20: number[]
  fills: PricePoint[]
  plans: PricePoint[]
  explanation: string
}
interface RunRecord {
  id: string
  at: string
  result: 'completed' | 'partial' | 'failed'
  note: string
}
type CockpitStateKey = 'loading' | 'empty' | 'missing' | 'stale' | 'error' | 'unknown'
interface StateDemo {
  title: string
  message: string
}
interface TraderCockpitSample {
  meta: { source: string; asOf: string; currency: string; strategyLabel: string }
  metrics: {
    equity: LabeledMetric
    cash: LabeledMetric
    positionValue: LabeledMetric
    dayPnl: LabeledMetric
  }
  equitySeries: EquityPoint[]
  whyNoTrade: string[]
  candidates: Candidate[]
  planSummary: PlanSummary
  holdings: Holding[]
  closed: ClosedTrade[]
  fees: FeeBreakdown
  instrument: InstrumentDetail
  recentRuns: RunRecord[]
  stateDemos: Record<CockpitStateKey, StateDemo>
}

/** 简单移动平均：只输出窗口完整的值（长度 = values.length - window + 1）。 */
function rollingMa(values: number[], window: number): number[] {
  const out: number[] = []
  for (let i = window - 1; i < values.length; i++) {
    let sum = 0
    for (let j = i - window + 1; j <= i; j++) sum += values[j]
    out.push(Math.round((sum / window) * 1000) / 1000)
  }
  return out
}

/** 图表标的 600036 近 30 个交易日日线；末根收盘 39.85 与 holdings.last 对齐。 */
const instrumentBars: Bar[] = [
  { t: '2026-09-11T15:00:00+08:00', o: 38.26, h: 38.41, l: 38.21, c: 38.34, v: 24310000 },
  { t: '2026-09-14T15:00:00+08:00', o: 38.36, h: 38.52, l: 38.31, c: 38.45, v: 21860000 },
  { t: '2026-09-15T15:00:00+08:00', o: 38.47, h: 38.58, l: 38.42, c: 38.51, v: 23050000 },
  { t: '2026-09-16T15:00:00+08:00', o: 38.5, h: 38.53, l: 38.33, c: 38.4, v: 28740000 },
  { t: '2026-09-17T15:00:00+08:00', o: 38.41, h: 38.55, l: 38.37, c: 38.48, v: 22390000 },
  { t: '2026-09-18T15:00:00+08:00', o: 38.5, h: 38.7, l: 38.46, c: 38.62, v: 30120000 },
  { t: '2026-09-21T15:00:00+08:00', o: 38.63, h: 38.66, l: 38.48, c: 38.57, v: 25680000 },
  { t: '2026-09-22T15:00:00+08:00', o: 38.56, h: 38.59, l: 38.38, c: 38.44, v: 27450000 },
  { t: '2026-09-23T15:00:00+08:00', o: 38.45, h: 38.62, l: 38.41, c: 38.55, v: 23920000 },
  { t: '2026-09-24T15:00:00+08:00', o: 38.57, h: 38.75, l: 38.53, c: 38.68, v: 26830000 },
  { t: '2026-09-25T15:00:00+08:00', o: 38.66, h: 38.72, l: 38.3, c: 38.6, v: 29540000 },
  { t: '2026-09-28T15:00:00+08:00', o: 38.58, h: 38.71, l: 38.55, c: 38.66, v: 25170000 },
  { t: '2026-09-29T15:00:00+08:00', o: 38.65, h: 38.7, l: 38.48, c: 38.52, v: 28090000 },
  { t: '2026-09-30T15:00:00+08:00', o: 38.53, h: 38.75, l: 38.5, c: 38.71, v: 26360000 },
  { t: '2026-10-08T15:00:00+08:00', o: 38.72, h: 38.96, l: 38.68, c: 38.9, v: 35480000 },
  { t: '2026-10-09T15:00:00+08:00', o: 38.91, h: 39.02, l: 38.8, c: 38.85, v: 31220000 },
  { t: '2026-10-12T15:00:00+08:00', o: 38.86, h: 39.05, l: 38.81, c: 38.98, v: 27950000 },
  { t: '2026-10-13T15:00:00+08:00', o: 38.99, h: 39.18, l: 38.94, c: 39.12, v: 29860000 },
  { t: '2026-10-14T15:00:00+08:00', o: 39.11, h: 39.15, l: 38.92, c: 38.98, v: 33140000 },
  { t: '2026-10-15T15:00:00+08:00', o: 38.99, h: 39.12, l: 38.95, c: 39.08, v: 26720000 },
  { t: '2026-10-16T15:00:00+08:00', o: 39.09, h: 39.24, l: 39.01, c: 39.18, v: 28530000 },
  { t: '2026-10-19T15:00:00+08:00', o: 39.17, h: 39.2, l: 38.96, c: 39.02, v: 30410000 },
  { t: '2026-10-20T15:00:00+08:00', o: 39.03, h: 39.16, l: 38.98, c: 39.12, v: 27180000 },
  { t: '2026-10-21T15:00:00+08:00', o: 39.13, h: 39.3, l: 39.07, c: 39.24, v: 29060000 },
  { t: '2026-10-22T15:00:00+08:00', o: 39.25, h: 39.34, l: 39.12, c: 39.18, v: 28740000 },
  { t: '2026-10-23T15:00:00+08:00', o: 39.18, h: 39.22, l: 38.99, c: 39.05, v: 31590000 },
  { t: '2026-10-26T15:00:00+08:00', o: 39.06, h: 39.18, l: 38.96, c: 39.12, v: 26980000 },
  { t: '2026-10-27T15:00:00+08:00', o: 39.13, h: 39.27, l: 39.05, c: 39.23, v: 27620000 },
  { t: '2026-10-28T15:00:00+08:00', o: 39.22, h: 39.26, l: 39.07, c: 39.12, v: 25410000 },
  { t: '2026-10-29T15:00:00+08:00', o: 39.14, h: 39.92, l: 39.1, c: 39.85, v: 58960000 },
]

// ma20[0] 对应 bars[19]（20 日窗口首次完整），与 bars 末 11 根一一对应；前 19 根窗口不足，不填充。
const instrumentMa20: number[] = rollingMa(
  instrumentBars.map((b) => b.c),
  20,
)

export const traderCockpitSample: TraderCockpitSample = {
  meta: {
    source: 'SAMPLE',
    asOf: '2026-10-29T15:00:00+08:00',
    currency: 'CNY',
    strategyLabel: '确定性轮动样例',
  },
  metrics: {
    equity: { value: 50000.0, label: '权益' },
    cash: { value: 20755.0, label: '现金' },
    positionValue: { value: 29245.0, label: '持仓市值' },
    dayPnl: { value: 229.0, label: '当日盈亏' },
  },
  equitySeries: [
    { t: '2026-09-25T15:00:00+08:00', v: 49102.0 },
    { t: '2026-09-28T15:00:00+08:00', v: 49214.5 },
    { t: '2026-09-29T15:00:00+08:00', v: 49088.0 },
    { t: '2026-09-30T15:00:00+08:00', v: 49264.5 },
    { t: '2026-10-08T15:00:00+08:00', v: 49431.0 },
    { t: '2026-10-09T15:00:00+08:00', v: 49356.5 },
    { t: '2026-10-12T15:00:00+08:00', v: 49508.0 },
    { t: '2026-10-13T15:00:00+08:00', v: 49601.5 },
    { t: '2026-10-14T15:00:00+08:00', v: 49547.0 },
    { t: '2026-10-15T15:00:00+08:00', v: 49672.5 },
    { t: '2026-10-16T15:00:00+08:00', v: 49748.0 },
    { t: '2026-10-19T15:00:00+08:00', v: 49663.5 },
    { t: '2026-10-20T15:00:00+08:00', v: 49757.0 },
    { t: '2026-10-21T15:00:00+08:00', v: 49842.5 },
    { t: '2026-10-22T15:00:00+08:00', v: 49918.0 },
    { t: '2026-10-23T15:00:00+08:00', v: 49845.5 },
    { t: '2026-10-26T15:00:00+08:00', v: 49662.5 },
    { t: '2026-10-27T15:00:00+08:00', v: 49569.0 },
    { t: '2026-10-28T15:00:00+08:00', v: 49771.0 },
    { t: '2026-10-29T15:00:00+08:00', v: 50000.0 },
  ],
  whyNoTrade: [
    '今日为非调仓日：轮动窗口固定为每周一，周四仅监控不交易。',
    '候选榜首 510300 动量分 82，未达买入阈值 85。',
    '两只持仓（600036 / 601318）均未触发止损或止盈退出条件。',
    'JEV 快速决策全天输出 hold，未生成任何下单指令。',
    '盘后 15:00 决策窗口已收口，当日模拟执行通道关闭。',
  ],
  candidates: [
    { code: '510300', name: '沪深300ETF', score: 82, bucket: 'plan', note: '动量分榜首，距买入阈值 85 差 3 分' },
    { code: '512100', name: '中证1000ETF', score: 76, bucket: 'plan', note: '次选，主候选未达标时顶替' },
    { code: '600036', name: '招商银行', score: 71, bucket: 'plan', note: '已持有 400 股，持有加分维持' },
    { code: '601318', name: '中国平安', score: 63, bucket: 'observe', note: '已持有 300 股，浮亏但未触发止损' },
    { code: '512880', name: '证券ETF', score: 58, bucket: 'observe', note: '10-27 止盈离场，重新进入观察区' },
    { code: '588000', name: '科创50ETF', score: 52, bucket: 'observe', note: '低于阈值，暂不调仓' },
    { code: '159915', name: '创业板ETF', score: 47, bucket: 'observe', note: '连续两日走弱' },
    { code: '512690', name: '酒ETF', score: 41, bucket: 'observe', note: '动量转负，列入候选末位' },
  ],
  planSummary: {
    title: '确定性轮动 · 2026-10-29 计划摘要',
    lines: [
      '轮动窗口为每周一；今日（周四）为非调仓日，仅执行监控，不下发订单。',
      '组合与计划持仓一致：600036 招商银行 400 股、601318 中国平安 300 股。',
      '下一窗口（2026-11-02）若 510300 动量分 ≥ 85，计划以不超过 30% 权益买入。',
      '硬约束：单票 ≤ 40% 权益，组合日内回撤 ≥ 2% 触发 Guard 复核；所有执行仅限模拟盘。',
      '今日无新增订单费用；本月累计费用 73.01 元。',
    ],
    nonRebalanceDay: true,
  },
  holdings: [
    { code: '600036', name: '招商银行', qty: 400, cost: 38.5, last: 39.85, pnl: 540.0 },
    { code: '601318', name: '中国平安', qty: 300, cost: 45.2, last: 44.35, pnl: -255.0 },
  ],
  closed: [
    { code: '512880', name: '证券ETF', qty: 2000, pnl: 320.5, closedAt: '2026-10-27T14:57:30+08:00' },
  ],
  fees: { commission: 25.34, stamp: 41.55, other: 6.12, total: 73.01 },
  instrument: {
    code: '600036',
    name: '招商银行',
    bars: instrumentBars,
    ma20: instrumentMa20,
    fills: [
      { t: '2026-09-25T09:47:00+08:00', price: 38.36 },
      { t: '2026-09-28T10:15:00+08:00', price: 38.64 },
    ],
    plans: [
      { t: '2026-09-24T20:05:00+08:00', price: 38.5 },
      { t: '2026-10-26T09:30:00+08:00', price: 35.8 },
      { t: '2026-10-26T09:30:00+08:00', price: 43.2 },
      { t: '2026-10-29T09:31:00+08:00', price: 40.0 },
    ],
    explanation:
      '样例数据：招商银行（600036）近 30 个交易日日线与 MA20。圆点为模拟成交（09-25 买入 200 股 @38.36、09-28 加买 200 股 @38.64，均价 38.50）；虚线为 StrategyPlan 计划价（38.50 建仓参考、35.80 止损、43.20 止盈、40.00 加仓观察）。价格沿 MA20 上方运行，10-29 放量上攻但最高 39.92 未触及 40.00 观察线，当日未产生新订单。',
  },
  recentRuns: [
    {
      id: 'run-20261029-1500-a3f2',
      at: '2026-10-29T15:00:05+08:00',
      result: 'completed',
      note: '盘后运行：生成轮动计划，判定今日不调仓；无订单产出。',
    },
    {
      id: 'run-20261029-1130-b7c1',
      at: '2026-10-29T11:31:12+08:00',
      result: 'completed',
      note: '午间复核：候选分未达阈值，维持持仓；JEV 输出 hold。',
    },
    {
      id: 'run-20261029-0935-c5d9',
      at: '2026-10-29T09:35:40+08:00',
      result: 'completed',
      note: '开盘检查：行情与账户就绪，510300 进入 plan 候选。',
    },
    {
      id: 'run-20261028-1500-d2e8',
      at: '2026-10-28T15:00:21+08:00',
      result: 'partial',
      note: '行情源延迟约 2 分钟，账户快照正常；计划照常生成。',
    },
    {
      id: 'run-20261027-1457-e9a4',
      at: '2026-10-27T14:57:03+08:00',
      result: 'completed',
      note: '触发 512880 止盈卖出 2000 股，模拟成交完成，盈亏 +320.50。',
    },
  ],
  stateDemos: {
    loading: {
      title: '数据加载中',
      message: '正在拉取行情与账户快照，请稍候…',
    },
    empty: {
      title: '暂无候选',
      message: '当前策略未产出任何候选标的，请确认 StrategyPlan 是否已生成。',
    },
    missing: {
      title: '数据缺失',
      message: '未找到今日的 StrategyPlan 记录，可能计划尚未运行或 ID 不存在。',
    },
    stale: {
      title: '数据过期',
      message: '行情快照最后更新于 14:58，已超过 5 分钟未刷新，请检查数据源。',
    },
    error: {
      title: '加载失败',
      message: '请求 /api/cockpit/summary 返回 503，JEV 决策通道暂不可用。',
    },
    unknown: {
      title: '状态未知',
      message: '该运行记录没有最终结论（可能仍在执行或被中断），请勿据此判断交易结果。',
    },
  },
}
