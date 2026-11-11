// 统一驾驶舱只读适配层（TASK-20261030-05）。
// 契约：docs/research/ops-cockpit-readonly-contract-v1.md（ops.v1 展示信封）。
// 边界：仅白名单 GET、挂载/手动刷新各一次，无轮询（轮询与退避归 TASK-06）。
// 任何请求失败 → 返回 null，由页面按窗格独立回退 SAMPLE；绝不合并两项目
// 账本/预算，不伪造 trace 或事件；金额保留源字符串单位；trader account 的
// ok=false 零占位必须整体丢弃并将金额置 null，不得显示为真实零现金。
import { fetchEnvelope, fetchJson } from '../services/client'
import type { Envelope } from '../types/common'
import type { ActivityItem, ActivityKind, ActivityPage, OverviewData, PlanRow, RuntimeData } from '../types/workbench'
import type { OpsAccountGlass, OpsEvent, OpsNode, OpsPane, OpsStatus } from '../demo/opsStreamSample'

/** trader 固定基础地址：localStorage 优先（ops-trader-base），其次构建期环境变量。
 *  默认空 → 左窗保持 SAMPLE，绝不硬编码远程主机。 */
export function traderBaseUrl(): string {
  let stored: string | null = null
  try { stored = localStorage.getItem('ops-trader-base') } catch { /* 存储禁用时仅看环境变量 */ }
  const raw = stored ?? (import.meta.env.VITE_OPS_TRADER_BASE as string | undefined) ?? ''
  return raw.trim().replace(/\/+$/, '')
}

/** 观察时点：适配器读取时刻，上海时区 ISO8601（Wang/150 运营口径 UTC+8）。 */
export function observedNow(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hourCycle: 'h23', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(now)
  const get = (type: string) => parts.find(p => p.type === type)?.value ?? '00'
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}+08:00`
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null)
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)

/** workbench 信封 data_status → ops.v1 展示状态；partial 视为成功但记入 notes。 */
const WORKBENCH_STATUS: Record<string, OpsStatus> = {
  ok: 'ok', partial: 'ok', stale: 'stale', unavailable: 'missing', error: 'error',
}
const SEVERITY: Record<OpsStatus, number> = {
  ok: 0, loading: 0, empty: 0, unknown: 1, stale: 2, missing: 3, error: 4,
}

function paneStatus(envelopes: Envelope<unknown>[]): { status: OpsStatus; notes: string[] } {
  let status: OpsStatus = 'ok'
  const notes: string[] = []
  for (const env of envelopes) {
    const mapped = WORKBENCH_STATUS[env.data_status] ?? 'unknown'
    if (SEVERITY[mapped] > SEVERITY[status]) status = mapped
    notes.push(...env.missing_reasons.map(reason => `${reason}（${env.source}）`))
  }
  return { status, notes }
}

function makeIdentity(project: 'trader' | 'TradeMind', source: string, observedAt: string) {
  return {
    project, host: null, account: null, run: null, source, schema_version: 'ops.v1' as const,
    data_as_of: null, observed_at: observedAt, data_status: 'ok' as OpsStatus,
    evidence_ref: null, sample: false as const,
  }
}

function chainEdges(
  identity: ReturnType<typeof makeIdentity>, nodes: OpsNode[], project: string,
): OpsPane['edges'] {
  return nodes.slice(1).map((node, i) => ({
    ...identity, id: `${project}-edge-${i}`, from: nodes[i]!.id, to: node.id,
    relation: 'config_dependency' as const, evidence_ref: null,
  }))
}

// ---------------------------------------------------------------------------
// 右窗 TradeMind：/api/workbench 白名单 GET（同源 observer/workbench 已启动时可用）
// ---------------------------------------------------------------------------

export const TRADEMIND_SOURCE = 'get:/api/workbench/{runtime,overview,strategies,activity}'

export interface WorkbenchSnapshots {
  runtime: Envelope<RuntimeData>
  overview: Envelope<OverviewData>
  strategies: Envelope<{ plans: PlanRow[] }>
  decisions: Envelope<ActivityPage>
  fills: Envelope<ActivityPage>
}

export async function loadTradeMindPane(signal?: AbortSignal): Promise<OpsPane | null> {
  try {
    const opts = { signal }
    const [runtime, overview, strategies, decisions, fills] = await Promise.all([
      fetchEnvelope<RuntimeData>('/api/workbench/runtime', opts),
      fetchEnvelope<OverviewData>('/api/workbench/overview', opts),
      fetchEnvelope<{ plans: PlanRow[] }>('/api/workbench/strategies?limit=10', opts),
      fetchEnvelope<ActivityPage>('/api/workbench/activity?kind=decision&limit=3', opts),
      fetchEnvelope<ActivityPage>('/api/workbench/activity?kind=fill&limit=3', opts),
    ])
    return buildTradeMindPane({ runtime, overview, strategies, decisions, fills }, observedNow())
  } catch {
    return null // 任一白名单 GET 失败 → 该窗回退 SAMPLE，不拼半真半假数据
  }
}

/** 纯构建函数（可单测）：workbench 快照 → ops.v1 实时信封。 */
export function buildTradeMindPane(snaps: WorkbenchSnapshots, observedAt: string): OpsPane {
  const { status, notes } = paneStatus([snaps.runtime, snaps.overview, snaps.strategies, snaps.decisions, snaps.fills])
  const identity = makeIdentity('TradeMind', TRADEMIND_SOURCE, observedAt)
  const plans = Array.isArray(snaps.strategies.data?.plans) ? snaps.strategies.data.plans : null
  const decisionItems = Array.isArray(snaps.decisions.data?.items) ? snaps.decisions.data.items : []
  const fillItems = Array.isArray(snaps.fills.data?.items) ? snaps.fills.data.items : []
  const account = snaps.overview.data?.account ?? null

  const nodeSpecs: { label: string; dataStatus: OpsStatus; asOf: string | null; detail: string }[] = [
    { label: '行情', dataStatus: 'unknown', asOf: null, detail: '行情快照未纳入本轮白名单 GET（TASK-06 接线）；unknown 不代表中断。' },
    { label: 'StrategyPlan',
      dataStatus: plans === null ? 'missing' : plans.length === 0 ? 'empty' : 'ok',
      asOf: snaps.strategies.as_of,
      detail: `workbench strategies 只读快照：${plans === null ? '字段缺失' : `${plans.length} 条计划（服务端限条10）`}。` },
    { label: 'JEV', dataStatus: 'unknown', asOf: null, detail: 'JEV 决策无独立只读端点入本轮白名单；缺证据不补造。' },
    { label: 'Guard', dataStatus: 'unknown', asOf: null, detail: 'Guard 否决记录未纳入本轮白名单 GET；缺证据不补造。' },
    { label: 'OMS', dataStatus: fillItems.length ? 'ok' : 'empty', asOf: snaps.fills.as_of,
      detail: `activity fill 只读证据：${fillItems.length} 条（服务端限条3）。` },
    { label: 'paper', dataStatus: account ? 'ok' : 'missing', asOf: snaps.overview.as_of,
      detail: '账户玻璃舱来自 workbench overview 只读快照，服务端固定账户。' },
  ]
  const nodes: OpsNode[] = nodeSpecs.map((spec, i) => ({
    ...identity, id: `TradeMind-node-${i}`, label: spec.label,
    detail: `LIVE · ${spec.detail}`,
    data_status: spec.dataStatus, data_as_of: spec.asOf,
    evidence_ref: `workbench:/api/workbench/${spec.label === 'OMS' ? 'activity?kind=fill' : spec.label === 'paper' ? 'overview' : spec.label === 'StrategyPlan' ? 'strategies' : 'runtime'}`,
  }))

  const events = mergeEvents([
    ...toEvents(decisionItems, 'decision', 'TradeMind-node-1', identity, snaps.decisions.as_of),
    ...toEvents(fillItems, 'fill', 'TradeMind-node-4', identity, snaps.fills.as_of),
  ])
  const glass: OpsAccountGlass | null = account ? {
    equity: account.equity, cash: account.available_cash,
    fees: snaps.overview.data?.today?.model_cost_settled ?? null,
    valuation_complete: account.valuation_complete, as_of: snaps.overview.as_of,
    note: '费用=日报已结算模型成本口径，预留≠结算；金额为服务端字符串，null=unknown。',
  } : null

  return {
    ...identity, subtitle: 'RIGHT / 低频计划与快速决策',
    data_status: status, data_as_of: snaps.runtime.as_of ?? null,
    account: snaps.overview.account_alias || null, notes,
    account_glass: glass ?? undefined,
    nodes, edges: chainEdges(identity, nodes, 'TradeMind'), events,
  }
}

function toEvents(
  items: ActivityItem[], kind: ActivityKind, nodeId: string,
  identity: ReturnType<typeof makeIdentity>, envelopeAsOf: string | null,
): OpsEvent[] {
  return items.map((item, i) => {
    const id = str(item.id)
    const who = str(item.instrument_id) ?? ''
    const verb = kind === 'decision'
      ? `决策 ${str(item.action) ?? str(item.side) ?? ''} ${str(item.status) ?? ''}`.trim()
      : `成交 ${str(item.side) ?? ''} ${str(item.filled_quantity) ?? str(item.quantity) ?? ''}股`.trim()
    return {
      ...identity, id: `TradeMind-event-${kind}-${id ?? i}`, node_id: nodeId,
      label: `${verb} ${who}`.trim(),
      data_as_of: str(item.executed_at) ?? str(item.created_at) ?? envelopeAsOf,
      data_status: 'ok',
      evidence_ref: id ? `workbench:activity/${kind}/${id}` : null,
    }
  })
}

function mergeEvents(events: OpsEvent[]): OpsEvent[] {
  return [...events]
    .sort((a, b) => (b.data_as_of ?? '').localeCompare(a.data_as_of ?? ''))
    .slice(0, 6)
}

// ---------------------------------------------------------------------------
// 左窗 trader：仅当用户配置固定基础地址时尝试（localStorage ops-trader-base /
// 构建期 VITE_OPS_TRADER_BASE）；默认空 → 保持 SAMPLE。固定 /api/* 端点（ETF/主
// 账户口径），不混用 /api/stock/*；run-ledger 条目字段未经 TASK-06 验证，本轮
// 不映射事件脉冲，事件区为成功空。
// ---------------------------------------------------------------------------

export const TRADER_SOURCE = 'trader-get:/api/{status,account,plan}'

export async function loadTraderPane(signal?: AbortSignal): Promise<OpsPane | null> {
  const base = traderBaseUrl()
  if (!base) return null // 未配置固定主机：不 fetch，左窗保持 SAMPLE
  try {
    const opts = { signal, timeoutMs: 6000 }
    const [status, account, plan] = await Promise.all([
      fetchJson<Record<string, unknown>>(`${base}/api/status`, opts),
      fetchJson<Record<string, unknown>>(`${base}/api/account`, opts),
      fetchJson<Record<string, unknown>>(`${base}/api/plan`, opts),
    ])
    return buildTraderPane(status, account, plan, observedNow())
  } catch {
    return null
  }
}

/** 纯构建函数（可单测）：trader 三个只读 GET → ops.v1 实时信封。
 *  字段映射严格按冻结合同；只透传白名单字段，不透传 data.path 或未脱敏 details/error。 */
export function buildTraderPane(
  status: Record<string, unknown>,
  account: Record<string, unknown>,
  plan: Record<string, unknown>,
  observedAt: string,
): OpsPane {
  const identity = makeIdentity('trader', TRADER_SOURCE, observedAt)
  const worker = (status.worker ?? {}) as Record<string, unknown>
  const database = (status.database ?? {}) as Record<string, unknown>
  const calendar = (status.calendar ?? {}) as Record<string, unknown>
  const data = (status.data ?? {}) as Record<string, unknown>
  const workerHealthy = bool(worker.healthy)
  const dbConnected = bool(database.connected)
  const session = str(calendar.market_session)
  const lastModified = str(data.last_modified)

  const accountOk = account.ok === true
  const accountAsOf = accountOk ? str(account.updated_at) : null
  const glass: OpsAccountGlass = accountOk ? {
    equity: str(account.total_equity),
    cash: str(account.cash),
    fees: str(account.total_fees),
    valuation_complete: str(account.valuation_status) === 'valued' ? true : str(account.valuation_status) === 'pending' ? false : null,
    as_of: accountAsOf,
    note: str(account.external_flows_status) === 'unsupported'
      ? '外部资金流 unsupported：不宣称收益已扣净外部流；数量为源字符串，null=unknown。'
      : '金额为源字符串与源单位，null=unknown。',
  } : {
    equity: null, cash: null, fees: null, valuation_complete: null, as_of: null,
    note: '源 ok=false：源零占位已整体丢弃，金额 unknown——读取失败不是零现金账户。',
  }

  const hasPlan = plan.has_plan === true
  const planDetail = hasPlan
    ? `plan_id 仅作计划证据非 run；生成时点 ${str(plan.generated_at) ?? 'unknown'}；` +
      `decision_date ${str(plan.decision_date) ?? 'unknown'}（非精确时间戳）；` +
      `生成时 actionable=${bool(plan.actionable) === true ? 'true' : bool(plan.actionable) === false ? 'false' : 'unknown'}，与当前生命周期分开。`
    : 'has_plan=false：成功读取后无计划，成功空不是错误。'

  const nodeSpecs: { label: string; dataStatus: OpsStatus; asOf: string | null; detail: string }[] = [
    { label: '数据刷新', dataStatus: lastModified ? 'ok' : 'missing', asOf: lastModified,
      detail: `数据文件修改时间 ${lastModified ?? 'missing'}（文件 mtime 非行情时间）；` +
        `worker ${workerHealthy === null ? 'unknown' : workerHealthy ? '存活' : '异常'} · ` +
        `DB ${dbConnected === null ? 'unknown' : dbConnected ? '连通' : '断开'} · ` +
        `时段 ${session ?? 'unknown'}。健康不等于交易就绪。` },
    { label: '质量', dataStatus: 'unknown', asOf: null,
      detail: 'data-verification 集合端点限窗未经验证，本轮不接入（TASK-06 处置）。' },
    { label: '因子', dataStatus: 'missing', asOf: null,
      detail: '计划已读取但无因子证据字段，按合同标 missing，不补造。' },
    { label: '计划', dataStatus: hasPlan ? 'ok' : 'empty', asOf: hasPlan ? str(plan.generated_at) : null,
      detail: planDetail },
    { label: '风险', dataStatus: 'unknown', asOf: null,
      detail: 'risk-rejections 后端限条/历史窗未验，本轮不接入；非可编辑风控。' },
    { label: 'OMS', dataStatus: 'unknown', asOf: null,
      detail: 'orders/trades/reconciliation 成本未验，本轮不接入（TASK-06 处置）。' },
    { label: 'paper', dataStatus: 'unknown', asOf: null,
      detail: 'run-ledger 条目字段待 TASK-06 定向验证，不映射事件脉冲、不冒充 paper-runs。' },
  ]
  const nodes: OpsNode[] = nodeSpecs.map((spec, i) => ({
    ...identity, id: `trader-node-${i}`, label: spec.label,
    detail: `LIVE · ${spec.detail}`,
    data_status: spec.dataStatus, data_as_of: spec.asOf,
    evidence_ref: `trader:${['status', 'unknown', 'plan', 'plan', 'unknown', 'unknown', 'run-ledger'][i]}`,
  }))

  // 三个 GET 均成功 → 窗格本身 ok；account ok=false 时按合同将账户块整体归
  // missing：金额/身份全部置 null + 说明（notes 与玻璃舱 note），不显示成真实零现金。
  const paneStatusValue: OpsStatus = 'ok'
  return {
    ...identity, subtitle: 'LEFT / 确定性策略与运行履历',
    data_status: paneStatusValue,
    data_as_of: accountAsOf, // 账本更新时点为最可信事实时间；current_time 是服务观察时间不作数据时间
    account: accountOk ? str(account.account_id) : null,
    account_glass: glass,
    notes: accountOk ? [] : ['trader account ok=false：账户读取整体按 missing 处理，金额全部置 null，源零占位已丢弃。'],
    nodes, edges: chainEdges(identity, nodes, 'trader'),
    events: [], // 无已验证事件源：成功空，不伪造脉冲
  }
}
