/** 固定脱敏样例；不代表 Wang/150 的运行状态，不调用任何 API。 */
export type OpsStatus = 'ok' | 'empty' | 'missing' | 'stale' | 'error' | 'unknown' | 'loading'
export interface OpsIdentity {
  project: 'trader' | 'TradeMind'
  host: string | null
  account: string | null
  run: string | null
  source: string
  schema_version: 'ops.v1'
  data_as_of: string | null
  observed_at: string
  data_status: OpsStatus
  evidence_ref: string | null
  /** true=固定样例；false=白名单 GET 的实时只读信封（adapters/opsReadonly）。 */
  sample: boolean
}
export interface OpsNode extends OpsIdentity { id: string; label: string; detail: string }
export interface OpsEdge extends OpsIdentity {
  id: string; from: string; to: string; relation: 'config_dependency'
}
export interface OpsEvent extends OpsIdentity { id: string; node_id: string; label: string }
/** 账户玻璃舱只读数值；金额保留源字符串与单位，null=unknown（绝不用 0 补位）。 */
export interface OpsAccountGlass {
  equity: string | null
  cash: string | null
  fees: string | null
  valuation_complete: boolean | null
  as_of: string | null
  note?: string
}
export interface OpsPane extends OpsIdentity {
  subtitle: string; nodes: OpsNode[]; edges: OpsEdge[]; events: OpsEvent[]
  /** 实时窗格的服务端 missing_reasons，或样例窗格的回退原因说明。 */
  notes?: string[]
  /** 实时窗格账户数值；SAMPLE 不携带，玻璃舱保持“—”。 */
  account_glass?: OpsAccountGlass
}
export const statusLabels: Record<OpsStatus, string> = {
  ok: '成功', empty: '成功空', missing: '缺失', stale: '陈旧',
  error: '错误', unknown: '未知', loading: '加载中',
}
function makePane(project: OpsIdentity['project'], labels: string[], subtitle: string): OpsPane {
  const identity: OpsIdentity = {
    project, host: 'Wang-SAMPLE', account: `${project}-SAMPLE-account`,
    run: `${project}-SAMPLE-run`, source: `sample:${project}`, schema_version: 'ops.v1',
    data_as_of: '2026-10-30T10:00:00+08:00', observed_at: '2026-10-30T10:00:05+08:00',
    data_status: 'ok', evidence_ref: `SAMPLE:${project}:snapshot-1`, sample: true,
  }
  const nodes = labels.map((label, i): OpsNode => ({
    ...identity, id: `${project}-node-${i}`, label,
    detail: `SAMPLE / 样例 · ${label}配置依赖展示，不证明该步骤已执行。`,
    evidence_ref: `SAMPLE:${project}:node-${i}`,
  }))
  return {
    ...identity, subtitle, nodes,
    edges: nodes.slice(1).map((node, i) => ({
      ...identity, id: `${project}-edge-${i}`, from: nodes[i]!.id, to: node.id,
      relation: 'config_dependency', evidence_ref: null,
    })),
    events: [0, 1, 3].map((i) => ({
      ...identity, id: `${project}-event-${i}`, node_id: nodes[i]!.id,
      label: `SAMPLE · ${labels[i]}样例记录（无跨节点 trace）`,
      evidence_ref: `SAMPLE:${project}:event-${i}`,
    })),
  }
}
export const opsStreamSample: readonly OpsPane[] = [
  makePane('trader', ['数据刷新', '质量', '因子', '计划', '风险', 'OMS', 'paper'], 'LEFT / 确定性策略与运行履历'),
  makePane('TradeMind', ['行情', 'StrategyPlan', 'JEV', 'Guard', 'OMS', 'paper'], 'RIGHT / 低频计划与快速决策'),
]
