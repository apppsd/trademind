import { describe, expect, it } from 'vitest'
import {
  buildTradeMindPane, buildTraderPane, observedNow, traderBaseUrl,
} from '@/adapters/opsReadonly'
import type { Envelope } from '@/types/common'
import type { ActivityPage, OverviewData, RuntimeData } from '@/types/workbench'

function envelope<T>(data: T, extra: Partial<Envelope<T>> = {}): Envelope<T> {
  return {
    schema_version: 1, account_alias: 'paper-t0-local-1k', as_of: '2026-10-30T10:00:00+08:00',
    generated_at: '2026-10-30T10:00:01+08:00', data_status: 'ok', source: 'workbench',
    age_seconds: 0, stale_after_seconds: 60, partial: false, missing_reasons: [], data, ...extra,
  }
}
const RUNTIME = { supervisor: null, intraday_health: null, observer: null, kill_switch: null, model_budget_today: null, capabilities: { controls_available: false, observer_read_only: true } } as unknown as RuntimeData
const OVERVIEW: OverviewData = {
  account: { cash: '10.00', reserved_cash: null, available_cash: '9.00', equity: '11.00', valuation_complete: false, positions_count: 0 },
  today: null, runtime_brief: { phase: null, halted: null, heartbeat_age_seconds: null },
}
const EMPTY_PAGE: ActivityPage = { kind: 'decision', items: [], next_cursor: null, has_more: false }

describe('trader pane builder (frozen contract mapping)', () => {
  const STATUS = {
    current_time: '2026-10-30T10:00:00+08:00',
    worker: { healthy: true, details: { raw: 'x' } },
    database: { connected: true, error: null },
    calendar: { market_session: 'closed' },
    data: { path: 'D:/secret.csv', exists: true, size_bytes: 1, last_modified: '2026-10-30T08:00:00+08:00' },
  }

  it('maps ok account/plan per contract, keeps strings, and never leaks data.path or details', () => {
    const pane = buildTraderPane(
      STATUS,
      { ok: true, account_id: 'shadow-1', cash: '100.00', total_equity: '200.00', total_fees: '3.00', valuation_status: 'pending', open_positions_count: 2, positions_count: 5, external_flows_status: 'unsupported', updated_at: '2026-10-30T07:00:00+08:00' },
      { has_plan: true, plan_id: 'p-9', generated_at: '2026-10-30T06:00:00+08:00', decision_date: '2026-10-31', actionable: true, lifecycle: { state: 'active' }, cash_weight: 0.1 },
      '2026-10-30T10:00:05+08:00',
    )
    expect(pane.sample).toBe(false)
    expect(pane.project).toBe('trader')
    expect(pane.host).toBeNull() // 主机未核实，不从 URL 推断
    expect(pane.account).toBe('shadow-1')
    expect(pane.run).toBeNull() // plan_id 不是 run
    expect(pane.data_as_of).toBe('2026-10-30T07:00:00+08:00') // 账本时点，而非 current_time
    expect(pane.data_status).toBe('ok')
    const plan = pane.nodes.find(n => n.label === '计划')!
    expect(plan.data_status).toBe('ok')
    expect(plan.data_as_of).toBe('2026-10-30T06:00:00+08:00')
    expect(plan.detail).not.toContain('cash_weight') // 比例不转现金值
    // 未脱敏字段不进任何展示字段
    const flat = JSON.stringify(pane)
    expect(flat).not.toContain('D:/secret.csv')
    expect(flat).not.toContain('raw')
    // 盯市 pending → valuation_complete false；external_flows unsupported 说明保留
    expect(pane.account_glass?.valuation_complete).toBe(false)
    expect(pane.account_glass?.note).toContain('外部资金流')
    // 无已验证事件源：成功空，不伪造脉冲
    expect(pane.events).toEqual([])
    // 边只在窗内节点间，静态配置依赖
    for (const edge of pane.edges) {
      expect(pane.nodes.some(n => n.id === edge.from)).toBe(true)
      expect(pane.nodes.some(n => n.id === edge.to)).toBe(true)
      expect(edge.relation).toBe('config_dependency')
    }
  })

  it('drops the ok=false zero placeholders entirely: amounts unknown, account block missing, no fake zero cash', () => {
    const pane = buildTraderPane(
      STATUS,
      { ok: false, error: 'db down', account_id: 'shadow-1', cash: '0.00', total_equity: '0.00', total_fees: '0.00', valuation_status: 'valued' },
      { has_plan: false },
      '2026-10-30T10:00:05+08:00',
    )
    // GET 均成功 → 窗 ok；但账户块整体按 missing：身份与金额全部 null
    expect(pane.data_status).toBe('ok')
    expect(pane.account).toBeNull()
    expect(pane.account_glass).toMatchObject({ equity: null, cash: null, fees: null })
    expect(pane.account_glass?.note).toContain('零占位')
    expect(pane.notes?.join('；')).toContain('missing')

    expect(JSON.stringify(pane)).not.toContain('0.00')
    const plan = pane.nodes.find(n => n.label === '计划')!
    expect(plan.data_status).toBe('empty') // has_plan=false 是成功空，不是错误
  })

  it('marks data-refresh missing without last_modified and keeps unwired nodes unknown', () => {
    const pane = buildTraderPane(
      { current_time: 't', worker: { healthy: null }, database: {}, calendar: {}, data: {} },
      { ok: true, account_id: 'a', updated_at: 'u' },
      { has_plan: false },
      '2026-10-30T10:00:05+08:00',
    )
    expect(pane.nodes.find(n => n.label === '数据刷新')!.data_status).toBe('missing')
    expect(pane.nodes.find(n => n.label === '质量')!.data_status).toBe('unknown')
    expect(pane.nodes.find(n => n.label === '因子')!.data_status).toBe('missing')
    expect(pane.nodes.find(n => n.label === 'OMS')!.data_status).toBe('unknown')
  })
})

describe('TradeMind pane builder (workbench envelopes → ops.v1)', () => {
  const snaps = {
    runtime: envelope(RUNTIME),
    overview: envelope(OVERVIEW),
    strategies: envelope({ plans: [{ plan_id: 'p-1' }] }),
    decisions: envelope({ ...EMPTY_PAGE, kind: 'decision' as const, items: [
      { id: 'd-1', instrument_id: '600000.SH', action: 'BUY', status: 'DECIDED', created_at: '2026-10-30T09:59:00+08:00' },
    ] }),
    fills: envelope({ ...EMPTY_PAGE, kind: 'fill' as const }),
  }

  it('builds a live sample:false envelope with frozen keys, per-node statuses and bounded events', () => {
    const pane = buildTradeMindPane(snaps, '2026-10-30T10:00:05+08:00')
    expect(pane.sample).toBe(false)
    expect(pane.project).toBe('TradeMind')
    expect(pane.account).toBe('paper-t0-local-1k')
    expect(pane.host).toBeNull()
    expect(pane.run).toBeNull()
    const keys = ['project', 'host', 'account', 'run', 'source', 'schema_version', 'data_as_of', 'observed_at', 'data_status', 'evidence_ref']
    for (const record of [...pane.nodes, ...pane.edges, ...pane.events]) keys.forEach(k => expect(record).toHaveProperty(k))
    expect(pane.nodes.find(n => n.label === 'StrategyPlan')!.data_status).toBe('ok')
    expect(pane.nodes.find(n => n.label === 'OMS')!.data_status).toBe('empty')
    expect(pane.nodes.find(n => n.label === 'JEV')!.data_status).toBe('unknown')
    expect(pane.events).toHaveLength(1)
    expect(pane.events[0]!.data_as_of).toBe('2026-10-30T09:59:00+08:00')
    expect(pane.events[0]!.evidence_ref).toBe('workbench:activity/decision/d-1')
    // 账户玻璃舱：保留字符串金额，unknown 不补零
    expect(pane.account_glass?.equity).toBe('11.00')
    expect(pane.account_glass?.cash).toBe('9.00')
    expect(pane.account_glass?.valuation_complete).toBe(false)
  })

  it('sorts events desc, caps at 6, and escalates envelope status (stale/unavailable/error, partial → notes)', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ id: `d-${i}`, created_at: `2026-10-30T09:5${i}:00+08:00` }))
    const pane = buildTradeMindPane({
      ...snaps,
      decisions: envelope({ ...EMPTY_PAGE, items: many }, { data_status: 'partial', missing_reasons: ['today 日报缺失'] }),
      fills: envelope({ ...EMPTY_PAGE, kind: 'fill' as const }, { data_status: 'stale' }),
    }, '2026-10-30T10:00:05+08:00')
    expect(pane.events).toHaveLength(6)
    expect(pane.data_status).toBe('stale') // stale 严重度高于 partial→ok
    expect(pane.notes?.join('；')).toContain('today 日报缺失')
    const times = pane.events.map(e => e.data_as_of ?? '')
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('maps unavailable to missing and error to error', () => {
    const pane = buildTradeMindPane({ ...snaps, overview: envelope(OVERVIEW, { data_status: 'unavailable' }) }, '2026-10-30T10:00:05+08:00')
    expect(pane.data_status).toBe('missing')
    const pane2 = buildTradeMindPane({ ...snaps, runtime: envelope(RUNTIME, { data_status: 'error' }) }, '2026-10-30T10:00:05+08:00')
    expect(pane2.data_status).toBe('error')
  })
})

describe('adapter config', () => {
  it('observedNow is Shanghai ISO8601 with explicit offset', () => {
    expect(observedNow(new Date('2026-10-30T02:00:05Z'))).toBe('2026-10-30T10:00:05+08:00')
  })
  it('trader base comes from localStorage first, strips trailing slash, defaults empty', () => {
    expect(traderBaseUrl()).toBe('')
    localStorage.setItem('ops-trader-base', 'http://127.0.0.1:8080/')
    expect(traderBaseUrl()).toBe('http://127.0.0.1:8080')
  })
})
