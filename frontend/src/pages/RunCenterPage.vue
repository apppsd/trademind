<template>
  <section class="run-center">
    <div class="readonly-banner">当前只读，不可操作（观察服务不执行控制；控制动作需既有授权流程与令牌，见旧工作台）</div>

    <div class="grid">
      <div class="panel">
        <h3 class="panel-title">监督进程（auto-paper）</h3>
        <ul class="kv-list">
          <li><span>阶段</span><strong>{{ phaseZh }}</strong></li>
          <li><span>健康状态</span><strong>{{ str(sup?.health_status) ?? '—' }}</strong></li>
          <li><span>状态消息</span><strong>{{ str(sup?.message) ?? '—' }}</strong></li>
          <li><span>心跳年龄</span><strong class="num">{{ fmtDash(age(supAge)) }}</strong></li>
          <li><span>paper 子进程 PID</span><strong class="num">{{ fmtDash(int(sup?.child_pid)) }}</strong></li>
          <li><span>下次动作时间</span><strong class="num">{{ fmtDash(shanghaiTime(str(sup?.retry_at))) }}</strong></li>
        </ul>
      </div>

      <div class="panel">
        <h3 class="panel-title">盘中健康（paper worker）</h3>
        <ul class="kv-list">
          <li><span>健康状态</span><strong>{{ str(intraday?.health_status) ?? '—' }}</strong></li>
          <li><span>数据日期</span><strong class="num">{{ str(intraday?.date) ?? '—' }}</strong></li>
          <li><span>持仓 / 未成交订单</span><strong class="num">{{ fmtDash(int(intraday?.open_positions_count)) }} / {{ fmtDash(int(intraday?.open_orders_count)) }}</strong></li>
          <li><span>最近原因</span><strong>{{ lastReason }}</strong></li>
        </ul>
        <p v-if="statusMessage" class="hint">{{ statusMessage }}</p>
        <p v-else class="hint">最近一轮没有留下原因记录</p>
      </div>

      <div class="panel">
        <h3 class="panel-title">观察服务（observer）</h3>
        <ul class="kv-list">
          <li><span>运行</span><strong>{{ obs?.running ? '是' : obs === null ? '状态未知' : '否' }}</strong></li>
          <li><span>PID</span><strong class="num">{{ fmtDash(int(obs?.pid)) }}</strong></li>
          <li><span>心跳年龄</span><strong class="num">{{ fmtDash(age(obs?.heartbeat_age_seconds ?? null)) }}</strong></li>
        </ul>
        <p class="hint">观察服务崩溃不影响交易：paper 与 observer 是独立进程，崩溃后监督进程 60 秒退避重生。</p>
      </div>

      <div class="panel">
        <h3 class="panel-title">停止开关与模型预算</h3>
        <ul class="kv-list">
          <li><span>kill switch</span><strong>{{ kill?.engaged ? `已生效（${str(kill.reason) ?? '无原因记录'}）` : kill === null ? '状态未知' : '未触发' }}</strong></li>
          <li><span>更新时间</span><strong class="num">{{ fmtDash(shanghaiTime(str(kill?.updated_at))) }}</strong></li>
          <li><span>今日模型费（已结算估值）</span><strong class="num">{{ fmtDash(str(budget?.settled_today)) }} 元</strong></li>
          <li><span>pending（未最终结算）</span><strong class="num">{{ fmtDash(str(budget?.pending_today)) }} 元</strong></li>
        </ul>
        <p class="hint">模型费为估算、另计，不直接从模拟现金扣除；预算上限以配置为准，此处不把上限写成已花。</p>
      </div>
    </div>

    <div v-if="gate" class="panel alerts-panel">
      <h3 class="panel-title">最近一轮策略条件（只读，不改变下单）</h3>
      <p class="hint">标的 {{ str(intraday?.last_instrument_id) ?? '—' }} · 计划 {{ str(gate.plan_id) ?? '—' }} · 允许动作 {{ allowedText }}</p>
      <p class="hint">排除：{{ exclusionText }}</p>
      <table v-if="conditionRows.length" class="cond-table">
        <thead>
          <tr><th>方向</th><th>特征</th><th>判断</th><th>阈值</th><th>实际值</th><th>满足</th><th>缺失</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in conditionRows" :key="i">
            <td>{{ row.side }}</td>
            <td>{{ row.field }}</td>
            <td>{{ row.operator }}</td>
            <td class="num">{{ row.threshold }}</td>
            <td class="num">{{ row.actual }}</td>
            <td>{{ row.matched }}</td>
            <td>{{ row.missing }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="hint">这一轮没有逐条条件记录</p>
    </div>

    <div class="panel alerts-panel">
      <h3 class="panel-title">告警（页面内通知，按类型+原因去重）</h3>
      <ul v-if="aggregated.length" class="alert-list">
        <li v-for="a in aggregated" :key="a.key" :data-sev="a.severity">
          <span class="alert-sev">{{ a.severity === 'danger' ? '严重' : '提醒' }}</span>
          <span class="alert-reason">{{ a.reason }}</span>
          <span class="alert-meta num">首次 {{ a.first }} · 最后 {{ a.last }} · {{ a.count }} 次</span>
        </li>
      </ul>
      <EmptyState v-else-if="!runtime.firstLoad.value" title="无未决告警"
        hint="普通 WAIT 观望不会作为告警弹出；只有数据缺口、锁定、kill switch 等才进入本列表" />
      <DataStatusLine class="status" :data-status="runtime.dataStatus.value" :as-of="runtime.asOf.value"
        :stale="runtime.stale.value" :missing-reasons="runtime.missingReasons.value?.slice(0, 4) ?? []" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { AlertItem } from '../types/workbench'
import { fetchAlerts, fetchRuntime } from '../services/workbenchApi'
import { useWorkbenchResource } from '../composables/useWorkbench'
import DataStatusLine from '../components/DataStatusLine.vue'
import EmptyState from '../components/EmptyState.vue'
import { ageLabel, fmtDash, shanghaiTime } from '../utils/format'
import { phaseLabel } from '../services/online'

const runtime = useWorkbenchResource((opts) => fetchRuntime(opts), 5_000)
const alertsRes = useWorkbenchResource((opts) => fetchAlerts(opts), 10_000)

function str(v: unknown): string | null {
  return v === null || v === undefined || v === '' ? null : String(v)
}
function int(v: unknown): number | null {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
function age(seconds: number | null): string | null {
  return ageLabel(seconds)
}

const data = computed(() => runtime.snapshot.value?.data ?? null)
const sup = computed(() => data.value?.supervisor as Record<string, unknown> | null)
const supAge = computed(() => int(sup.value?.state_age_seconds))
const intraday = computed(() => data.value?.intraday_health as Record<string, unknown> | null)
const statusMessage = computed(() => str(intraday.value?.status_message_zh))
const lastReason = computed(() => {
  const klass = str(intraday.value?.last_blocker_class_zh) ?? str(intraday.value?.last_blocker_class)
  const reason = str(intraday.value?.last_decision_reason_zh) ?? str(intraday.value?.last_decision_reason)
  if (klass && reason) return `${klass}：${reason}`
  return klass ?? reason ?? '—'
})
const FIELD_ZH: Record<string, string> = {
  close: '收盘价', vwap: '成交量加权均价', atr_14: '十四期平均真实波幅',
  ret_1m: '一分钟收益率', ret_5m: '五分钟收益率', ret_15m: '十五分钟收益率', volume_ratio: '量比',
}
const OP_ZH: Record<string, string> = { gt: '大于', gte: '大于或等于', lt: '小于', lte: '小于或等于', eq: '等于' }
const ACTION_ZH: Record<string, string> = { BUY: '买入', SELL: '卖出', HOLD: '持有', WAIT: '观望' }
const EXCLUSION_ZH: Record<string, string> = {
  plan_disallowed: '计划未允许', conditions_unmet: '条件未满足', pending_order: '存在未成交订单',
  cash_reserve: '现金储备不足', no_position_or_qty: '无持仓或可卖数量', unknown: '未知',
}
const gate = computed(() => {
  const raw = intraday.value?.last_action_gate
  return raw !== null && typeof raw === 'object' ? raw as Record<string, unknown> : null
})
const allowedText = computed(() => {
  const raw = gate.value?.allowed_actions
  if (!Array.isArray(raw) || raw.length === 0) return '—'
  return raw.map((a) => ACTION_ZH[String(a)] ?? String(a)).join('、')
})
const exclusionText = computed(() => {
  const raw = gate.value?.exclusion
  if (!raw || typeof raw !== 'object') return '—'
  return Object.entries(raw as Record<string, unknown>)
    .map(([action, reason]) => `${ACTION_ZH[action] ?? action}=${reason == null ? '未排除' : (EXCLUSION_ZH[String(reason)] ?? String(reason))}`)
    .join('、') || '—'
})

function conditionList(side: string, key: string) {
  const raw = gate.value?.[key]
  if (!Array.isArray(raw)) return []
  return raw.filter((row) => row && typeof row === 'object').map((row) => {
    const item = row as Record<string, unknown>
    const field = str(item.field)
    const operator = str(item.operator)
    return {
      side,
      field: field ? (FIELD_ZH[field] ?? field) : '—',
      operator: operator ? (OP_ZH[operator] ?? operator) : '—',
      threshold: str(item.threshold) ?? '—',
      actual: str(item.actual) ?? '—',
      matched: item.matched === true ? '是' : '否',
      missing: item.missing === true ? '是' : '否',
    }
  })
}
const conditionRows = computed(() => [
  ...conditionList('入场', 'entry_conditions'),
  ...conditionList('退出', 'exit_conditions'),
])
const obs = computed(() => data.value?.observer ?? null)
const kill = computed(() => data.value?.kill_switch ?? null)
const budget = computed(() => data.value?.model_budget_today ?? null)

const PHASE_ZH: Record<string, string> = {
  paper: '盘中模拟运行', serve: '旧版网页服务（遗留）', null: '等待开市 / 休市',
}
const phaseZh = computed(() => {
  if (!sup.value || sup.value.active !== true) return '监督进程状态未知或已停止'
  const phase = sup.value?.phase
  if (sup.value?.halted === true) return '已锁定，待人工核对'
  if (phase === null || phase === undefined) return '等待开市 / 休市（无 writer 子进程）'
  return PHASE_ZH[String(phase)] ?? String(phase)
})
// 顶栏阶段文案与运行中心保持一致
watch(phaseZh, (v) => { phaseLabel.value = v })

// ---- 告警去重聚合（跨轮询累计 first/last/count，页面内内存态） ----------
const seen = new Map<string, { key: string; reason: string; severity: string; first: string; last: string; count: number }>()
const tick = ref(0)
onMounted(() => {
  watch(alertItems, mergeAlerts)
  mergeAlerts(alertItems.value)
})

const alertItems = computed<AlertItem[]>(() => alertsRes.snapshot.value?.data?.alerts ?? [])

function mergeAlerts(items: AlertItem[]): void {
  for (const a of items) {
    const key = `${a.type}|${a.instrument_id ?? ''}|${a.reason}`
    const now = shanghaiTime(new Date().toISOString()) ?? '—'
    const hit = seen.get(key)
    if (hit) {
      hit.last = now
      hit.count += 1
    } else {
      seen.set(key, { key, reason: a.reason, severity: a.severity, first: now, last: now, count: 1 })
    }
  }
  tick.value += 1  // 触发聚合视图重算
}

const aggregated = computed(() => {
  void tick.value
  const active = new Set(alertItems.value.map((a) => `${a.type}|${a.instrument_id ?? ''}|${a.reason}`))
  return [...seen.values()]
    .filter((a) => active.has(a.key))  // 已消失的告警不再展示
    .sort((a, b) => (a.severity === b.severity ? a.key.localeCompare(b.key) : a.severity === 'danger' ? -1 : 1))
})
</script>

<style scoped>
.run-center { display: flex; flex-direction: column; gap: var(--card-gap); }
.readonly-banner {
  font-size: var(--fs-caption); color: var(--text-secondary);
  border: 1px solid var(--border-weak); background: var(--bg-card-2);
  border-radius: var(--radius-small); padding: 6px 14px;
}
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--card-gap); }
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 14px 18px;
}
.panel-title { margin: 0 0 10px; font-size: var(--fs-secondary); font-weight: 600; color: var(--text-secondary); }
.kv-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 7px; font-size: var(--fs-caption); }
.kv-list li { display: flex; justify-content: space-between; gap: 10px; }
.kv-list span { color: var(--text-tertiary); }
.kv-list strong { font-weight: 500; color: var(--text-primary); text-align: right; word-break: break-all; }
.hint { font-size: var(--fs-caption); color: var(--text-faint); margin: 10px 0 0; }
.cond-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: var(--fs-caption); }
.cond-table th, .cond-table td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border-weak); }
.cond-table th { color: var(--text-tertiary); font-weight: 500; }
.alerts-panel { font-size: var(--fs-caption); }
.alert-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.alert-list li { display: flex; gap: 12px; align-items: baseline; border: 1px solid var(--border-weak); border-radius: var(--radius-small); padding: 7px 12px; }
.alert-list li[data-sev='danger'] { border-color: rgba(255, 92, 92, 0.4); }
.alert-sev { white-space: nowrap; }
.alert-list li[data-sev='danger'] .alert-sev { color: var(--up); }
.alert-list li[data-sev='warn'] .alert-sev { color: var(--warn); }
.alert-reason { flex: 1; color: var(--text-primary); }
.alert-meta { color: var(--text-faint); white-space: nowrap; }
.status { margin-top: 10px; }
@media (max-width: 1280px) { .grid { grid-template-columns: repeat(2, 1fr); } }
</style>
