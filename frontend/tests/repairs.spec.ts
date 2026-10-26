import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { fetchJson } from '../src/services/client'
import { useResource } from '../src/composables/useResource'
import { chartNumber, withMinuteGaps, eventBarIndex } from '../src/utils/chartData'
import { useWorkbenchResource } from '../src/composables/useWorkbench'
import { createRouter, createMemoryHistory } from 'vue-router'
import WorkbenchLayout from '../src/layouts/WorkbenchLayout.vue'
import { markOnline } from '../src/services/online'

const api = vi.hoisted(() => ({ fetchBars: vi.fn(), fetchInstrumentEvents: vi.fn(), fetchInstruments: vi.fn() }))
vi.mock('../src/services/workbenchApi', () => api)
vi.mock('../src/components/ChartPanel.vue', () => ({ default: { props: ['option'], template: '<div class="chart-probe" />' } }))
import MarketPage from '../src/pages/MarketPage.vue'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks() })

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const result = (value: string) => ({ data: value, asOf: null, dataStatus: 'ok', missingReasons: [] })

it('cancel immediately permits the next request; an ignored abort cannot overwrite it', async () => {
  const a = deferred<ReturnType<typeof result>>()
  const b = deferred<ReturnType<typeof result>>()
  const fetcher = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
  const scope = effectScope()
  const resource = scope.run(() => useResource(fetcher))!
  const old = resource.load()
  resource.cancel(true)
  const next = resource.load()
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls[0]![0].aborted).toBe(true)
  b.resolve(result('B')); await next
  a.resolve(result('A')); await old
  expect(resource.snapshot.value).toBe('B')
  scope.stop()
})

it('scope disposal aborts its in-flight request', async () => {
  const pending = deferred<ReturnType<typeof result>>()
  let signal: AbortSignal | undefined
  const scope = effectScope()
  const resource = scope.run(() => useResource(s => { signal = s; return pending.promise }))!
  const task = resource.load()
  scope.stop()
  expect(signal?.aborted).toBe(true)
  pending.resolve(result('late')); await task
  expect(resource.snapshot.value).toBeNull()
})

it('timeout covers an unresolved response body after headers arrive', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: () => new Promise(() => {}) })))
  const task = fetchJson('/offline-test', { timeoutMs: 50 })
  const assertion = expect(task).rejects.toMatchObject({ kind: 'timeout' })
  await vi.advanceTimersByTimeAsync(51)
  await assertion
  expect(vi.getTimerCount()).toBe(0)
})

it('external cancellation also covers a pending body', async () => {
  const controller = new AbortController()
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: () => new Promise(() => {}) })))
  const task = fetchJson('/offline-test', { signal: controller.signal })
  controller.abort()
  await expect(task).rejects.toMatchObject({ kind: 'abort' })
})

it('an already aborted signal starts no request', async () => {
  const controller = new AbortController()
  controller.abort()
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  await expect(fetchJson('/offline-test', { signal: controller.signal })).rejects.toMatchObject({ kind: 'abort' })
  expect(fetch).not.toHaveBeenCalled()
})

it('an online fixture API does not claim real market data or a healthy trading worker', async () => {
  const router = createRouter({ history:createMemoryHistory(), routes:['/','/market','/assets','/strategy','/activity','/reports','/run'].map(path => ({ path, component:{ template:'<div />' } })) })
  await router.push('/')
  markOnline('fixture-account')
  const page = mount(WorkbenchLayout, { global:{ plugins:[router] } })
  expect(page.text()).toContain('fixture-account')
  expect(page.text()).toContain('未实时核验')
  expect(page.text()).not.toContain('真实行情 · 模拟成交')
  expect(page.text()).not.toContain('盘中模拟运行')
  page.unmount()
})

it('a successful old snapshot becomes stale with time and cannot be washed fresh by polling', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-26T02:00:09Z'))
  const env = { schema_version:1 as const, account_alias:'fixture', as_of:'2026-10-26T02:00:00Z',
    generated_at:'2026-10-26T02:00:09Z', data_status:'ok' as const, source:'fixture', age_seconds:9,
    stale_after_seconds:10, partial:false, missing_reasons:[], data:{} }
  const scope = effectScope()
  const resource = scope.run(() => useWorkbenchResource(async () => env))!
  await resource.load()
  expect(resource.stale.value).toBe(false)
  await vi.advanceTimersByTimeAsync(2000)
  expect(resource.stale.value).toBe(true)
  await resource.refresh()
  expect(resource.stale.value).toBe(true)
  scope.stop()
  expect(vi.getTimerCount()).toBe(0)
})

describe('financial chart facts', () => {
  it.each([null, undefined, '', false, NaN, Infinity])('unknown %s stays a gap', value => {
    expect(chartNumber(value)).toBeNull()
  })
  it('preserves a real zero', () => { expect(chartNumber('0')).toBe(0) })
  it('anchors sub-minute events to their bucket and excludes gaps', () => {
    const rows = [{ bar_time:'2026-10-26T02:00:00Z' }, { bar_time:'2026-10-26T02:01:00Z', gap:true }]
    expect(eventBarIndex(rows, '2026-10-26T02:00:35Z')).toBe(0)
    expect(eventBarIndex(rows, '2026-10-26T02:01:35Z')).toBe(-1)
  })
  it('breaks missing minutes and incomplete buckets without inventing a price', () => {
    const rows = withMinuteGaps([
      { bar_time: '2026-10-26T02:00:00Z', close: '1' },
      { bar_time: '2026-10-26T02:03:00Z', close: '2' },
      { bar_time: '2026-10-26T02:04:00Z', close: '3', complete: false },
    ])
    expect(rows).toHaveLength(4)
    expect(chartNumber(rows[1]?.close)).toBeNull()
    expect(chartNumber(rows[3]?.close)).toBeNull()
  })
})

it('mounted market switches immediately and keeps the late old symbol off the chart', async () => {
  const a = deferred<unknown>()
  const b = deferred<unknown>()
  const instruments = ['513050.SH', '518880.SH'].map((id, index) => ({ instrument_id: id,
    display_name: index === 0 ? '阿中概互联ETF' : '黄金ETF', full_name: null, market: 'XSHG', kind: 'etf',
    currency: 'CNY', capabilities: { tradable: true }, aliases: [] }))
  api.fetchInstruments.mockResolvedValue({ instruments })
  api.fetchBars.mockImplementation((id: string) => id === '513050.SH' ? a.promise : b.promise)
  api.fetchInstrumentEvents.mockImplementation(async (id: string) => ({ data_status: 'ok', data: { instrument_id: id, events: [] } }))
  const page = mount(MarketPage)
  await flushPromises()
  expect(api.fetchBars.mock.calls[0]?.[0]).toBe('513050.SH')
  const originalSignal = api.fetchBars.mock.calls[0]?.[3].signal as AbortSignal
  const buttons = page.findAll('.inst-row')
  await buttons[1]!.trigger('click')
  await flushPromises()
  expect(originalSignal.aborted).toBe(true)
  expect(api.fetchBars.mock.calls.at(-1)?.[0]).toBe('518880.SH')
  const env = (id: string) => ({ data_status: 'ok', data: { instrument_id: id, period: '1m', bars: [
    { bar_time: '2026-10-26T02:00:00Z', close: '1' }, { bar_time: '2026-10-26T02:01:00Z', close: '2' },
  ] } })
  b.resolve(env('518880.SH')); await flushPromises()
  expect(page.find('.chart-probe').exists()).toBe(true)
  a.resolve(env('513050.SH')); await flushPromises()
  expect(page.find('.chart-probe').exists()).toBe(true)
  expect(page.find('.inst-title').text()).toBe('黄金ETF')
  page.unmount()
})
