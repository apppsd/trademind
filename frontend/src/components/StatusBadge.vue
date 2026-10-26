<template>
  <span class="badge" :data-tone="tone">
    <span v-if="dot" class="badge-dot" aria-hidden="true"></span>{{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DataStatus } from '../types/common'

const props = withDefaults(defineProps<{
  label: string
  /** ok=正常 cyan；warn=陈旧/部分；danger=不可用；muted=未知/演示。 */
  tone?: 'ok' | 'warn' | 'danger' | 'muted'
  dot?: boolean
}>(), { tone: 'muted', dot: false })

// data_status 到徽标色调的确定性映射；未知值按 muted 处理。
const STATUS_TONE: Record<DataStatus, 'ok' | 'warn' | 'danger' | 'muted'> = {
  ok: 'ok', partial: 'warn', stale: 'warn', unavailable: 'danger', error: 'danger',
}
const tone = computed(() =>
  (['ok', 'partial', 'stale', 'unavailable', 'error'] as DataStatus[]).includes(props.label as DataStatus)
    ? STATUS_TONE[props.label as DataStatus]
    : props.tone)
</script>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-caption);
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--border-weak);
  color: var(--text-secondary);
  white-space: nowrap;
}
.badge-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.badge[data-tone='ok'] { color: var(--cyan); border-color: rgba(51, 214, 198, 0.35); }
.badge[data-tone='warn'] { color: var(--warn); border-color: rgba(245, 176, 77, 0.35); }
.badge[data-tone='danger'] { color: var(--danger); border-color: rgba(255, 77, 79, 0.4); }
.badge[data-tone='muted'] { color: var(--text-faint); }
</style>
