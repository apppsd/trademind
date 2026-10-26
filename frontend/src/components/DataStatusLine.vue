<template>
  <div class="dsl" :class="{ stale }">
    <StatusBadge :label="statusLabel" :tone="tone" />
    <span v-if="timeText" class="dsl-time num">截至 {{ timeText }}<template v-if="ageText">（{{ ageText }}）</template></span>
    <span v-if="stale" class="dsl-chip">数据陈旧，显示最后成功快照</span>
    <ul v-if="missingReasons.length" class="dsl-missing">
      <li v-for="reason in missingReasons" :key="reason">{{ reason }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import { ageLabel, shanghaiTime } from '../utils/format'

const props = withDefaults(defineProps<{
  dataStatus: string | null
  asOf: string | null
  ageSeconds?: number | null
  missingReasons?: string[]
  stale?: boolean
}>(), { ageSeconds: null, missingReasons: () => [], stale: false })

const TONE: Record<string, 'ok' | 'warn' | 'danger'> = {
  ok: 'ok', partial: 'warn', stale: 'warn', unavailable: 'danger', error: 'danger',
}

const LABEL: Record<string, string> = {
  ok: '数据完整', partial: '部分可用', stale: '数据陈旧', unavailable: '暂不可用', error: '数据错误',
}

const statusLabel = computed(() =>
  props.dataStatus && LABEL[props.dataStatus] ? LABEL[props.dataStatus] : '状态未知')
const tone = computed(() =>
  (props.dataStatus && TONE[props.dataStatus]) || 'warn')
const timeText = computed(() => shanghaiTime(props.asOf))
const ageText = computed(() => ageLabel(props.ageSeconds ?? null))
</script>

<style scoped>
.dsl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: var(--fs-caption); color: var(--text-tertiary); }
.dsl-time { font-variant-numeric: tabular-nums; }
.dsl-chip {
  color: var(--warn); border: 1px solid rgba(245, 176, 77, 0.4);
  border-radius: 999px; padding: 0 8px;
}
.dsl-missing { margin: 0; padding: 0; list-style: none; width: 100%; }
.dsl-missing li::before { content: '· '; }
</style>
