<template>
  <article class="watch-card">
    <header class="wc-head">
      <InstrumentLabel :info="info" />
      <span class="wc-tags">
        <StatusBadge v-if="info?.capabilities?.tradable" label="T+0白名单" tone="ok" />
        <StatusBadge v-else label="仅观察" tone="warn" />
      </span>
    </header>
    <div class="wc-quote">
      <span class="wc-price num">{{ priceText }}</span>
      <span v-if="changeText" class="num" :class="changeCls">{{ changeText }}</span>
      <span class="wc-change-note">{{ changeText ? '区间变化（无前收，非今日涨跌）' : '' }}</span>
    </div>
    <ChartPanel v-if="hasCurve" :option="sparkOption" compact :height-px="88" />
    <div v-else class="wc-nocurve">今日暂无本地分钟行情</div>
    <footer class="wc-foot">
      <span>当前动作：{{ actionText }}</span>
      <StatusBadge :title="info.purchase_check?.reasons.join('；')" :label="cashText" :tone="cashTone" />
    </footer>
    <DataStatusLine
      class="wc-status"
      :data-status="bars.dataStatus.value"
      :as-of="bars.asOf.value"
      :stale="bars.stale.value"
      :missing-reasons="compactMissing" />
  </article>
</template>

<script setup lang="ts">
import { chartNumber as num } from '../utils/chartData'
import { withMinuteGaps } from '../utils/chartData'
import { computed, onMounted } from 'vue'
import type { InstrumentInfo } from '../types/instrument'
import type { BarRow } from '../types/workbench'
import { fetchBars } from '../services/workbenchApi'
import { useWorkbenchResource } from '../composables/useWorkbench'
import ChartPanel from './ChartPanel.vue'
import DataStatusLine from './DataStatusLine.vue'
import InstrumentLabel from './InstrumentLabel.vue'
import StatusBadge from './StatusBadge.vue'
import { fmtDash, signClass } from '../utils/format'

const props = defineProps<{
  info: InstrumentInfo
  /** 该标的最新 WAIT/动作原因（来自真实决策流）；null 显示 —。 */
  latestActionReason: string | null
  /** 可用现金（字符串金额）；用于“资金可达”判断，未知则不判断。 */
  availableCash: string | null
}>()

const bars = useWorkbenchResource(
  (opts) => fetchBars(props.info.instrument_id, 'timeshare', undefined, opts),
  30_000,
)
onMounted(() => { void bars.refresh() })

const rows = computed<BarRow[]>(() => {
  const data = bars.snapshot.value?.data
  return withMinuteGaps(data?.bars ?? [])
})
const hasCurve = computed(() => rows.value.length >= 2)



const lastClose = computed(() => num(rows.value.at(-1)?.close))
const priceText = computed(() => fmtDash(lastClose.value !== null ? lastClose.value.toFixed(3) : null))

const changeText = computed(() => {
  if (lastClose.value === null || rows.value.length < 2) return null
  const first = num(rows.value[0]?.close)
  if (first === null || first === 0) return null
  const pct = ((lastClose.value - first) / first) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
})
const changeCls = computed(() => changeText.value?.startsWith('-') ? signClass('-1') : changeText.value ? signClass('+1') : 'flat')

const sparkOption = computed(() => ({
  grid: { left: 8, right: 8, top: 8, bottom: 8 },
  xAxis: { type: 'category', show: false, data: rows.value.map((r) => String(r.bar_time)) },
  yAxis: { type: 'value', scale: true, show: false },
  series: [{
    type: 'line', data: rows.value.map((r) => num(r.close)), showSymbol: false,
    lineStyle: { color: '#55c7ff', width: 1.5 },
    areaStyle: { color: 'rgba(85,199,255,0.10)' },
  }],
}))

const actionText = computed(() => props.latestActionReason ?? '—')

const cashText = computed(() => props.info.purchase_check?.status ?? '资金可达性未知')
const cashTone = computed(() => cashText.value === '资金可达' ? 'ok' : 'warn')

const compactMissing = computed(() => (bars.missingReasons.value ?? []).slice(0, 2))
</script>

<style scoped>
.watch-card {
  border: 1px solid var(--border-weak);
  border-radius: var(--radius-small);
  background: var(--bg-card-2);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.wc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.wc-quote { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.wc-price { font-size: 22px; font-weight: 600; color: var(--text-primary); }
.wc-change-note { font-size: var(--fs-caption); color: var(--text-faint); }
.wc-nocurve {
  height: 88px; display: flex; align-items: center; justify-content: center;
  color: var(--text-faint); font-size: var(--fs-caption);
  border: 1px dashed var(--border-weak); border-radius: var(--radius-small);
}
.wc-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: var(--fs-caption); color: var(--text-secondary); }
.wc-status { margin-top: 2px; }
</style>
