<template>
  <div class="trader-overview">
    <div class="metric-grid">
      <MetricCard label="权益" :value="fmtNum(sample.metrics.equity.value)" unit="元"
        subtext="现金 + 持仓市值" :as-of-label="asOfLabel" />
      <MetricCard label="现金" :value="fmtNum(sample.metrics.cash.value)" unit="元" subtext="模拟盘可用" />
      <MetricCard label="持仓市值" :value="fmtNum(sample.metrics.positionValue.value)" unit="元"
        :subtext="`${sample.holdings.length} 只持仓`" />
      <MetricCard label="当日盈亏" :value="fmtSigned(sample.metrics.dayPnl.value)" unit="元"
        subtext="较前一交易日收盘" />
    </div>

    <ChartPanel class="equity-chart" title="权益曲线（近 20 个交易日）" unit-label="元"
      :option="equityOption" :height-px="200" />

    <div class="two-col">
      <section class="pane-card">
        <h3 class="card-title">为什么没交易</h3>
        <ul class="reason-list">
          <li v-for="reason in sample.whyNoTrade" :key="reason">{{ reason }}</li>
        </ul>
      </section>
      <section class="pane-card">
        <h3 class="card-title">
          <span class="card-title-text">{{ sample.planSummary.title }}</span>
          <StatusBadge v-if="sample.planSummary.nonRebalanceDay" label="非调仓日" />
        </h3>
        <ul class="reason-list">
          <li v-for="line in sample.planSummary.lines" :key="line">{{ line }}</li>
        </ul>
      </section>
    </div>

    <div class="two-col">
      <section class="pane-card">
        <h3 class="card-title">
          候选速览
          <button type="button" class="link-btn" @click="setTraderPage('explorer')">全部候选 →</button>
        </h3>
        <table class="dense-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th class="r">分值</th><th>区</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in peekCandidates" :key="c.code">
              <td class="mono">{{ c.code }}</td>
              <td>{{ c.name }}</td>
              <td class="r">{{ c.score }}</td>
              <td><StatusBadge :label="c.bucket === 'plan' ? '计划' : '观察'" :tone="c.bucket === 'plan' ? 'ok' : 'muted'" /></td>
            </tr>
          </tbody>
        </table>
      </section>
      <section class="pane-card">
        <h3 class="card-title">最近运行</h3>
        <table class="dense-table">
          <thead>
            <tr><th>时间</th><th>结果</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="run in sample.recentRuns" :key="run.id">
              <td class="nowrap">{{ fmtDash(shanghaiTime(run.at)) }}</td>
              <td><StatusBadge :label="RUN_LABEL[run.result]" :tone="RUN_TONE[run.result]" /></td>
              <td class="run-note">{{ run.note }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// trader/总览：SAMPLE 演示页（TASK-08b）。全部数值来自前端样例，不接真实链路。
import MetricCard from '../../../components/MetricCard.vue'
import ChartPanel from '../../../components/ChartPanel.vue'
import StatusBadge from '../../../components/StatusBadge.vue'
import { traderCockpitSample as sample } from '../../../demo/traderCockpitSample'
import { useCockpitNavigation } from '../../../composables/useCockpitNavigation'
import { fmtDash, shanghaiTime } from '../../../utils/format'

const { setTraderPage } = useCockpitNavigation()

const asOfLabel = shanghaiTime(sample.meta.asOf)
const peekCandidates = sample.candidates.slice(0, 5)

const RUN_LABEL = { completed: '完成', partial: '部分', failed: '失败' } as const
const RUN_TONE: Record<keyof typeof RUN_LABEL, 'ok' | 'warn' | 'danger'> = {
  completed: 'ok', partial: 'warn', failed: 'danger',
}

/** 样例数值恒为有限数，直接千分位两位小数；未知口径由接入真实接口的页面负责。 */
function fmtNum(value: number): string {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtSigned(value: number): string {
  if (value > 0) return `+${fmtNum(value)}`
  if (value < 0) return `-${fmtNum(-value)}`
  return fmtNum(value)
}

const equityOption = {
  grid: { left: 56, right: 12, top: 14, bottom: 24 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: sample.equitySeries.map((p) => p.t.slice(5, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{
    type: 'line', name: '权益（元）', showSymbol: false,
    data: sample.equitySeries.map((p) => p.v),
    lineStyle: { color: '#55c7ff', width: 2 },
    areaStyle: { color: 'rgba(85, 199, 255, 0.10)' },
  }],
}
</script>

<style scoped>
.trader-overview { display: flex; flex-direction: column; gap: 8px; }

.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
/* 半窗格密度：压低指标卡内边距与主数字字号 */
.metric-grid :deep(.metric-card) { padding: 10px 12px; gap: 4px; }
.metric-grid :deep(.metric-value) { font-size: 22px; }

.equity-chart { margin-top: 0; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: start; }

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
.card-title-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.link-btn {
  appearance: none; border: none; background: none; padding: 0;
  font-size: var(--fs-caption); color: var(--accent); cursor: pointer; font-weight: 400;
}
.link-btn:hover { text-decoration: underline; }

.reason-list {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: 6px;
  font-size: var(--fs-caption); color: var(--text-secondary);
}
.reason-list li { position: relative; padding-left: 10px; }
.reason-list li::before { content: '·'; position: absolute; left: 0; color: var(--text-faint); }

.dense-table { width: 100%; border-collapse: collapse; font-size: var(--fs-caption); }
.dense-table th {
  text-align: left; font-weight: 400; color: var(--text-faint);
  padding: 3px 6px; border-bottom: 1px solid var(--border-weak); white-space: nowrap;
}
.dense-table td {
  padding: 4px 6px; vertical-align: top; color: var(--text-secondary);
  border-bottom: 1px solid rgba(142, 165, 199, 0.07);
}
.dense-table tbody tr:last-child td { border-bottom: none; }
.r { text-align: right; }
.nowrap { white-space: nowrap; }
.run-note { color: var(--text-faint); }

@media (max-width: 1100px) {
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .two-col { grid-template-columns: 1fr; }
}
</style>
