<template>
  <div class="wb-shell">
    <header class="wb-topbar">
      <div class="wb-brand">
        <span class="wb-brand-mark" aria-hidden="true"></span>
        <span class="wb-brand-name">TradeMind</span>
        <span class="wb-brand-sub">中文大屏工作台</span>
      </div>
      <nav class="wb-nav" aria-label="主导航">
        <RouterLink
          v-for="item in NAV_ITEMS" :key="item.to" :to="item.to"
          class="wb-nav-link" :class="{ active: isActive(item.to) }">
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="wb-topbar-right">
        <span class="wb-clock num">{{ shanghaiTime }}</span>
        <span class="wb-phase">{{ effectivePhase }}</span>
        <slot name="topbar-actions" />
      </div>
    </header>

    <div class="wb-status-strip" :data-tone="stripTone">
      <span class="wb-strip-dot" aria-hidden="true"></span>
      <span><span v-if="accountAlias">账户：{{ accountAlias }} · </span>{{ stripText }}</span>
      <span v-if="dataUpdatedLabel" class="wb-strip-time">数据更新 {{ dataUpdatedLabel }}</span>
    </div>

    <main class="wb-main">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { apiOnline, phaseLabel, accountAlias } from '../services/online'

const NAV_ITEMS = [
  { to: '/', label: '总览' },
  { to: '/market', label: '行情' },
  { to: '/assets', label: '资产' },
  { to: '/strategy', label: '策略' },
  { to: '/activity', label: '交易记录' },
  { to: '/reports', label: '日报' },
  { to: '/run', label: '运行中心' },
] as const

/** 'live'：连接真实只读接口；'demo'：离线演示。任一接口成功后自动切 live。 */
const props = withDefaults(defineProps<{
  mode?: 'live' | 'demo'
  phaseLabel?: string
  dataUpdatedLabel?: string | null
  degraded?: boolean
}>(), {
  mode: 'demo',
  phaseLabel: '',
  dataUpdatedLabel: null,
  degraded: false,
})

const route = useRoute()
const isActive = (to: string) => route.path === to

const effectiveMode = computed(() => (props.mode === 'demo' && apiOnline.value ? 'live' : props.mode))
const effectivePhase = computed(() => {
  if (effectiveMode.value === 'demo') return '尚未连接'
  return props.phaseLabel || phaseLabel.value || '状态获取中'
})

const now = ref(new Date())
let timer: number | undefined
onMounted(() => { timer = window.setInterval(() => { now.value = new Date() }, 1000) })
onUnmounted(() => { if (timer !== undefined) window.clearInterval(timer) })

const shanghaiTime = computed(() => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hour12: false,
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
}).format(now.value))

const stripTone = computed(() => (props.degraded ? 'warn' : effectiveMode.value === 'live' ? 'ok' : 'demo'))
const stripText = computed(() => {
  if (props.degraded) return '数据陈旧 · 展示最后成功快照'
  return effectiveMode.value === 'live' ? '本地只读快照 · 行情来源与模型能力未实时核验' : '尚未连接本地服务 · 数据来源未确认'
})
</script>

<style scoped>
.wb-shell {
  max-width: var(--page-max);
  margin: 0 auto;
  padding: 0 var(--page-pad) 32px;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.wb-topbar {
  display: flex;
  align-items: center;
  gap: 28px;
  height: 60px;
  border-bottom: 1px solid var(--border-weak);
}

.wb-brand { display: flex; align-items: baseline; gap: 10px; white-space: nowrap; }
.wb-brand-mark {
  width: 10px; height: 10px; border-radius: 3px;
  background: linear-gradient(135deg, var(--accent), var(--cyan));
  align-self: center;
}
.wb-brand-name { font-size: 17px; font-weight: 600; letter-spacing: 0.02em; }
.wb-brand-sub { font-size: var(--fs-caption); color: var(--text-faint); }

.wb-nav { display: flex; gap: 4px; flex: 1; justify-content: center; }
.wb-nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  padding: 7px 16px;
  border-radius: var(--radius-small);
  border: 1px solid transparent;
  transition: color var(--motion-fast) var(--ease-out), background var(--motion-fast) var(--ease-out);
}
.wb-nav-link:hover { color: var(--text-primary); background: var(--bg-hover); }
.wb-nav-link.active {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: rgba(85, 199, 255, 0.25);
}

.wb-topbar-right { display: flex; align-items: center; gap: 14px; white-space: nowrap; }
.wb-clock { color: var(--text-secondary); font-size: var(--fs-secondary); }
.wb-phase {
  font-size: var(--fs-caption);
  color: var(--cyan);
  border: 1px solid rgba(51, 214, 198, 0.3);
  border-radius: 999px;
  padding: 3px 12px;
}

.wb-status-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  font-size: var(--fs-caption);
  color: var(--text-secondary);
}
.wb-strip-dot { width: 7px; height: 7px; border-radius: 50%; }
.wb-status-strip[data-tone='ok'] .wb-strip-dot { background: var(--cyan); }
.wb-status-strip[data-tone='demo'] .wb-strip-dot { background: var(--text-faint); }
.wb-status-strip[data-tone='warn'] .wb-strip-dot { background: var(--warn); }
.wb-status-strip[data-tone='warn'] { color: var(--warn); }
.wb-strip-time { color: var(--text-faint); }

.wb-main { flex: 1; padding-top: var(--card-gap); }
</style>
