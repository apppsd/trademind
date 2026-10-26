<template>
  <div class="chart-panel" :class="{ compact }">
    <div v-if="title || unitLabel" class="chart-head">
      <span class="chart-title">{{ title }}</span>
      <span v-if="unitLabel" class="chart-unit">{{ unitLabel }}</span>
      <slot name="actions" />
    </div>
    <div ref="chartEl" class="chart-canvas" :style="{ height: heightPx + 'px' }"></div>
    <div v-if="note" class="chart-note">{{ note }}</div>
  </div>
</template>

<script setup lang="ts">
// 统一 ECharts 封装：按需引入、本地打包、Canvas 渲染；
// 图表只负责展示，不在这里计算权威盈亏。
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, CandlestickChart, BarChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkPointComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'

echarts.use([
  LineChart, CandlestickChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkPointComponent,
  CanvasRenderer,
])

const props = withDefaults(defineProps<{
  option: EChartsCoreOption | null
  title?: string
  unitLabel?: string | null
  note?: string | null
  heightPx?: number
  compact?: boolean
}>(), { title: '', unitLabel: null, note: null, heightPx: 380, compact: false })

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

function latest(): void {
  chart?.dispatchAction({ type: 'dataZoom', start: 70, end: 100 })
}
defineExpose({ latest })

function render() {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  if (props.option) chart.setOption(props.option, true)
}

onMounted(() => {
  render()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartEl.value!)
})
onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
watch(() => props.option, render, { deep: false })
</script>

<style scoped>
.chart-panel {
  background: var(--bg-card-1);
  border: var(--border-card);
  border-radius: var(--radius-card);
  padding: 16px 18px 10px;
  min-width: 0;
}
.chart-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.chart-title { font-size: var(--fs-secondary); color: var(--text-secondary); }
.chart-unit { font-size: var(--fs-caption); color: var(--text-faint); }
.chart-head :deep(.chart-actions) { margin-left: auto; display: flex; gap: 6px; }
.chart-canvas { width: 100%; }
.chart-note {
  font-size: var(--fs-caption);
  color: var(--text-faint);
  padding: 4px 2px 6px;
}
</style>
