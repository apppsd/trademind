<template>
  <div class="trader-instrument">
    <div class="inst-head">
      <StatusBadge label="SAMPLE" />
      <h3 class="inst-name">{{ sample.instrument.name }}</h3>
      <span class="inst-code mono">{{ sample.instrument.code }}</span>
      <span class="inst-meta num">近 {{ bars.length }} 个交易日 · 截至 {{ asOfLabel }}</span>
    </div>

    <p v-if="requestedCode && requestedCode !== sample.instrument.code" class="disclaimer">
      URL 请求标的 <span class="mono num">{{ requestedCode }}</span> 不在样例数据内；
      以下仍展示样例标的 {{ sample.instrument.code }} {{ sample.instrument.name }} 的演示图，不接真实行情。
    </p>

    <ChartPanel title="日线 · MA20 · 成交量" unit-label="元"
      :option="instrumentOption" :height-px="280" />

    <section class="pane-card">
      <h3 class="card-title">走势解读</h3>
      <p class="explanation">{{ sample.instrument.explanation }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
// trader/标的：SAMPLE 演示页（TASK-08c）。图表恒为样例 600036；query traderInstrument
// 只做比对提示，不发真实行情请求，也不产生任何交易动作。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts/core'
import { MarkLineComponent } from 'echarts/components'
import ChartPanel from '../../../components/ChartPanel.vue'
import StatusBadge from '../../../components/StatusBadge.vue'
import { traderCockpitSample as sample } from '../../../demo/traderCockpitSample'
import { shanghaiTime } from '../../../utils/format'
import type { EChartsCoreOption } from 'echarts/core'

// ChartPanel 已注册 MarkPoint；markLine（计划价虚线）由本页按需补注册（echarts.use 全局生效）。
echarts.use([MarkLineComponent])

const route = useRoute()

const requestedCode = computed(() => {
  const q = route.query.traderInstrument
  return typeof q === 'string' ? q : Array.isArray(q) ? (q[0] ?? '') : ''
})

const UP = '#ff5c5c'
const DOWN = '#34d399'
const PLAN = '#f5b04d'
const ACCENT = '#55c7ff'

const bars = sample.instrument.bars
const asOfLabel = shanghaiTime(sample.meta.asOf)

/** 日线分类轴刻度：MM-dd（shanghaiTime 输出 'MM-dd HH:mm:ss'，截前 5 位）。 */
const times = bars.map((b) => shanghaiTime(b.t)?.slice(0, 5) ?? b.t.slice(5, 10))

// ma20[0] 对应 bars[19]（20 日窗口首次完整）；前 19 根窗口不足，补 null 不绘制。
const maData: (number | null)[] = [
  ...Array.from({ length: bars.length - sample.instrument.ma20.length }, () => null as number | null),
  ...sample.instrument.ma20,
]

const closes = bars.map((b) => b.c)
const volColors = closes.map((c, i) => (c >= (i > 0 ? closes[i - 1] : c) ? UP : DOWN))

/** 成交/计划时间带盘中时分，按自然日落到对应日 K 分类轴。 */
function dayIndex(iso: string): number {
  const day = iso.slice(0, 10)
  return bars.findIndex((b) => b.t.slice(0, 10) === day)
}

// 成交圆点（样例数据不含买卖方向，只标成交价，不做方向臆造）。
const fillMarks = sample.instrument.fills.flatMap((f) => {
  const i = dayIndex(f.t)
  if (i < 0) return []
  return [{
    coord: [times[i], f.price], symbol: 'circle', symbolSize: 7,
    itemStyle: { color: ACCENT, borderColor: 'rgba(255,255,255,0.85)', borderWidth: 1 },
    label: { show: true, position: 'top', formatter: f.price.toFixed(2), color: ACCENT, fontSize: 10 },
  }]
})

// 计划价虚线：全宽水平线，只标价格，语义见走势解读。
const planLines = sample.instrument.plans.map((p) => ({
  yAxis: p.price,
  lineStyle: { type: 'dashed' as const, color: PLAN, width: 1 },
  label: { show: true, position: 'insideEndTop', formatter: p.price.toFixed(2), color: PLAN, fontSize: 10 },
}))

const instrumentOption = computed<EChartsCoreOption>(() => ({
  animation: false,
  axisPointer: { link: [{ xAxisIndex: 'all' }] },
  grid: [
    { left: 52, right: 14, top: 16, height: '56%' },
    { left: 52, right: 14, top: '72%', height: '16%' },
  ],
  tooltip: { trigger: 'axis' },
  xAxis: [
    { type: 'category', data: times, gridIndex: 0, axisLine: { lineStyle: { color: '#2a3a55' } } },
    { type: 'category', data: times, gridIndex: 1, axisLabel: { show: false } },
  ],
  yAxis: [
    { type: 'value', scale: true, gridIndex: 0, splitLine: { lineStyle: { color: 'rgba(142,165,199,0.08)' } } },
    { type: 'value', gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
  ],
  dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
  series: [
    {
      type: 'candlestick', name: '日线',
      data: bars.map((b) => [b.o, b.c, b.l, b.h]),
      itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      markPoint: fillMarks.length ? { data: fillMarks, animation: false } : undefined,
      markLine: planLines.length ? { symbol: 'none', data: planLines, animation: false } : undefined,
    },
    {
      type: 'line', name: 'MA20', data: maData, showSymbol: false,
      lineStyle: { color: ACCENT, width: 1.4 },
    },
    {
      type: 'bar', name: '成交量', data: bars.map((b) => b.v),
      itemStyle: { color: (p: { dataIndex: number }) => volColors[p.dataIndex] },
      xAxisIndex: 1, yAxisIndex: 1,
    },
  ],
}))
</script>

<style scoped>
.trader-instrument { display: flex; flex-direction: column; gap: 8px; }

.inst-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.inst-name { margin: 0; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-primary); }
.inst-code { color: var(--text-faint); }
.inst-meta { font-size: var(--fs-caption); color: var(--text-faint); }

.disclaimer {
  margin: 0;
  font-size: var(--fs-caption); color: var(--warn);
  background: rgba(245, 176, 77, 0.08);
  border: 1px solid rgba(245, 176, 77, 0.25);
  border-radius: var(--radius-small);
  padding: 6px 10px;
}

.pane-card {
  background: var(--bg-inset);
  border: var(--border-card);
  border-radius: var(--radius-small);
  padding: 10px 12px;
  min-width: 0;
}

.card-title {
  margin: 0 0 8px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: var(--fs-caption); font-weight: 600; color: var(--text-secondary);
}

.explanation {
  margin: 0;
  font-size: var(--fs-caption); line-height: 1.6;
  color: var(--text-secondary);
}
</style>
