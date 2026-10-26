<template>
  <section class="market">
    <aside class="list-pane panel">
      <h3 class="panel-title">关注列表</h3>
      <input v-model="query" class="search" type="search" placeholder="搜索中文名 / 代码"
        aria-label="搜索标的" />
      <div class="filter-row" role="group" aria-label="筛选">
        <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部</button>
        <button :class="{ on: filter === 'tradable' }" @click="filter = 'tradable'">T+0白名单</button>
        <button :class="{ on: filter === 'watch' }" @click="filter = 'watch'">仅观察</button>
      </div>
      <ul class="inst-list">
        <li v-for="inst in visibleInstruments" :key="inst.instrument_id">
          <button class="inst-row" :class="{ on: inst.instrument_id === selectedId }"
            @click="select(inst.instrument_id)">
            <span class="inst-name">{{ inst.display_name ?? '名称待补充' }}</span>
            <span class="inst-code mono">{{ inst.instrument_id }}</span>
            <span class="fav" role="button" tabindex="0" :aria-label="`收藏 ${inst.instrument_id}`"
              @click.stop="toggleFav(inst.instrument_id)"
              @keydown.enter.prevent="toggleFav(inst.instrument_id)">{{ isFav(inst.instrument_id) ? '★' : '☆' }}</span>
          </button>
        </li>
      </ul>
      <EmptyState v-if="!list.firstLoad.value && visibleInstruments.length === 0" title="没有匹配的标的"
        hint="支持中文名与代码搜索" />
    </aside>

    <div class="detail-pane">
      <div v-if="selected" class="detail-head panel">
        <div class="head-main">
          <h3 class="inst-title">{{ selected.display_name ?? '名称待补充' }}</h3>
          <button class="full-name-toggle" @click="showFull = !showFull">{{ showFull ? '收起全称' : '全称' }}</button>
          <span v-if="showFull" class="full-name">{{ selected.full_name ?? '全称未录入' }}</span>
        </div>
        <div class="head-meta">
          <span>{{ selected.market ?? '—' }} · {{ selected.kind ?? '—' }} · {{ selected.currency ?? '—' }}</span>
          <StatusBadge v-if="selected.capabilities?.tradable" label="T+0白名单" tone="ok" />
          <StatusBadge v-else label="仅观察" tone="warn" />
          <span class="mono code-copy" title="点击复制代码" role="button" tabindex="0"
            @click="copyCode" @keydown.enter.prevent="copyCode">{{ selected.instrument_id }}</span>
        </div>
      </div>

      <div class="chart-toolbar panel">
        <div class="period-row" role="tablist" aria-label="周期">
          <button v-for="p in PERIODS" :key="p.key" role="tab"
            :aria-selected="period === p.key" :class="{ on: period === p.key }"
            @click="period = p.key">{{ p.label }}</button>
          <button disabled title="日K需要完整权威日线数据；本地分钟存量不能拼成完整一天">日K 不可用</button>
        </div>
        <div class="tool-row">
          <button @click="returnLatest">回到最新</button>
          <label class="marks-toggle">
            <input v-model="showMarks" type="checkbox" /> 交易/决策标记
          </label>
        </div>
      </div>

      <ChartPanel ref="priceChart" v-if="chartOption" :option="chartOption" :height-px="480"
        :note="chartNote" title="价格（元）· 成交量（份）" />
      <div v-else class="panel chart-empty">
        <ErrorState v-if="bars.status.value === 'error'" :detail="bars.error.value?.message ?? null"
          retryable @retry="() => bars.refresh()" />
        <EmptyState v-else-if="!bars.firstLoad.value" title="暂无该标的本地分钟行情"
          hint="后端缺 Context 存量时这里保持空态，不绘制演示K线" />
        <p v-else class="loading-text">加载中…</p>
      </div>
      <DataStatusLine class="chart-status" :data-status="bars.dataStatus.value" :as-of="bars.asOf.value"
        :stale="bars.stale.value" :missing-reasons="bars.missingReasons.value?.slice(0, 4) ?? []" />

      <div class="panel events-panel">
        <h3 class="panel-title">当日事件图层语义</h3>
        <ul class="event-legend">
          <li><span class="mk mk-fill-buy">▲</span> 买入成交（真实模拟 Fill）</li>
          <li><span class="mk mk-fill-sell">▼</span> 卖出成交（真实模拟 Fill）</li>
          <li><span class="mk mk-order">●</span> 订单提交（非成交）</li>
          <li><span class="mk mk-intent">◇</span> 决策意图（非成交）</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { chartNumber as num } from '../utils/chartData'
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import type { InstrumentInfo } from '../types/instrument'
import type { BarRow, EventMark } from '../types/workbench'
import type { BarPeriod } from '../services/workbenchApi'
import { fetchBars, fetchInstruments, fetchInstrumentEvents } from '../services/workbenchApi'
import { useResource } from '../composables/useResource'
import { useWorkbenchResource } from '../composables/useWorkbench'
import { useFavoriteInstruments } from '../composables/usePreferences'
import ChartPanel from '../components/ChartPanel.vue'
import DataStatusLine from '../components/DataStatusLine.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { withMinuteGaps, eventBarIndex } from '../utils/chartData'
import { shanghaiTime } from '../utils/format'

const PERIODS = [
  { key: 'timeshare', label: '分时' },
  { key: '1m', label: '1m' },
  { key: '5m', label: '5m' },
  { key: '15m', label: '15m' },
] as const

const query = ref('')
const filter = ref<'all' | 'tradable' | 'watch'>('all')
const selectedId = ref<string | null>(null)
const period = ref<BarPeriod>('timeshare')
const showMarks = ref(true)
const showFull = ref(false)

const { favorites, toggle: toggleFav } = useFavoriteInstruments()
const isFav = (id: string) => favorites.value.has(id)

// 搜索/目录：切关键词时单在途 + 取消上一次（资源层负责去重与世代守卫）。
const list = useResource<{ instruments: InstrumentInfo[] }>(async (signal, o) => {
  const body = await fetchInstruments({ q: query.value.trim() || undefined, signal, timeoutMs: o.timeoutMs })
  return { data: body, asOf: null, dataStatus: 'ok', missingReasons: [] }
})
let searchTimer: number | undefined
watch(query, () => {
  window.clearTimeout(searchTimer)
  list.cancel(true)
  searchTimer = window.setTimeout(() => { void list.load() }, 300)
})
onMounted(() => { void list.load() })
onScopeDispose(() => window.clearTimeout(searchTimer))

const visibleInstruments = computed<InstrumentInfo[]>(() => {
  const items = list.snapshot.value?.instruments ?? []
  return items.filter((i) =>
    filter.value === 'tradable' ? i.capabilities?.tradable
      : filter.value === 'watch' ? !i.capabilities?.tradable : true)
    .sort((a, b) => (a.display_name ?? '').localeCompare(b.display_name ?? '', 'zh-Hans-CN'))
})

watch(visibleInstruments, (items) => {
  if (!selectedId.value && items.length) selectedId.value = items[0].instrument_id
}, { immediate: true })

const selected = computed<InstrumentInfo | null>(() =>
  (list.snapshot.value?.instruments ?? []).find((i) => i.instrument_id === selectedId.value) ?? null)

function select(id: string): void {
  selectedId.value = id  // 切换取消由 [selectedId, period] 的 watch 统一处理
}

watch([selectedId, period], () => {
  bars.cancel(true)       // 切标的/周期取消旧请求
  eventsRes.cancel(true)
  void bars.refresh()
  void eventsRes.refresh()
})

// K线：单资源按 (selectedId, period) 取数。
const bars = useWorkbenchResource(
  (opts) => fetchBars(selectedId.value ?? '', period.value, undefined, { ...opts, timeoutMs: 15_000 }),
)
const eventsRes = useWorkbenchResource(
  (opts) => fetchInstrumentEvents(selectedId.value ?? '', undefined, opts),
)

const rows = computed<BarRow[]>(() => {
  const data = bars.snapshot.value?.data
  if (!data || data.instrument_id !== selectedId.value || data.period !== (period.value === 'timeshare' ? '1m' : period.value)) return []
  return withMinuteGaps(data.bars ?? [], period.value === '5m' ? 5 : period.value === '15m' ? 15 : 1)
})

const marks = computed<EventMark[]>(() => {
  const data = eventsRes.snapshot.value?.data
  if (!data || data.instrument_id !== selectedId.value) return []
  return data.events
})



const upColor = '#ff5c5c'
const downColor = '#34d399'

const chartOption = computed(() => {
  if (rows.value.length < 2) return null
  const times = rows.value.map((r) => {
    const t = shanghaiTime(String(r.bar_time))
    return t ?? String(r.bar_time)
  })
  const isCandle = period.value !== 'timeshare'
  const closes = rows.value.map((r) => num(r.close))
  const volumes = rows.value.map((r) => num(r.volume))
  const volColors = closes.map((c, i) => {
    const prev = i > 0 ? closes[i - 1] : c
    return c !== null && prev !== null && c >= prev ? upColor : downColor
  })
  const markData = showMarks.value ? buildMarks() : []
  const series: Record<string, unknown>[] = []
  if (isCandle) {
    series.push({
      type: 'candlestick', name: 'K线',
      data: rows.value.map((r) => [num(r.open), num(r.close), num(r.low), num(r.high)]),
      itemStyle: { color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor },
      markPoint: markData.length ? { data: markData, animation: false } : undefined,
    })
  } else {
    series.push({
      type: 'line', name: '分时', data: closes, showSymbol: false,
      lineStyle: { color: '#55c7ff', width: 1.6 },
      areaStyle: { color: 'rgba(85,199,255,0.08)' },
      markPoint: markData.length ? { data: markData, animation: false } : undefined,
    })
  }
  series.push({
    type: 'bar', name: '成交量', data: volumes,
    itemStyle: { color: (p: { dataIndex: number }) => volColors[p.dataIndex] },
    xAxisIndex: 1, yAxisIndex: 1,
  })
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 64, right: 20, top: 20, height: '58%' },
           { left: 64, right: 20, top: '72%', height: '18%' }],
    tooltip: { trigger: 'axis' },
    axisLabel: {},
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, axisLine: { lineStyle: { color: '#2a3a55' } } },
      { type: 'category', data: times, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0, splitLine: { lineStyle: { color: 'rgba(142,165,199,0.08)' } } },
      { type: 'value', gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 70, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], start: 70, end: 100, height: 18, bottom: 6 },
    ],
    series,
  }
})

function buildMarks() {
  const out: { coord: [string, number]; symbol: string; symbolSize: number; itemStyle: { color: string }; label: { show: boolean; formatter: string; color?: string } }[] = []
  for (const ev of marks.value) {
    const idx = eventBarIndex(rows.value, ev.time, period.value === '5m' ? 5 : period.value === '15m' ? 15 : 1)
    if (idx < 0) continue
    const t = times.value[idx]
    if (!t) continue
    // 事件价格优先；无价格用就近bar收盘（不做臆造价格）。
    let y = num(ev.price)
    if (y === null) {
      y = idx >= 0 ? num(rows.value[idx]?.close) : null
    }
    if (y === null) continue
    const action = (ev.action ?? '').toUpperCase()
    if (ev.type === 'fill') {
      const buy = action === 'BUY'
      out.push({
        coord: [t, y], symbol: 'triangle', symbolSize: 10,
        itemStyle: { color: buy ? upColor : downColor },
        label: { show: true, formatter: buy ? '买' : '卖', color: buy ? upColor : downColor },
      })
    } else if (ev.type === 'order_submitted') {
      out.push({
        coord: [t, y], symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#f5b04d' }, label: { show: false, formatter: '' },
      })
    } else {
      out.push({
        coord: [t, y], symbol: 'diamond', symbolSize: 8,
        itemStyle: { color: 'rgba(158,172,192,0.9)' }, label: { show: false, formatter: '' },
      })
    }
  }
  return out
}

const times = computed(() => rows.value.map((r) => shanghaiTime(String(r.bar_time)) ?? String(r.bar_time)))

const chartNote = computed(() => {
  const data = bars.snapshot.value?.data
  const notes: string[] = []
  if (data?.gap_notes?.length) notes.push(data.gap_notes[0].reason)
  if (data?.coverage) notes.push(`覆盖 ${shanghaiTime(data.coverage.from) ?? '—'} ~ ${shanghaiTime(data.coverage.to) ?? '—'}（${data.coverage.count}根）`)
  return notes.join('；') || null
})

async function copyCode(): Promise<void> {
  if (selectedId.value) {
    try { await navigator.clipboard.writeText(selectedId.value) } catch { /* 剪贴板不可用时静默 */ }
  }
}
const priceChart = ref<InstanceType<typeof ChartPanel> | null>(null)
async function returnLatest(): Promise<void> {
  await Promise.all([bars.refresh(), eventsRes.refresh()])
  priceChart.value?.latest()
}
</script>

<style scoped>
.market {
  display: grid; grid-template-columns: minmax(260px, 3fr) minmax(0, 9fr);
  gap: var(--card-gap); align-items: start;
}
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 14px 18px;
}
.panel-title { margin: 0 0 10px; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-secondary); }
.search {
  width: 100%; background: var(--bg-card-2); border: 1px solid var(--border-weak);
  color: var(--text-primary); border-radius: var(--radius-small); padding: 7px 12px; margin-bottom: 8px;
}
.filter-row { display: flex; gap: 6px; margin-bottom: 10px; }
.filter-row button, .period-row button, .tool-row button {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-secondary);
  border-radius: var(--radius-small); padding: 3px 10px; cursor: pointer; font-size: var(--fs-caption);
}
.filter-row button.on, .period-row button.on { color: var(--accent); border-color: var(--accent); }
.inst-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; max-height: 520px; overflow: auto; }
.inst-row {
  width: 100%; display: flex; align-items: center; gap: 8px; text-align: left;
  background: transparent; border: 1px solid transparent; color: var(--text-primary);
  border-radius: var(--radius-small); padding: 7px 10px; cursor: pointer;
}
.inst-row:hover { background: var(--bg-card-2); }
.inst-row.on { border-color: var(--accent); background: var(--bg-card-2); }
.inst-name { flex: 1; }
.inst-code { font-size: var(--fs-caption); color: var(--text-faint); }
.fav { color: #f5b04d; cursor: pointer; padding: 0 4px; }
.detail-pane { display: flex; flex-direction: column; gap: var(--card-gap); min-width: 0; }
.head-main { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.inst-title { margin: 0; font-size: var(--fs-title); }
.full-name-toggle {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-tertiary);
  border-radius: var(--radius-small); padding: 2px 8px; cursor: pointer; font-size: var(--fs-caption);
}
.full-name { width: 100%; color: var(--text-tertiary); font-size: var(--fs-caption); }
.head-meta { display: flex; gap: 12px; align-items: center; margin-top: 6px; color: var(--text-tertiary); font-size: var(--fs-caption); flex-wrap: wrap; }
.code-copy { cursor: copy; }
.period-row { display: flex; gap: 6px; flex-wrap: wrap; }
.period-row button:disabled { opacity: 0.45; cursor: not-allowed; }
.tool-row { display: flex; gap: 14px; align-items: center; margin-top: 8px; }
.marks-toggle { font-size: var(--fs-caption); color: var(--text-secondary); display: flex; gap: 6px; align-items: center; }
.chart-empty { min-height: 480px; display: flex; align-items: center; justify-content: center; }
.loading-text { color: var(--text-faint); }
.chart-status { padding: 0 4px; }
.events-panel { font-size: var(--fs-caption); }
.event-legend { list-style: none; margin: 0; padding: 0; display: flex; gap: 18px; flex-wrap: wrap; color: var(--text-tertiary); }
.mk-fill-buy { color: var(--up); }
.mk-fill-sell { color: var(--down); }
.mk-order { color: var(--warn); }
.mk-intent { color: var(--text-tertiary); }
@media (max-width: 1100px) {
  .market { grid-template-columns: 1fr; }
}
</style>
