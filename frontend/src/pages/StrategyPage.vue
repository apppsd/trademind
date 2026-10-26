<template>
  <section class="strategy">
    <div class="panel">
      <h3 class="panel-title">策略计划（Flash 生成 · 真实 StrategyPlan 历史）</h3>
      <template v-if="plans.length">
        <article v-for="(plan, idx) in plans" :key="String(plan.plan_id ?? idx)" class="plan-card"
          :class="{ current: idx === 0 }">
          <header class="plan-head">
            <StatusBadge :label="planStatusLabel(plan)" :tone="planStatusTone(plan)" />
            <span class="mono plan-id">{{ str(plan.plan_id) }}</span>
            <InstrumentLabel :info="instrumentInfo(str(plan.instrument_id))" />
            <span class="plan-time num">{{ fmtDash(shanghaiTime(str(plan.available_at))) }} 可用</span>
            <span class="plan-time num">失效 {{ fmtDash(shanghaiTime(str(plan.valid_until))) }}</span>
          </header>
          <dl class="plan-grid">
            <div><dt>趋势</dt><dd>{{ fmtDash(str(plan.trend)) }}</dd></div>
            <div><dt>风险</dt><dd>{{ fmtDash(str(plan.risk)) }}</dd></div>
            <div><dt>反证</dt><dd>{{ fmtDash(str(plan.critic)) }}</dd></div>
            <div><dt>允许动作</dt><dd class="mono">{{ fmtDash(join(plan.allowed_actions)) }}</dd></div>
            <div class="wide"><dt>原因</dt><dd>{{ fmtDash(str(plan.reason)) }}</dd></div>
          </dl>
          <div class="conditions">
            <div class="cond-col">
              <h4>入场条件（需全部满足）</h4>
              <ul><li v-for="(c, i) in conds(plan.entry_conditions)" :key="'e' + i">{{ c }}</li></ul>
            </div>
            <div class="cond-col">
              <h4>退出条件（任一满足）</h4>
              <ul><li v-for="(c, i) in conds(plan.exit_conditions)" :key="'x' + i">{{ c }}</li></ul>
            </div>
            <div class="cond-col">
              <h4>失效条件（任一失效即作废）</h4>
              <ul><li v-for="(c, i) in conds(plan.invalidation_conditions)" :key="'i' + i">{{ c }}</li></ul>
            </div>
          </div>
          <p class="source-note">实际值来自生成前最近留存快照：{{ fmtDash(shanghaiTime(str(plan.condition_as_of))) }}。阈值来自计划；无快照显示未知，不用最新行情回填过去。
            <RouterLink to="/activity">查看决策、订单、成交与程序复核时间线</RouterLink>；JEV 置信度为回答置信度，非盈利概率。</p>
        </article>
      </template>
      <EmptyState v-else-if="!plansRes.firstLoad.value" title="暂无策略计划"
        hint="配置账户尚无 StrategyPlan 记录；不把旧 Thesis 接口当作快速链计划" />
      <DataStatusLine class="status" :data-status="plansRes.dataStatus.value" :as-of="plansRes.asOf.value"
        :stale="plansRes.stale.value" :missing-reasons="plansRes.missingReasons.value ?? []" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import InstrumentLabel from '../components/InstrumentLabel.vue'
import { useResource } from '../composables/useResource'
import type { InstrumentInfo } from '../types/instrument'
import type { PlanRow } from '../types/workbench'
import { fetchStrategies, fetchInstruments } from '../services/workbenchApi'
import { useWorkbenchResource } from '../composables/useWorkbench'
import DataStatusLine from '../components/DataStatusLine.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { fmtDash, shanghaiTime } from '../utils/format'

const plansRes = useWorkbenchResource((opts) => fetchStrategies(20, opts), 30_000)
const plans = computed(() => (plansRes.snapshot.value?.data?.plans ?? []) as PlanRow[])

function str(v: unknown): string | null {
  return v === null || v === undefined || v === '' ? null : String(v)
}
function join(v: unknown): string | null {
  return Array.isArray(v) ? v.map(a => ({ BUY:'买入', SELL:'卖出', HOLD:'持有', WAIT:'观望' }[String(a)] ?? '未知动作')).join(' / ') : str(v)
}
const catalog = useResource<{ instruments: InstrumentInfo[] }>(async (signal) => {
  const data = await fetchInstruments({ signal })
  return { data, asOf: null, dataStatus: 'ok', missingReasons: [] }
})
onMounted(() => { void catalog.load() })
function instrumentInfo(id: string | null): InstrumentInfo | null {
  return catalog.snapshot.value?.instruments.find(i => i.instrument_id === id) ?? (id ? { instrument_id: id, display_name: null } as InstrumentInfo : null)
}
function conds(v: unknown): string[] {
  if (!Array.isArray(v)) return ['条件未记录']
  if (!v.length) return ['无条件记录']
  const fields: Record<string, string> = { close:'收盘价（元）', ret_1m:'1分钟收益率（小数）', ret_5m:'5分钟收益率（小数）', ret_15m:'15分钟收益率（小数）', vwap:'成交均价（元）', atr_14:'14期平均波幅（元）', volume_ratio:'量比（倍）' }
  const operators: Record<string,string> = { gt:'大于', gte:'大于等于', lt:'小于', lte:'小于等于', eq:'等于' }
  return v.map(c => {
    if (!c || typeof c !== 'object') return '条件无法解析'
    const row = c as Record<string,unknown>
    const state = row.matched === true ? '满足' : row.matched === false ? '不满足' : '未知'
    return `${fields[String(row.field)] ?? '未知条件'} ${operators[String(row.operator)] ?? '未知比较'} ${str(row.value) ?? '未知'}；当时值 ${str(row.actual) ?? '未知'} · ${state}`
  })
}
function planStatusLabel(plan: PlanRow): string {
  const until = Date.parse(String(plan.valid_until ?? ''))
  if (Number.isFinite(until) && Date.now() >= until) return '已过期'
  return str(plan.display_status) ?? '状态未知'
}
function planStatusTone(plan: PlanRow): 'ok' | 'warn' {
  return planStatusLabel(plan) === '有效' ? 'ok' : 'warn'
}


</script>

<style scoped>
.strategy { display: flex; flex-direction: column; gap: var(--card-gap); }
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 16px 20px;
}
.panel-title { margin: 0 0 12px; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-secondary); }
.plan-card { border: 1px solid var(--border-weak); border-radius: var(--radius-small); padding: 12px 16px; margin-bottom: 12px; background: var(--bg-card-2); }
.plan-card.current { border-color: var(--accent); }
.plan-head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; font-size: var(--fs-caption); }
.plan-id { color: var(--text-faint); }
.plan-time { color: var(--text-tertiary); }
.plan-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 18px; margin: 12px 0 0; }
.plan-grid div.wide { grid-column: 1 / -1; }
.plan-grid dt { font-size: var(--fs-caption); color: var(--text-tertiary); }
.plan-grid dd { margin: 2px 0 0; font-size: var(--fs-caption); color: var(--text-primary); }
.conditions { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px; }
.cond-col h4 { margin: 0 0 6px; font-size: var(--fs-caption); font-weight: 600; color: var(--text-secondary); }
.cond-col ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: var(--fs-caption); color: var(--text-secondary); }
.cond-col li::before { content: '· '; color: var(--text-faint); }
.source-note { margin: 12px 0 0; font-size: var(--fs-caption); color: var(--text-faint); }
.status { margin-top: 8px; }
@media (max-width: 1100px) {
  .plan-grid { grid-template-columns: 1fr 1fr; }
  .conditions { grid-template-columns: 1fr; }
}
</style>
