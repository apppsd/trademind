<template>
  <div class="ops-cockpit" :data-motion="motionActive ? 'on' : 'off'">
    <header class="ops-header">
      <div><p class="ops-kicker">UNIFIED OPS COCKPIT / READ-ONLY</p><h1>总控运行流 <span>Ops Stream</span></h1><p>左 trader · 右 TradeMind · 单一只读驾驶舱</p></div>
      <div class="header-actions">
        <button class="reload" type="button" :disabled="reloading" @click="reload">{{ reloading ? '读取中…' : '刷新只读源' }}</button>
        <label class="motion-toggle"><input v-model="motionEnabled" type="checkbox" :disabled="reducedMotion" />示意动效 {{ motionActive ? '开启' : '关闭' }}<small>{{ reducedMotion ? '系统已要求减少动效' : '可关闭粒子；隐藏页面自动暂停' }}</small></label>
      </div>
    </header>
    <div class="sample-banner" role="note"><strong>{{ bannerTitle }}</strong> {{ bannerText }}</div>
    <div class="ops-split">
      <OpsStreamPane v-for="(pane, i) in panes" :key="pane.project" :pane="pane" :motion="motionActive" :load-note="loadNotes[i] ?? null" />
    </div>
    <p class="ops-footnote">独立来源 / 独立账本 / 独立时点 · 不构造跨项目因果 · 白名单 GET 失败或未配置时该窗独立回退样例</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import OpsStreamPane from '../components/OpsStreamPane.vue'
import { opsStreamSample, type OpsPane } from '../demo/opsStreamSample'
import { loadTraderPane, loadTradeMindPane } from '../adapters/opsReadonly'
function savedMotion(): boolean {
  try { return localStorage.getItem('ops-stream-motion') !== 'off' } catch { return false }
}
const motionEnabled = ref(savedMotion())
const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
const reducedMotion = ref(media?.matches ?? false)
const visible = ref(!document.hidden)
const motionActive = computed(() => motionEnabled.value && !reducedMotion.value && visible.value)
const updateVisibility = () => { visible.value = !document.hidden }
const updatePreference = () => { reducedMotion.value = media?.matches ?? false }

// 只读白名单 GET：挂载与手动刷新各一次，无轮询（TASK-06 才负责轮询/退避）。
// 每窗独立成功或失败；失败/未配置 → 该窗回退固定 SAMPLE，不拼半真半假数据。
const panes = ref<OpsPane[]>(opsStreamSample.map(pane => ({ ...pane })))
const loadNotes = ref<(string | null)[]>([null, null])
const reloading = ref(false)
let controller: AbortController | undefined

function fallbackPane(index: 0 | 1, reason: string): OpsPane {
  return { ...opsStreamSample[index]!, notes: [reason] }
}
function applyPane(index: 0 | 1, live: OpsPane | null, project: string) {
  if (live) { panes.value[index] = live; loadNotes.value[index] = null; return }
  panes.value[index] = fallbackPane(index, `${project} 白名单源未配置或读取失败，本窗显示固定样例（SAMPLE）。`)
}
async function reload() {
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  reloading.value = true
  loadNotes.value = ['正在读取只读白名单源…', '正在读取只读白名单源…']
  const [trademind, trader] = await Promise.allSettled([loadTradeMindPane(signal), loadTraderPane(signal)])
  if (signal.aborted) return
  applyPane(1, trademind.status === 'fulfilled' ? trademind.value : null, 'TradeMind')
  applyPane(0, trader.status === 'fulfilled' ? trader.value : null, 'trader')
  loadNotes.value = [null, null]
  reloading.value = false
}
onMounted(() => {
  updatePreference()
  media?.addEventListener('change', updatePreference)
  document.addEventListener('visibilitychange', updateVisibility)
  try { motionEnabled.value = localStorage.getItem('ops-stream-motion') !== 'off' } catch { /* 存储禁用时仍可切换 */ }
  void reload()
})
watch(motionEnabled, value => {
  try { localStorage.setItem('ops-stream-motion', value ? 'on' : 'off') } catch { /* 当前会话偏好仍有效 */ }
})
onUnmounted(() => {
  controller?.abort()
  media?.removeEventListener('change', updatePreference)
  document.removeEventListener('visibilitychange', updateVisibility)
})

const liveCount = computed(() => panes.value.filter(pane => !pane.sample).length)
const bannerTitle = computed(() => (liveCount.value === 2 ? '实时只读 / LIVE' : '样例 / SAMPLE'))
const bannerText = computed(() => {
  if (liveCount.value === 2) return '双窗均已连接本地只读白名单 GET · 挂载/手动刷新各一次 · 无轮询 · 粒子仅示意 · 无任何交易写入'
  if (liveCount.value === 1) return '双窗中 1 窗为实时只读，另 1 窗因未配置或读取失败显示固定样例；两窗独立失败，互不拖垮 · 无轮询'
  return '双窗固定离线样例 · 只读白名单 GET 未配置或失败时按窗独立回退 · 粒子仅示意 · 不代表 Wang/150 运行状态'
})
</script>

<style scoped>
.ops-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px; padding: 8px 0 22px; }
.ops-kicker { font-size: 11px; letter-spacing: .16em; color: #7f9fb9; margin: 0; }
h1 { margin: 9px 0; font-size: clamp(24px, 3vw, 38px); font-weight: 600; letter-spacing: .04em; }
h1 span { font-size: .5em; color: #8c9fba; white-space: nowrap; }
.ops-header p:last-child { margin: 0; color: #a7b5c9; font-size: 13px; }
.header-actions { display: flex; align-items: stretch; flex-wrap: wrap; gap: 12px; }
.reload { padding: 12px 16px; border: 1px solid #3c536c; border-radius: 10px; font-size: 13px; cursor: pointer; background: #152238; color: inherit; }
.reload:hover:not(:disabled) { border-color: #33d6c6; color: #33d6c6; }
.reload:disabled { opacity: .6; cursor: default; }
.motion-toggle { padding: 12px 16px; border: 1px solid #3c536c; border-radius: 10px; font-size: 13px; cursor: pointer; }
.motion-toggle input { accent-color: #33d6c6; margin-right: 8px; }
.motion-toggle small { display: block; margin-top: 5px; font-size: 11px; color: #9eacc0; }
.sample-banner { padding: 12px 16px; margin-bottom: 20px; border: 1px solid #f5b04d55; background: #f5b04d0c; border-radius: 10px; color: #e9c18a; font-size: 12px; }
.sample-banner strong { margin-right: 16px; }
.ops-split { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 20px; align-items: start; }
.ops-footnote { text-align: center; color: #8d9eb4; font-size: 11px; margin: 22px 0 0; }
@media (max-width: 850px) { .ops-split { grid-template-columns: minmax(0,1fr); } }
</style>
