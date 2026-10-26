<template>
  <section class="reports">
    <div class="panel controls">
      <h3 class="panel-title">交易日报</h3>
      <div class="controls-row">
        <label>日期（上海日切）
          <input v-model="day" type="date" :max="today" />
        </label>
        <button @click="downloadJson">下载 JSON</button>
        <a :href="`/api/workbench/reports/${day}?fmt=md`" target="_blank" rel="noopener">查看 Markdown</a>
        <span class="hint">只读取已有产物，不触发生成、不调用模型</span>
      </div>
    </div>

    <div class="panel body-panel">
      <ErrorState v-if="report.status.value === 'error'"
        :title="report.error.value?.status === 404 ? '该日期日报产物不存在' : '日报暂时不可读'"
        :detail="report.error.value?.message ?? null" retryable @retry="() => report.refresh()" />
      <p v-else-if="report.firstLoad.value" class="loading">加载中…</p>
      <template v-else-if="reportData">
        <DataStatusLine :data-status="'ok'" :as-of="str(reportData.as_of) ?? null"
          :missing-reasons="[]" />
        <div class="kv-grid">
          <div v-for="row in summaryRows" :key="row.label" class="kv">
            <span class="k">{{ row.label }}</span>
            <span class="v num" :class="row.cls">{{ row.value }}</span>
          </div>
        </div>
        <details class="raw">
          <summary>原始 JSON（白名单展示）</summary>
          <pre class="mono">{{ jsonText }}</pre>
        </details>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import DataStatusLine from '../components/DataStatusLine.vue'
import ErrorState from '../components/ErrorState.vue'
import { fetchReport } from '../services/workbenchApi'
import { useResource } from '../composables/useResource'
import { fmtDash, shanghaiToday, shanghaiTime } from '../utils/format'

const today = shanghaiToday()
const day = ref(today)

type ReportDict = Record<string, unknown>
const report = useResource<ReportDict>(async (signal, o) => {
  const body = await fetchReport(day.value, { signal, timeoutMs: o.timeoutMs })
  return { data: body, asOf: str(body.as_of) ?? null, dataStatus: 'ok', missingReasons: [] }
})
watch(day, () => { report.cancel(true); void report.refresh() })
// 初始化即取一次
void report.refresh()

const reportData = computed<ReportDict | null>(() => report.snapshot.value)
const jsonText = computed(() => JSON.stringify(reportData.value, null, 2))

function str(v: unknown): string | null {
  return v === null || v === undefined ? null : String(v)
}

const ending = computed(() => (reportData.value?.ending ?? null) as ReportDict | null)
const modelCost = computed(() => (reportData.value?.model_cost ?? null) as ReportDict | null)

const summaryRows = computed(() => {
  const r = reportData.value ?? {}
  const pnl = str(r.trading_pnl)
  return [
    { label: '状态', value: fmtDash(str(r.period_status)), cls: '' },
    { label: '生成时间（上海）', value: fmtDash(shanghaiTime(str(r.generated_at))), cls: '' },
    { label: '期末权益', value: fmtDash(str(ending.value?.equity)) + (str(ending.value?.equity) ? ' 元' : ''), cls: '' },
    { label: '今日交易损益', value: pnl === null ? '—' : `${pnl} 元`, cls: pnl?.startsWith('-') ? 'down' : pnl && pnl !== '0' ? 'up' : '' },
    { label: '已实现盈亏', value: fmtDash(str(r.realized_pnl)), cls: '' },
    { label: '交易费用', value: fmtDash(str(r.trading_fees)), cls: '' },
    { label: '净外部资金流', value: fmtDash(str(r.net_external_cash_flow)), cls: '' },
    { label: '扣模型费后暂估', value: fmtDash(str(r.net_after_settled_model_estimate)), cls: '' },
    { label: '模型费已结算/pending', value: `${fmtDash(str(modelCost.value?.settled_estimate))} / ${fmtDash(str(modelCost.value?.pending_reservation))}`, cls: '' },
  ]
})

function downloadJson(): void {
  if (!reportData.value) return
  const blob = new Blob([jsonText.value], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `daily-report-${day.value}.json`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.reports { display: flex; flex-direction: column; gap: var(--card-gap); }
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 16px 20px;
}
.panel-title { margin: 0 0 10px; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-secondary); }
.controls-row { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: var(--fs-caption); color: var(--text-tertiary); }
.controls-row input {
  background: var(--bg-card-2); border: 1px solid var(--border-weak); color: var(--text-primary);
  border-radius: var(--radius-small); padding: 4px 8px; margin-left: 6px;
}
.controls-row button, .controls-row a {
  background: transparent; border: 1px solid var(--border-weak); color: var(--accent);
  border-radius: var(--radius-small); padding: 4px 12px; cursor: pointer; font-size: var(--fs-caption);
  text-decoration: none;
}
.hint { color: var(--text-faint); }
.loading { color: var(--text-faint); }
.kv-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px 24px; margin-top: 14px; }
.kv { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px dashed var(--border-weak); padding-bottom: 6px; }
.kv .k { color: var(--text-tertiary); font-size: var(--fs-caption); }
.kv .v { font-weight: 600; }
.kv .v.up { color: var(--up); }
.kv .v.down { color: var(--down); }
.raw { margin-top: 16px; }
.raw summary { cursor: pointer; color: var(--text-tertiary); font-size: var(--fs-caption); }
.raw pre { max-height: 320px; overflow: auto; font-size: 12px; color: var(--text-secondary); }
@media (max-width: 1100px) { .kv-grid { grid-template-columns: 1fr 1fr; } }
</style>
