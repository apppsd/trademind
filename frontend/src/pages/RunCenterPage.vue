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
        </ul>
        <ul v-if="blockers.length" class="blocker-list">
          <li v-for="b in blockers" :key="b">{{ b }}</li>
        </ul>
        <p v-else class="hint">无阻断项记录</p>
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
const blockers = computed(() => {
  const raw = intraday.value?.blockers
  return Array.isArray(raw) ? raw.map((b) => (typeof b === 'string' ? b : JSON.stringify(b))) : []
})
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
.blocker-list { list-style: none; margin: 10px 0 0; padding: 0; font-size: var(--fs-caption); color: var(--warn); }
.blocker-list li::before { content: '· '; }
.hint { font-size: var(--fs-caption); color: var(--text-faint); margin: 10px 0 0; }
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
