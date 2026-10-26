<template>
  <div class="metric-card">
    <div class="metric-head">
      <span class="metric-label">{{ label }}</span>
      <StatusBadge v-if="status" :label="status" />
    </div>
    <div class="metric-value-row">
      <!-- 未知口径显示 em dash，绝不把未知渲染为 0 -->
      <span class="metric-value num">{{ displayValue }}</span>
      <span v-if="displayValue !== '—' && unit" class="metric-unit">{{ unit }}</span>
    </div>
    <div class="metric-foot">
      <span v-if="subtext" class="metric-sub">{{ subtext }}</span>
      <span v-if="asOfLabel" class="metric-asof">截至 {{ asOfLabel }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'
import type { DataStatus } from '../types/common'

const props = withDefaults(defineProps<{
  label: string
  /** 字符串金额直接展示；null/undefined 表示暂不可计算（显示 —）。 */
  value?: string | number | null
  unit?: string
  subtext?: string | null
  asOfLabel?: string | null
  status?: DataStatus | null
}>(), { value: null, unit: '', subtext: null, asOfLabel: null, status: null })

const displayValue = computed(() => {
  if (props.value === null || props.value === undefined) return '—'
  return String(props.value)
})
</script>

<style scoped>
.metric-card {
  background: var(--bg-card-1);
  border: var(--border-card);
  border-radius: var(--radius-card);
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.metric-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.metric-label { color: var(--text-secondary); font-size: var(--fs-secondary); }
.metric-value-row { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.metric-value {
  font-size: var(--fs-metric);
  font-weight: 600;
  letter-spacing: 0.01em;
  line-height: 1.15;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.metric-unit { color: var(--text-secondary); font-size: var(--fs-metric-unit); }
.metric-foot {
  display: flex; justify-content: space-between; gap: 8px;
  font-size: var(--fs-caption); color: var(--text-faint);
  min-height: 16px;
}
.metric-sub { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
