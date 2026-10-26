import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '@/components/StatusBadge.vue'
import MetricCard from '@/components/MetricCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import InstrumentLabel from '@/components/InstrumentLabel.vue'
import type { InstrumentInfo } from '@/types/instrument'

const info: InstrumentInfo = {
  instrument_id: '513050.SH',
  display_name: '中概互联ETF',
  full_name: '易方达中概互联50ETF',
  market: 'XSHG',
  kind: 'etf',
  currency: 'CNY',
  name_source: 'whitelist',
  aliases: [],
  capabilities: { tradable: true, settlement_rule: 'T+0', lot_size: 100, intraday_bars: true, daily_bars: false },
}

describe('StatusBadge', () => {
  it('maps data_status to deterministic tones', () => {
    const toneOf = (label: string) =>
      mount(StatusBadge, { props: { label } }).find('.badge').attributes('data-tone')
    expect(toneOf('ok')).toBe('ok')
    expect(toneOf('partial')).toBe('warn')
    expect(toneOf('stale')).toBe('warn')
    expect(toneOf('unavailable')).toBe('danger')
    expect(toneOf('error')).toBe('danger')
  })

  it('renders arbitrary Chinese labels with muted tone', () => {
    const w = mount(StatusBadge, { props: { label: '仅观察' } })
    expect(w.text()).toContain('仅观察')
  })
})

describe('MetricCard', () => {
  it('renders em dash for unknown value, never 0', () => {
    const w = mount(MetricCard, { props: { label: '今日交易盈亏', value: null } })
    expect(w.find('.metric-value').text()).toBe('—')
    expect(w.text()).not.toContain('0')
  })

  it('shows unit and subtext when value known', () => {
    const w = mount(MetricCard, { props: { label: '可用现金', value: '812.34', unit: '元', subtext: '冻结 200.00 元' } })
    expect(w.find('.metric-value').text()).toBe('812.34')
    expect(w.find('.metric-unit').text()).toBe('元')
    expect(w.text()).toContain('冻结 200.00 元')
  })
})

describe('InstrumentLabel', () => {
  it('shows Chinese display name with copyable code', () => {
    const w = mount(InstrumentLabel, { props: { info, showCode: true } })
    expect(w.find('.inst-name').text()).toBe('中概互联ETF')
    expect(w.find('.inst-code').text()).toBe('513050.SH')
  })

  it('degrades safely for unknown instrument id', () => {
    const w = mount(InstrumentLabel, { props: { info: '999999.ZZ', showCode: true } })
    expect(w.find('.inst-name').text()).toBe('名称待补充')
    expect(w.find('.inst-code').text()).toBe('999999.ZZ')
  })

  it('degrades safely for null info', () => {
    const w = mount(InstrumentLabel, { props: { info: null } })
    expect(w.find('.inst-name').text()).toBe('名称待补充')
  })
})

describe('EmptyState / ErrorState', () => {
  it('empty state shows title and hint', () => {
    const w = mount(EmptyState, { props: { title: '暂无成交', hint: '可查看决策与原因' } })
    expect(w.text()).toContain('暂无成交')
    expect(w.text()).toContain('可查看决策与原因')
  })

  it('error state shows retry button only when retryable', () => {
    expect(mount(ErrorState, { props: { title: '该模块暂时不可用' } }).find('button').exists()).toBe(false)
    expect(mount(ErrorState, { props: { title: '失败', retryable: true } }).find('button').exists()).toBe(true)
  })
})
