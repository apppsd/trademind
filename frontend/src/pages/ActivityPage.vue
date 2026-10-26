<template>
  <section class="activity">
    <div class="panel">
      <div class="tabs" role="tablist" aria-label="记录类型">
        <button v-for="k in KINDS" :key="k.key" role="tab" :aria-selected="kind === k.key"
          :class="{ on: kind === k.key }" @click="switchKind(k.key)">{{ k.label }}</button>
      </div>
      <div class="filters">
        <label>标的 <select v-model="instrumentFilter"><option value="">全部标的</option><option v-for="i in catalog.snapshot.value?.instruments ?? []" :key="i.instrument_id" :value="i.instrument_id">{{ i.display_name ?? `名称待补充 ${i.instrument_id}` }}</option></select></label>
        <label>日期 <input v-model="dayFilter" type="date" :max="shanghaiToday()" /></label>
        <span class="hint">服务端筛选与分页；切换筛选回到第一页</span>
      </div>

      <table v-if="items.length" class="act-table">
        <thead>
          <tr>
            <th>时间</th>
            <th v-if="kind !== 'cash'">名称 / 代码</th>
            <th v-if="kind !== 'cash'">方向</th>
            <th v-if="kind !== 'decision'">状态</th>
            <th class="r" v-if="kind === 'fill' || kind === 'order'">价格</th>
            <th class="r" v-if="kind !== 'cash'">数量</th>
            <th class="r" v-if="kind === 'fill'">费用</th>
            <th class="r" v-if="kind === 'cash'">金额</th>
            <th v-if="kind === 'decision'">原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id" class="row-link" tabindex="0"
            @click="openDetail(item)" @keydown.enter.prevent="openDetail(item)">
            <td class="num">{{ fmtDash(shanghaiTime(item.executed_at ?? item.created_at ?? item.effective_at ?? null)) }}</td>
            <template v-if="kind !== 'cash'">
              <td><InstrumentLabel :info="catalogInfo(item.instrument_id ?? null)" /></td>
              <td>{{ sideZh(item) }}</td>
              <td v-if="kind !== 'decision'">{{ statusZh(item) }}</td>
            </template>
            <td class="r num" v-if="kind === 'fill' || kind === 'order'">{{ fmtDash(item.price ?? item.limit_price ?? null) }}</td>
            <td class="r num" v-if="kind !== 'cash'">{{ fmtDash(item.quantity ?? item.filled_quantity ?? null) }}</td>
            <td class="r num" v-if="kind === 'fill'">{{ fmtDash(item.fee ?? null) }}</td>
            <td class="r num" v-if="kind === 'cash'">{{ fmtDash(item.amount ?? null) }}</td>
            <td v-if="kind === 'decision'" class="reason">{{ truncate(item.reason ?? null) }}</td>
          </tr>
        </tbody>
      </table>
      <EmptyState v-else-if="!list.firstLoad.value" :title="emptyTitle" :hint="emptyHint" />
      <ErrorState v-if="list.status.value === 'error'" :detail="list.error.value?.message ?? null"
        retryable @retry="() => list.refresh()" />

      <div class="pager">
        <button :disabled="!canPrev" @click="goPrev">上一页</button>
        <button v-if="hasMore" @click="goNext">下一页</button>
        <span class="hint">每页 {{ limit }} 条</span>
      </div>
    </div>

    <DetailDrawer :open="detailOpen" :title="`记录详情 · ${detailId}`" @close="detailOpen = false">
      <ol v-if="timeline.length" class="trace-timeline">
        <li v-for="(node, index) in timeline" :key="index">
          <strong>{{ node.label }}</strong> · {{ fmtDash(shanghaiTime(node.time)) }}
          <p>{{ node.record.reason ?? node.record.verdict ?? node.record.status ?? '已留存记录' }}</p>
        </li>
      </ol>
      <p v-if="detailRows.length" class="hint">仅展示已保存且能关联的节点；缺失节点不代表通过。</p>
      <dl v-if="detailRows.length" class="detail-kv">
        <div v-for="row in detailRows" :key="row.k">
          <dt>{{ row.k }}</dt>
          <dd class="mono">{{ truncate(row.v, 400) }}</dd>
        </div>
      </dl>
      <p v-else-if="detailLoading" class="hint">加载中…</p>
      <p v-else class="hint">详情不可用或记录不属于当前账户</p>
    </DetailDrawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import type { ActivityItem, ActivityKind } from '../types/workbench'
import type { InstrumentInfo } from '../types/instrument'
import { fetchActivity, fetchActivityDetail, fetchInstruments } from '../services/workbenchApi'
import { useResource } from '../composables/useResource'
import DetailDrawer from '../components/DetailDrawer.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorState from '../components/ErrorState.vue'
import InstrumentLabel from '../components/InstrumentLabel.vue'
import { fmtDash, shanghaiTime, shanghaiToday } from '../utils/format'

const KINDS = [
  { key: 'fill', label: '成交' },
  { key: 'decision', label: '决策' },
  { key: 'order', label: '订单' },
  { key: 'cash', label: '资金变动' },
] as const

const kind = ref<ActivityKind>('fill')
const instrumentFilter = ref('')
const dayFilter = ref('')
const limit = 50
const cursorStack = ref<(string | null)[]>([null])
const cursor = computed(() => cursorStack.value.at(-1) ?? null)

let debounceTimer: number | undefined
watch([instrumentFilter, dayFilter], () => {
  list.cancel(true)
  window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    cursorStack.value = [null]  // 切筛选回第一页
    list.cancel(true)
    void list.refresh()
  }, 300)
})

// 交易记录不轮询（审计流，用户手动翻页/刷新）；首次进入加载一次。
const list = useResource<{ items: ActivityItem[]; hasMore: boolean; nextCursor: string | null }>(
  async (signal, o) => {
    const env = await fetchActivity(kind.value, {
      instrumentId: instrumentFilter.value.trim() || undefined,
      day: dayFilter.value || undefined,
      limit,
      cursor: cursor.value ?? undefined,
    }, { signal, timeoutMs: o.timeoutMs })
    const data = env.data
    return {
      data: { items: data?.items ?? [], hasMore: data?.has_more ?? false, nextCursor: data?.next_cursor ?? null },
      asOf: env.as_of ?? null, dataStatus: env.data_status, missingReasons: env.missing_reasons ?? [],
    }
  })

function switchKind(next: ActivityKind): void {
  detailOpen.value = false
  kind.value = next
  cursorStack.value = [null]
  list.cancel(true)
  void list.refresh()
}
function goNext(): void {
  const next = list.snapshot.value?.nextCursor
  if (next) cursorStack.value.push(next)
  list.cancel(true)
  void list.refresh()
}
function goPrev(): void {
  if (cursorStack.value.length > 1) cursorStack.value.pop()
  list.cancel(true)
  void list.refresh()
}
const canPrev = computed(() => cursorStack.value.length > 1)
const hasMore = computed(() => list.snapshot.value?.hasMore ?? false)

onMounted(() => { void list.refresh() })

const items = computed(() => list.snapshot.value?.items ?? [])

const catalog = useResource<{ instruments: InstrumentInfo[] }>(async (signal, o) => {
  const body = await fetchInstruments({ signal, timeoutMs: o.timeoutMs })
  return { data: body, asOf: null, dataStatus: 'ok', missingReasons: [] }
})
onMounted(() => { void catalog.load() })
function catalogInfo(id: string | null): InstrumentInfo | string | null {
  return id ? (catalog.snapshot.value?.instruments ?? []).find((i) => i.instrument_id === id) ?? id : null
}

function sideZh(item: ActivityItem): string {
  const a = (item.action ?? item.side ?? '').toUpperCase()
  return ({ BUY:'买入', SELL:'卖出', WAIT:'观望', HOLD:'持有' }[a] ?? '未知')
}
function statusZh(item: ActivityItem): string {
  const map: Record<string, string> = {
    filled: '已成交', partially_filled: '部分成交', cancelled: '已撤销', rejected: '已拒绝',
    submitted: '已提交', pending: '待处理', expired: '已过期', unknown: '未知',
    WAIT: '观望', BUY: '建议买入', SELL: '建议卖出',
  }
  const s = (item.status ?? '').toLowerCase()
  return map[s] ?? fmtDash(s || null)
}
function truncate(v: string | null, max = 60): string {
  if (v === null) return '—'
  return v.length > max ? v.slice(0, max) + '…' : v
}

const emptyTitle = computed(() =>
  kind.value === 'fill' ? '暂无成交' : `暂无${KINDS.find((k) => k.key === kind.value)?.label ?? ''}记录`)
const emptyHint = computed(() =>
  kind.value === 'fill'
    ? '可切到「决策」标签查看为什么没有交易；无成交不等于接口失败'
    : '该筛选范围没有记录')

// 详情抽屉
const detailOpen = ref(false)
const detailId = ref('')
const detailLoading = ref(false)
const detailRows = ref<{ k: string; v: string }[]>([])
const timeline = ref<{ label: string; time: string | null; record: Record<string, unknown> }[]>([])
let detailController: AbortController | null = null
let detailGeneration = 0
onScopeDispose(() => { window.clearTimeout(debounceTimer); detailController?.abort(); detailGeneration++ })
watch(detailOpen, (open) => { if (!open) { detailController?.abort(); detailGeneration++ } })
async function openDetail(item: ActivityItem): Promise<void> {
  detailController?.abort()
  const gen = ++detailGeneration
  detailController = new AbortController()
  timeline.value = []
  detailOpen.value = true
  detailId.value = item.id
  detailLoading.value = true
  detailRows.value = []
  try {
    const env = await fetchActivityDetail(kind.value, item.id, { signal: detailController.signal })
    if (gen !== detailGeneration) return
    timeline.value = (env.data?.timeline ?? []) as typeof timeline.value
    detailRows.value = Object.entries(env.data ?? {}).filter(([k]) => k !== 'timeline').map(([k, v]) => ({
      k, v: typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? ''),
    }))
  } catch {
    if (gen !== detailGeneration) return
    detailRows.value = []  // 错误态由 detailRows 空 + loading false 呈现
  } finally {
    if (gen === detailGeneration) detailLoading.value = false
  }
}
</script>

<style scoped>
.activity { display: flex; flex-direction: column; gap: var(--card-gap); }
.panel {
  background: var(--bg-card-1); border: var(--border-card);
  border-radius: var(--radius-card); padding: 16px 20px;
}
.tabs { display: flex; gap: 6px; margin-bottom: 10px; }
.tabs button {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-secondary);
  border-radius: var(--radius-small); padding: 5px 16px; cursor: pointer; font-size: var(--fs-caption);
}
.tabs button.on { color: var(--accent); border-color: var(--accent); }
.filters { display: flex; gap: 16px; align-items: center; margin-bottom: 12px; font-size: var(--fs-caption); color: var(--text-tertiary); flex-wrap: wrap; }
.filters input {
  background: var(--bg-card-2); border: 1px solid var(--border-weak); color: var(--text-primary);
  border-radius: var(--radius-small); padding: 4px 8px; margin-left: 6px;
}
.hint { color: var(--text-faint); font-size: var(--fs-caption); }
.act-table { width: 100%; border-collapse: collapse; font-size: var(--fs-caption); }
.act-table th, .act-table td { padding: 7px 10px; border-bottom: 1px solid var(--border-weak); text-align: left; }
.act-table th { color: var(--text-tertiary); font-weight: 500; }
.act-table .r { text-align: right; }
.row-link { cursor: pointer; }
.row-link:hover { background: var(--bg-card-2); }
.reason { color: var(--text-tertiary); }
.pager { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
.pager button {
  background: transparent; border: 1px solid var(--border-weak); color: var(--text-secondary);
  border-radius: var(--radius-small); padding: 4px 12px; cursor: pointer; font-size: var(--fs-caption);
}
.pager button:disabled { opacity: 0.4; cursor: not-allowed; }
.detail-kv { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.detail-kv dt { color: var(--text-tertiary); font-size: var(--fs-caption); }
.detail-kv dd { margin: 0; word-break: break-all; font-size: var(--fs-caption); }
</style>
