<template>
  <section class="overview">
    <div class="metric-grid">
      <MetricCard label="总资产" :value="equity" unit="元"
        subtext="现金（含冻结）+ 持仓市值；估值不完整时另行标注" :status="cardStatus" />
      <MetricCard label="今日交易损益" :value="tradingPnl" unit="元"
        subtext="期末权益−期初权益−净外部资金流；入金不算收益" :status="todayStatus" />
      <MetricCard label="扣模型费后暂估" :value="netAfterModel" unit="元"
        subtext="估算 · 模型费另计，不直接从模拟现金扣除" :status="todayStatus" />
      <MetricCard label="可用现金" :value="availableCash" unit="元"
        :subtext="frozenText" :status="cardStatus" />
    </div>

    <div v-if="valuationIncomplete" class="warn-banner">
      持仓估值不完整：总资产按最后可得价格估算，非完整口径。
    </div>

    <div class="overview-main">
      <div class="chart-zone">
        <div class="chart-switch" role="tablist" aria-label="主图切换">
          <button role="tab" :aria-selected="mainMode === 'assets'" :class="{ on: mainMode === 'assets' }"
            @click="mainMode = 'assets'">账户资产变化</button>
          <button role="tab" :aria-selected="mainMode === 'instrument' && !!selectedId"
            :class="{ on: mainMode === 'instrument' }" :disabled="!selectedId"
            @click="mainMode = 'instrument'">关注标的走势</button>
        </div>
        <ChartPanel v-if="mainMode === 'assets' && equitySeries.length >= 2"
          title="账户资产变化（元）" unit-label="元" :option="equityOption" :height-px="360" />
        <ChartPanel v-else-if="mainMode === 'instrument' && selectedRows.length >= 2"
          :title="`关注标的走势（${selectedId}）`" unit-label="元" :option="instrumentOption" :height-px="360" />
        <EmptyState v-else title="暂无可绘制曲线"
          hint="资产曲线需要已生成的日报产物；标的走势需要本地 Context 分钟存量。数据不足时这里保持空态，不绘制演示曲线。" />
        <DataStatusLine class="chart-status" :data-status="mainStatus" :as-of="mainAsOf"
          :stale="mainStale" :missing-reasons="mainMissing" />
      </div>

      <aside class="watch-zone">
        <div class="panel">
          <h3 class="panel-title">关注标的</h3>
          <div class="watch-cards">
            <WatchCard v-for="inst in watchInstruments" :key="inst.instrument_id"
              :info="inst" :latest-action-reason="latestReasonFor(inst.instrument_id)"
              :available-cash="availableCash" />
            <EmptyState v-if="!watchInstruments.length && !catalog.firstLoad.value"
              title="暂无关注标的" hint="标的目录为空或未加载成功" />
          </div>
        </div>
      </aside>
    </div>

    <div class="two-col">
      <div class="panel">
        <h3 class="panel-title">状态摘要</h3>
        <ul class="status-list">
          <li><span>运行状态</span><strong>{{ runtimeText }}</strong></li>
          <li><span>最新数据时间</span><strong class="num">{{ fmtDash(shanghaiTime(overview.asOf.value)) }}</strong></li>
          <li><span>最近决策时间</span><strong class="num">{{ fmtDash(shanghaiTime(latestDecisionAt)) }}</strong></li>
          <li><span>告警</span><strong>{{ alertsBrief }}</strong></li>
        </ul>
      </div>
      <div class="panel">
        <h3 class="panel-title">为什么还没买</h3>
        <ul v-if="whyNotBuying.length" class="why-list">
          <li v-for="reason in whyNotBuying" :key="reason">{{ reason }}</li>
        </ul>
        <EmptyState v-else title="暂无未买入原因记录" hint="最新决策与告警会汇总在这里" />
      </div>
    </div>

    <div class="panel event-panel">
      <h3 class="panel-title">最新事件
        <RouterLink class="more-link" to="/activity">全部交易记录 →</RouterLink>
      </h3>
      <ul v-if="recentEvents.length" class="event-list">
        <li v-for="e in recentEvents" :key="e.key" class="event-item">
          <RouterLink :to="e.to" class="event-link">
            <span class="event-time num">{{ e.time }}</span>
            <span class="event-text">{{ e.text }}</span>
          </RouterLink>
        </li>
      </ul>
      <EmptyState v-else-if="!overview.firstLoad.value" title="暂无近期事件"
        hint="模拟成交与决策会出现在这里；无成交不等于接口失败" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { chartNumber as num } from '../utils/chartData'
import { withMinuteGaps } from '../utils/chartData'
import { computed, onMounted, ref, watch } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import ChartPanel from '../components/ChartPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import DataStatusLine from '../components/DataStatusLine.vue'
import WatchCard from '../components/WatchCard.vue'
import type { InstrumentInfo } from '../types/instrument'
import type { ActivityItem, AlertData, BarRow } from '../types/workbench'
import { fetchAccountSeries, fetchActivity, fetchAlerts, fetchBars, fetchInstruments, fetchOverview } from '../services/workbenchApi'
import { useWorkbenchResource } from '../composables/useWorkbench'
import { useResource } from '../composables/useResource'
import { phaseLabel } from '../services/online'
import { fmtDash, shanghaiTime } from '../utils/format'

const overview = useWorkbenchResource((opts) => fetchOverview(opts), 5_000)
const series = useWorkbenchResource((opts) => fetchAccountSeries(30, opts), 60_000)
const decisions = useWorkbenchResource((opts) => fetchActivity('decision', { limit: 8 }, opts), 30_000)
const fills = useWorkbenchResource((opts) => fetchActivity('fill', { limit: 5 }, opts), 15_000)
const alerts = useWorkbenchResource<AlertData>((opts) => fetchAlerts(opts), 20_000)

// 标的目录只加载一次（单资源一次在途，切页回来不重复刷全页）。
const catalog = useResource<{ instruments: InstrumentInfo[] }>(async (signal, o) => {
  const body = await fetchInstruments({ signal, timeoutMs: o.timeoutMs })
  return { data: body, asOf: null, dataStatus: 'ok', missingReasons: [] }
})
onMounted(() => { void catalog.load() })

const env = computed(() => overview.snapshot.value)
const account = computed(() => env.value?.data?.account ?? null)
const today = computed(() => env.value?.data?.today ?? null)
const cardStatus = computed(() => overview.dataStatus.value as never)
const todayStatus = computed(() => (today.value ? overview.dataStatus.value : 'unavailable') as never)

const equity = computed(() => account.value?.equity ?? null)
const tradingPnl = computed(() => today.value?.trading_pnl ?? null)
const netAfterModel = computed(() => today.value?.net_after_settled_model_estimate ?? null)
const availableCash = computed(() => account.value?.available_cash ?? null)
const frozenText = computed(() => `冻结 ${fmtDash(account.value?.reserved_cash ?? null)} 元（cash 已含冻结，不重复加减）`)
const valuationIncomplete = computed(() => account.value?.valuation_complete === false)

const watchInstruments = computed<InstrumentInfo[]>(() =>
  (catalog.snapshot.value?.instruments ?? []).slice(0, 3))

// ---- 主图：资产变化 / 标的走势 --------------------------------------------
const mainMode = ref<'assets' | 'instrument'>('assets')
const selectedId = ref<string | null>(null)
watch(watchInstruments, (list) => {
  if (!selectedId.value && list.length) selectedId.value = list[0].instrument_id
}, { immediate: true })

const instrumentBars = useWorkbenchResource((opts) => fetchBars(selectedId.value ?? '', 'timeshare', undefined, opts), 30_000)
watch(selectedId, () => {
  if (selectedId.value) { instrumentBars.cancel(); void instrumentBars.refresh() }
})

const equitySeries = computed(() =>
  (series.snapshot.value?.data?.equity_daily ?? []) as { date: string; equity: string | null }[])

const selectedRows = computed<BarRow[]>(() => {
  if (mainMode.value !== 'instrument' || !selectedId.value) return []
  const data = instrumentBars.snapshot.value?.data
  if (data?.instrument_id !== selectedId.value) return []
  return withMinuteGaps(data.bars ?? [])
})



const equityOption = computed(() => ({
  grid: { left: 64, right: 20, top: 24, bottom: 34 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: equitySeries.value.map((p) => p.date) },
  yAxis: { type: 'value', scale: true },
  series: [{
    type: 'line', name: '总资产（元）', showSymbol: true,
    data: equitySeries.value.map((p) => num(p.equity)),
    lineStyle: { color: '#55c7ff', width: 2 },
    areaStyle: { color: 'rgba(85,199,255,0.10)' },
  }],
}))

const instrumentOption = computed(() => ({
  grid: { left: 64, right: 20, top: 24, bottom: 34 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: selectedRows.value.map((r) => String(r.bar_time)) },
  yAxis: { type: 'value', scale: true },
  series: [{
    type: 'line', name: '分钟收盘（元）', showSymbol: false,
    data: selectedRows.value.map((r) => num(r.close)),
    lineStyle: { color: '#33d6c6', width: 2 },
  }],
}))

const mainStatus = computed(() =>
  mainMode.value === 'assets' ? series.dataStatus.value : instrumentBars.dataStatus.value)
const mainAsOf = computed(() =>
  mainMode.value === 'assets' ? series.asOf.value : instrumentBars.asOf.value)
const mainStale = computed(() =>
  mainMode.value === 'assets' ? series.stale.value : instrumentBars.stale.value)
const mainMissing = computed(() =>
  (mainMode.value === 'assets' ? series.missingReasons.value : instrumentBars.missingReasons.value)?.slice(0, 3) ?? [])

// ---- 状态摘要 / 为什么还没买 / 事件 --------------------------------------
const runtimeBrief = computed(() => env.value?.data?.runtime_brief ?? null)
const runtimeText = computed(() => {
  if (overview.status.value === 'error') return '服务连接失败'
  const brief = runtimeBrief.value
  if (!brief) return '—'
  if (brief.heartbeat_age_seconds === null || brief.heartbeat_age_seconds >= 30) return '监督状态未知或陈旧'
  if (brief.halted) return '需人工核对（自动模拟锁定）'
  if (brief.phase === 'paper') return '盘中模拟运行'
  if (brief.phase === null && brief.heartbeat_age_seconds === null) return '自动模拟未运行'
  return '等待开市 / 休市'
})
watch(runtimeText, (text) => { phaseLabel.value = text })

const alertItems = computed(() => alerts.snapshot.value?.data?.alerts ?? [])
const alertsBrief = computed(() => {
  if (alerts.status.value === 'error') return '告警获取失败'
  if (!alertItems.value.length) return '无未决告警'
  return `${alertItems.value.length} 条（${alertItems.value[0].reason.slice(0, 40)}）`
})

const decisionItems = computed(() => decisions.snapshot.value?.data?.items ?? [])
const latestDecisionAt = computed(() => decisionItems.value[0]?.created_at ?? null)

function latestReasonFor(instrumentId: string): string | null {
  const hit = decisionItems.value.find((d) => d.instrument_id === instrumentId)
  return hit?.reason ?? null
}

const whyNotBuying = computed<string[]>(() => {
  const reasons: string[] = []
  for (const d of decisionItems.value) {
    if ((d.action ?? '').toUpperCase() === 'WAIT' && d.reason && !reasons.includes(d.reason)) {
      reasons.push(`${displayName(d.instrument_id)}：${d.reason}`.replace(/^：/, ''))
    }
    if (reasons.length >= 3) break
  }
  for (const a of alertItems.value) {
    if (a.type === 'data_gap' && a.reason && !reasons.some((r) => r.includes(a.reason))) {
      reasons.push(a.reason)
    }
  }
  return reasons.slice(0, 4)
})

const recentEvents = computed(() => {
  const evts = [
    ...fills.snapshot.value?.data?.items ?? [],
    ...decisionItems.value.slice(0, 5),
  ]
  return evts
    .map((item) => ({
      key: `${item.kind ?? 'decision'}-${item.id}`,
      time: shanghaiTime(item.executed_at ?? item.created_at ?? null) ?? '—',
      text: eventText(item),
      to: item.fill_id ? '/activity' : '/strategy',
    }))
    .sort((a, b) => (a.time < b.time ? 1 : -1))
    .slice(0, 5)
})

function displayName(id: string | undefined): string {
  return catalog.snapshot.value?.instruments.find(i => i.instrument_id === id)?.display_name ?? `名称待补充 ${id ?? ''}`
}

function eventText(item: ActivityItem): string {
  const act = item.action?.toUpperCase()
  if (item.fill_id) {
    const side = act === 'BUY' ? '买入成交' : act === 'SELL' ? '卖出成交' : '成交'
    return `${side} · ${displayName(item.instrument_id)} ${item.quantity ?? ''} @ ${item.price ?? '—'}`
  }
  const actZh = act === 'BUY' ? '建议买入' : act === 'SELL' ? '建议卖出' : '观望（WAIT）'
  return `决策：${actZh} · ${displayName(item.instrument_id)} ${item.reason ? `（${item.reason}）` : ''}`
}
</script>

<style scoped>
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--card-gap); }
.warn-banner {
  margin-top: var(--card-gap); font-size: var(--fs-caption); color: var(--warn);
  border: 1px solid rgba(245, 176, 77, 0.3); background: rgba(245, 176, 77, 0.07);
  border-radius: var(--radius-small); padding: 6px 14px;
}
.overview-main {
  display: grid; grid-template-columns: minmax(0, 8fr) minmax(300px, 4fr);
  gap: var(--card-gap); margin-top: var(--card-gap);
}
.chart-zone { min-width: 0; }
.chart-switch { display: flex; gap: 8px; margin-bottom: 10px; }
.chart-switch button {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-secondary);
  border-radius: var(--radius-small); padding: 5px 14px; cursor: pointer; font-size: var(--fs-caption);
}
.chart-switch button.on { color: var(--accent); border-color: var(--accent); }
.chart-switch button:disabled { opacity: 0.45; cursor: not-allowed; }
.chart-status { margin-top: 8px; }
.watch-zone { min-width: 0; display: flex; flex-direction: column; }
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 16px 20px;
}
.panel-title {
  margin: 0 0 10px; font-size: var(--fs-secondary); font-weight: 600;
  color: var(--text-secondary); display: flex; justify-content: space-between; align-items: baseline;
}
.more-link { font-size: var(--fs-caption); font-weight: 400; }
.watch-cards { display: flex; flex-direction: column; gap: 12px; }
.two-col {
  display: grid; grid-template-columns: 1fr 1fr; gap: var(--card-gap); margin-top: var(--card-gap);
}
.status-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.status-list li { display: flex; justify-content: space-between; gap: 12px; font-size: var(--fs-caption); }
.status-list span { color: var(--text-tertiary); }
.why-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: var(--fs-caption); color: var(--text-secondary); }
.why-list li::before { content: '· '; color: var(--text-faint); }
.event-panel { margin-top: var(--card-gap); }
.event-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.event-link { display: flex; gap: 14px; padding: 4px 6px; border-radius: var(--radius-small); color: var(--text-secondary); }
.event-link:hover { background: var(--bg-card-2); }
.event-time { color: var(--text-faint); white-space: nowrap; }
@media (max-width: 1100px) {
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .overview-main, .two-col { grid-template-columns: 1fr; }
}
</style>
