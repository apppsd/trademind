<template>
  <section class="assets">
    <div class="account-grid">
      <MetricCard label="总资产" :value="acc?.equity ?? null" unit="元"
        subtext="权益 = 现金（含冻结）+ 持仓市值" :status="accountStatus" />
      <MetricCard label="现金（含冻结）" :value="acc?.cash ?? null" unit="元"
        subtext="cash 为账本事实，含冻结部分" :status="accountStatus" />
      <MetricCard label="冻结" :value="acc?.reserved_cash ?? null" unit="元"
        subtext="未成交订单占用的资金" :status="accountStatus" />
      <MetricCard label="可用现金" :value="acc?.available_cash ?? null" unit="元"
        subtext="可用 = cash − 冻结（不重复加减）" :status="accountStatus" />
      <MetricCard label="持仓标的数" :value="acc?.positions_count ?? null" unit="只"
        subtext="空仓时不画虚构占比" :status="accountStatus" />
      <MetricCard label="今日模型费（已结算估值）" :value="today?.model_cost_settled ?? null" unit="元"
        subtext="估算 · 另计，不直接从模拟现金扣除；pending 未最终结算" :status="todayStatus" />
    </div>

    <div v-if="valuationIncomplete" class="warn-banner">估值不完整：部分持仓缺少最新可得价格，总资产为估算口径。</div>

    <div class="curve-block panel">
      <div class="chart-switch" role="tablist" aria-label="曲线切换">
        <button role="tab" :aria-selected="mode === 'equity'" :class="{ on: mode === 'equity' }"
          @click="mode = 'equity'">资产曲线</button>
        <button role="tab" :aria-selected="mode === 'pnl'" :class="{ on: mode === 'pnl' }"
          @click="mode = 'pnl'">交易损益（扣资金流）</button>
      </div>
      <ChartPanel v-if="mode === 'equity' && equityRows.length >= 2" :option="equityOption"
        title="资产曲线（元）· 入金/出金单独标记，不画为收益" unit-label="元" :height-px="320" />
      <ChartPanel v-else-if="mode === 'pnl' && pnlRows.length >= 2" :option="pnlOption"
        title="交易损益（元）= 期末权益 − 期初权益 − 净外部资金流" unit-label="元" :height-px="320" />
      <EmptyState v-else title="暂无可绘制曲线" hint="需要至少两天的日报产物（data/daily-reports）" />
      <DataStatusLine class="status" :data-status="series.dataStatus.value" :as-of="series.asOf.value"
        :stale="series.stale.value" :missing-reasons="series.missingReasons.value?.slice(0, 3) ?? []" />
      <p class="semantics">口径：{{ series.snapshot.value?.data?.semantics?.trading_pnl ?? '以服务端日报口径为准' }}；
        收益率在口径方法未声明前一律显示 —。</p>
    </div>

    <div class="positions-block panel">
      <h3 class="panel-title">持仓
        <RouterLink class="more-link" to="/reports">日报 →</RouterLink>
      </h3>
      <table v-if="positionRows.length" class="pos-table">
        <thead>
          <tr>
            <th>名称</th><th>代码</th><th class="r">数量</th><th class="r">可卖数量</th>
            <th class="r">成本价</th><th class="r">最新价</th><th class="r">市值</th>
            <th class="r">浮动盈亏</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in positionRows" :key="p.instrument_id">
            <td><InstrumentLabel :info="catalogInfo(p.instrument_id)" /></td>
            <td class="mono code">{{ p.instrument_id }}</td>
            <td class="r num">{{ fmtDash(p.quantity) }}</td>
            <td class="r num">{{ fmtDash(p.available_quantity) }}</td>
            <td class="r num">{{ fmtDash(p.avg_cost) }}</td>
            <td class="r num">{{ fmtDash(p.last_price) }}</td>
            <td class="r num">{{ fmtDash(p.market_value) }}</td>
            <td class="r num" :class="signClass(p.unrealized_pnl)">{{ fmtDash(p.unrealized_pnl) }}</td>
          </tr>
        </tbody>
      </table>
      <EmptyState v-else-if="!positions.firstLoad.value" title="空仓"
        hint="当前无持仓记录；不绘制虚构占比" />
      <DataStatusLine class="status" :data-status="positions.dataStatus.value" :as-of="positions.asOf.value"
        :stale="positions.stale.value" :missing-reasons="positions.missingReasons.value?.slice(0, 3) ?? []" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { chartNumber as num } from '../utils/chartData'
import { computed, onMounted, ref } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import ChartPanel from '../components/ChartPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import DataStatusLine from '../components/DataStatusLine.vue'
import InstrumentLabel from '../components/InstrumentLabel.vue'
import type { InstrumentInfo } from '../types/instrument'
import { fetchAccountSeries, fetchInstruments, fetchOverview, fetchPositions } from '../services/workbenchApi'
import { useWorkbenchResource } from '../composables/useWorkbench'
import { useResource } from '../composables/useResource'
import { fmtDash, signClass } from '../utils/format'

const overview = useWorkbenchResource((opts) => fetchOverview(opts), 8_000)
const positions = useWorkbenchResource((opts) => fetchPositions(opts), 8_000)
const series = useWorkbenchResource((opts) => fetchAccountSeries(30, opts), 60_000)

const catalog = useResource<{ instruments: InstrumentInfo[] }>(async (signal, o) => {
  const body = await fetchInstruments({ signal, timeoutMs: o.timeoutMs })
  return { data: body, asOf: null, dataStatus: 'ok', missingReasons: [] }
})
onMounted(() => { void catalog.load() })
function catalogInfo(id: string): InstrumentInfo | string | null {
  return (catalog.snapshot.value?.instruments ?? []).find((i) => i.instrument_id === id) ?? id
}

const acc = computed(() => overview.snapshot.value?.data?.account ?? null)
const today = computed(() => overview.snapshot.value?.data?.today ?? null)
const accountStatus = computed(() => overview.dataStatus.value as never)
const todayStatus = computed(() => (today.value ? overview.dataStatus.value : 'unavailable') as never)
const valuationIncomplete = computed(() => acc.value?.valuation_complete === false)

const positionRows = computed(() => positions.snapshot.value?.data?.positions ?? [])

const mode = ref<'equity' | 'pnl'>('equity')

const equityRows = computed(() =>
  (series.snapshot.value?.data?.equity_daily ?? []) as { date: string; equity: string | null }[])
const pnlRows = computed(() =>
  (series.snapshot.value?.data?.trading_pnl_daily ?? []) as { date: string; trading_pnl: string | null }[])

const equityOption = computed(() => ({
  grid: { left: 64, right: 20, top: 20, bottom: 32 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: equityRows.value.map((r) => r.date) },
  yAxis: { type: 'value', scale: true },
  series: [{
    type: 'line', showSymbol: true, data: equityRows.value.map((r) => num(r.equity)),
    lineStyle: { color: '#55c7ff', width: 2 },
    areaStyle: { color: 'rgba(85,199,255,0.10)' },
  }],
}))

const pnlOption = computed(() => ({
  grid: { left: 64, right: 20, top: 20, bottom: 32 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: pnlRows.value.map((r) => r.date) },
  yAxis: { type: 'value', scale: true },
  series: [{
    type: 'bar', data: pnlRows.value.map((r) => ({
      value: num(r.trading_pnl),
      itemStyle: { color: (num(r.trading_pnl) ?? 0) >= 0 ? '#ff5c5c' : '#34d399' },
    })),
  }],
}))
</script>

<style scoped>
.assets { display: flex; flex-direction: column; gap: var(--card-gap); }
.account-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: var(--card-gap); }
.warn-banner {
  font-size: var(--fs-caption); color: var(--warn);
  border: 1px solid rgba(245, 176, 77, 0.3); background: rgba(245, 176, 77, 0.07);
  border-radius: var(--radius-small); padding: 6px 14px;
}
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 16px 20px;
}
.panel-title { margin: 0 0 10px; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-secondary); display: flex; justify-content: space-between; }
.more-link { font-size: var(--fs-caption); font-weight: 400; }
.chart-switch { display: flex; gap: 8px; margin-bottom: 10px; }
.chart-switch button {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-secondary);
  border-radius: var(--radius-small); padding: 4px 12px; cursor: pointer; font-size: var(--fs-caption);
}
.chart-switch button.on { color: var(--accent); border-color: var(--accent); }
.status { margin-top: 8px; }
.semantics { font-size: var(--fs-caption); color: var(--text-faint); margin: 8px 0 0; }
.pos-table { width: 100%; border-collapse: collapse; font-size: var(--fs-caption); }
.pos-table th, .pos-table td { padding: 7px 10px; border-bottom: 1px solid var(--border-weak); }
.pos-table th { color: var(--text-tertiary); font-weight: 500; text-align: left; }
.pos-table .r { text-align: right; }
.pos-table .code { color: var(--text-faint); }
@media (max-width: 1280px) { .account-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 800px) { .account-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
