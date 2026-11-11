import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import OpsStreamPage from '@/pages/OpsStreamPage.vue'
import WorkbenchLayout from '@/layouts/WorkbenchLayout.vue'
import { router } from '@/router'
import { apiOnline, accountAlias } from '@/services/online'
import { opsStreamSample } from '@/demo/opsStreamSample'

type FetchMock = ReturnType<typeof vi.fn>

/** 断言本页只对同源 /api/workbench/* 白名单发起 GET，绝不触 /api/control、远程主机或 POST。 */
function assertWhitelistGetOnly(mock: FetchMock) {
  const calls = mock.mock.calls as [RequestInfo | URL, RequestInit | undefined][]
  expect(calls.length).toBeGreaterThan(0)
  for (const [input, init] of calls) {
    const url = String(input)
    expect(url).toMatch(/^\/api\/workbench\//)
    expect(url).not.toContain('/api/control')
    expect(init?.method ?? 'GET').toBe('GET')
  }
}

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) }
}
function envelope(data: unknown, extra: Record<string, unknown> = {}) {
  return {
    schema_version: 1, account_alias: 'paper-t0-local-1k', as_of: '2026-10-30T10:00:00+08:00',
    generated_at: '2026-10-30T10:00:01+08:00', data_status: 'ok', source: 'workbench',
    age_seconds: 0, stale_after_seconds: 60, partial: false, missing_reasons: [], data, ...extra,
  }
}
const RUNTIME_DATA = {
  supervisor: { running: true }, intraday_health: null,
  observer: { running: true, pid: 1, heartbeat_age_seconds: 3 },
  kill_switch: null, model_budget_today: { settled_today: '1.00', pending_today: null },
  capabilities: { controls_available: false, observer_read_only: true },
}
const OVERVIEW_DATA = {
  account: { cash: '4000.00', reserved_cash: '100.00', available_cash: '3900.00', equity: '5000.00', valuation_complete: true, positions_count: 1 },
  today: { trading_pnl: null, realized_pnl: null, trading_fees: null, net_external_cash_flow: null, net_after_settled_model_estimate: null, model_cost_settled: '2.50', model_cost_pending: null, report_period_status: null, report_generated_at: null },
  runtime_brief: { phase: null, halted: false, heartbeat_age_seconds: 3 },
}
function stubWorkbenchLive() {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/workbench/runtime') return jsonResponse(envelope(RUNTIME_DATA))
    if (url === '/api/workbench/overview') return jsonResponse(envelope(OVERVIEW_DATA))
    if (url === '/api/workbench/strategies?limit=10') return jsonResponse(envelope({ plans: [{ plan_id: 'p-1' }] }))
    if (url === '/api/workbench/activity?kind=decision&limit=3') {
      return jsonResponse(envelope({ kind: 'decision', next_cursor: null, has_more: false, items: [
        { id: 'd-1', instrument_id: '600000.SH', action: 'BUY', status: 'DECIDED', created_at: '2026-10-30T09:59:00+08:00' },
      ] }))
    }
    if (url === '/api/workbench/activity?kind=fill&limit=3') {
      return jsonResponse(envelope({ kind: 'fill', next_cursor: null, has_more: false, items: [] }))
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', mock)
  return mock
}

describe('Ops Stream readonly cockpit', () => {
  let mediaListener: (() => void) | undefined
  let media: { matches: boolean; addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> }
  const wrappers: ReturnType<typeof mount>[] = []
  beforeEach(() => {
    localStorage.clear()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    media = {
      matches: false,
      addEventListener: vi.fn((_name, listener) => { mediaListener = listener }),
      removeEventListener: vi.fn(),
    }
    vi.stubGlobal('matchMedia', vi.fn(() => media))
    vi.stubGlobal('fetch', vi.fn(() => { throw new Error('unexpected non-whitelist fetch') }))
  })
  afterEach(() => {
    wrappers.splice(0).forEach(w => w.unmount())
    apiOnline.value = false
    accountAlias.value = null
    vi.unstubAllGlobals()
  })
  function page() { const w = mount(OpsStreamPage); wrappers.push(w); return w }

  it('per-pane SAMPLE fallback on failed reads, and only whitelist GETs are ever attempted', async () => {
    const w = page()
    await flushPromises()
    const panes = w.findAll('.ops-pane')
    expect(panes).toHaveLength(2)
    for (const p of panes) {
      expect(p.text()).toContain('样例 / SAMPLE')
      expect(p.text()).toContain('白名单源未配置或读取失败')
      await p.find('.node-list button').trigger('click')
      expect(p.find('.evidence').text()).toContain('evidence_ref')
      expect(p.find('.evidence').text()).toContain('observed_at')
      expect(p.find('.evidence').text()).toContain('不推断相邻节点因果')
    }
    expect(panes[0]!.text()).toContain('trader-SAMPLE-account')
    expect(panes[1]!.text()).toContain('TradeMind-SAMPLE-account')
    assertWhitelistGetOnly(fetch as unknown as FetchMock)
    expect(w.find('form').exists()).toBe(false)
    expect(w.find('.sample-banner').text()).toContain('样例 / SAMPLE')
  })

  it('goes live on the right pane when the same-origin workbench answers, left stays SAMPLE without a trader base', async () => {
    const mock = stubWorkbenchLive()
    const w = page()
    await flushPromises()
    const panes = w.findAll('.ops-pane')
    expect(panes[0]!.text()).toContain('样例 / SAMPLE')
    expect(panes[1]!.text()).toContain('实时只读 / LIVE')
    expect(panes[1]!.text()).toContain('paper-t0-local-1k')
    expect(panes[1]!.text()).not.toContain('Wang-SAMPLE')
    // 混合状态横幅：明确哪窗实时哪窗样例
    expect(w.find('.sample-banner').text()).toContain('实时只读')
    expect(w.find('.sample-banner').text()).toContain('固定样例')
    // 账户玻璃舱显示实时字符串金额，非“—”占位
    await panes[1]!.findAll('.layer-nav button')[2]!.trigger('click')
    expect(panes[1]!.find('.account-metrics').text()).toContain('5000.00')
    expect(panes[1]!.find('.account-metrics').text()).toContain('2.50')
    // 事件层：有决策证据、无成交记录时不补造
    await panes[1]!.findAll('.layer-nav button')[3]!.trigger('click')
    expect(panes[1]!.text()).toContain('决策 BUY')
    // 演示状态选择器只属于样例窗
    expect(panes[0]!.find('select').exists()).toBe(true)
    expect(panes[1]!.find('select').exists()).toBe(false)
    assertWhitelistGetOnly(mock)
  })

  it('attempts trader GETs only against the configured fixed base, mapping frozen fields and dropping ok=false zeros', async () => {
    localStorage.setItem('ops-trader-base', 'http://127.0.0.1:8080/')
    const mock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === 'http://127.0.0.1:8080/api/status') {
        return jsonResponse({
          current_time: '2026-10-30T10:00:00+08:00',
          worker: { healthy: true, details: { inner_secret: 'must-not-leak' } },
          database: { connected: true, error: null },
          calendar: { market_session: 'closed' },
          data: { path: 'D:/secret/path.csv', exists: true, size_bytes: 1, last_modified: '2026-10-30T08:00:00+08:00' },
        })
      }
      if (url === 'http://127.0.0.1:8080/api/account') {
        return jsonResponse({ ok: true, account_id: 'shadow-1', currency: 'CNY', cash: '100.00', total_equity: '200.00', total_fees: '3.00', valuation_status: 'valued', open_positions_count: 1, external_flows_status: 'unsupported', updated_at: '2026-10-30T07:00:00+08:00' })
      }
      if (url === 'http://127.0.0.1:8080/api/plan') return jsonResponse({ has_plan: false })
      throw new Error(`unexpected fetch: ${url}`)
    })
    vi.stubGlobal('fetch', mock)
    const w = page()
    await flushPromises()
    const panes = w.findAll('.ops-pane')
    expect(panes[0]!.text()).toContain('实时只读 / LIVE')
    expect(panes[0]!.text()).toContain('shadow-1')
    // 主机未核实身份 → unknown，不从 URL 推断
    expect(panes[0]!.text()).toContain('unknown（未核实身份）')
    // 未脱敏字段不透传 UI
    expect(panes[0]!.text()).not.toContain('must-not-leak')
    expect(panes[0]!.text()).not.toContain('D:/secret/path.csv')
    // 计划成功空；账户玻璃舱实时数值
    await panes[0]!.findAll('.layer-nav button')[2]!.trigger('click')
    expect(panes[0]!.find('.account-metrics').text()).toContain('200.00')
    await panes[0]!.findAll('.layer-nav button')[3]!.trigger('click')
    expect(panes[0]!.text()).toContain('成功空')
    // 只访问固定 /api/* 三个白名单端点，不混用 /api/stock/*
    const urls = (mock.mock.calls as [RequestInfo | URL][]).map(([input]) => String(input))
    for (const url of urls.filter(u => u.includes('8080'))) {
      expect(url).toMatch(/^http:\/\/127\.0\.0\.1:8080\/api\/(status|account|plan)$/)
      expect(url).not.toContain('/api/stock/')
    }
  })

  it('keeps trader pane SAMPLE when ok=false zeros would be misleading', async () => {
    localStorage.setItem('ops-trader-base', 'http://127.0.0.1:8080')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === 'http://127.0.0.1:8080/api/status') return jsonResponse({ current_time: '2026-10-30T10:00:00+08:00', worker: { healthy: false, details: {} }, database: { connected: false, error: 'x' }, calendar: {}, data: {} })
      if (url === 'http://127.0.0.1:8080/api/account') return jsonResponse({ ok: false, error: 'db down', account_id: 'shadow-1', cash: '0.00', total_equity: '0.00', total_fees: '0.00' })
      if (url === 'http://127.0.0.1:8080/api/plan') return jsonResponse({ has_plan: false })
      throw new Error(`unexpected fetch: ${url}`)
    }))
    const w = page()
    await flushPromises()
    const left = w.findAll('.ops-pane')[0]!
    expect(left.text()).toContain('实时只读 / LIVE')
    await left.findAll('.layer-nav button')[2]!.trigger('click')
    // ok=false：零占位全部丢弃，金额 unknown（“—”），不显示成真实零现金
    expect(left.findAll('.account-metrics strong').map(x => x.text())).toEqual(['—', '—', '—'])
    expect(left.text()).toContain('零占位')
    expect(left.text()).not.toContain('0.00')
  })

  it('removes particles on manual off, persists preference, and pauses hidden pages', async () => {
    const w = page()
    await flushPromises()
    expect(w.findAll('.particles')).toHaveLength(2)
    await w.find('input[type=checkbox]').setValue(false)
    expect(w.find('.particles').exists()).toBe(false)
    expect(localStorage.getItem('ops-stream-motion')).toBe('off')
    await w.find('input[type=checkbox]').setValue(true)
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await nextTick()
    expect(w.find('.particles').exists()).toBe(false)
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await nextTick()
    expect(w.findAll('.particles')).toHaveLength(2)
    w.unmount()
    expect(media.removeEventListener).toHaveBeenCalled()
  })

  it('honors initial and changed reduced motion, even if the saved preference is on', async () => {
    localStorage.setItem('ops-stream-motion', 'on')
    media.matches = true
    const w = page()
    expect(w.find('.particles').exists()).toBe(false)
    expect(w.find('input[type=checkbox]').attributes('disabled')).toBeDefined()
    media.matches = false
    mediaListener?.()
    await nextTick()
    expect(w.findAll('.particles')).toHaveLength(2)
    media.matches = true
    mediaListener?.()
    await nextTick()
    expect(w.find('.particles').exists()).toBe(false)
  })

  it.each(['empty', 'missing', 'stale', 'error', 'unknown', 'loading'])(
    'isolates the left %s state and stops its particles', async status => {
      const w = page()
      await flushPromises()
      const panes = w.findAll('.ops-pane')
      await panes[0]!.find('select').setValue(status)
      expect(panes[0]!.find('.scenario-message').exists()).toBe(true)
      expect(panes[0]!.find('.particles').exists()).toBe(false)
      expect(panes[1]!.find('.node-list').exists()).toBe(true)
      assertWhitelistGetOnly(fetch as unknown as FetchMock)
    },
  )

  it('filters event evidence and keeps unknown account values separate from zero', async () => {
    const w = page()
    await flushPromises()
    const left = w.findAll('.ops-pane')[0]!
    await left.findAll('.layer-nav button')[3]!.trigger('click')
    await left.find('input[type=search]').setValue('no-match')
    expect(left.text()).toContain('成功空')
    await left.findAll('.layer-nav button')[2]!.trigger('click')
    expect(left.findAll('.account-metrics strong').map(x => x.text())).toEqual(['—', '—', '—'])
  })

  it('does not inherit a previously online account banner into the ops route', async () => {
    apiOnline.value = true
    accountAlias.value = 'TEST-OTHER-ACCOUNT'
    await router.push('/ops-stream')
    const w = mount(WorkbenchLayout, { global: { plugins: [router] } })
    wrappers.push(w)
    expect(w.find('.wb-status-strip').text()).toContain('样例 / SAMPLE')
    expect(w.text()).not.toContain('TEST-OTHER-ACCOUNT')
    expect(w.find('.wb-phase').text()).toContain('只读')
  })

  it('keeps the frozen identity envelope on every sample record and never invents cross-pane edges', () => {
    const keys = ['project', 'host', 'account', 'run', 'source', 'schema_version', 'data_as_of', 'observed_at', 'data_status', 'evidence_ref']
    for (const pane of opsStreamSample) {
      for (const record of [...pane.nodes, ...pane.edges, ...pane.events]) {
        keys.forEach(key => expect(record).toHaveProperty(key))
        expect(record.sample).toBe(true)
      }
      pane.edges.forEach(edge => {
        expect(pane.nodes.some(node => node.id === edge.from)).toBe(true)
        expect(pane.nodes.some(node => node.id === edge.to)).toBe(true)
        expect(edge.relation).toBe('config_dependency')
      })
    }
  })
})
