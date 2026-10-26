<template>
  <span class="instrument-label" :title="[info?.full_name, info?.market, info?.kind].filter(Boolean).join(' · ')">
    <span v-if="info" class="inst-name">{{ info.display_name ?? '名称待补充' }}</span>
    <span v-else class="inst-name pending">名称待补充</span>
    <button
      v-if="showCode || !info?.display_name" class="inst-code mono" type="button"
      :title="`复制代码 ${code}`"
      @click="copyCode">{{ code }}</button>
  </span>
</template>

<script setup lang="ts">
// 统一标的展示：中文名为主，代码次级可复制；URL/请求仍用 instrument_id。
import { computed } from 'vue'
import type { InstrumentInfo } from '../types/instrument'

const props = withDefaults(defineProps<{
  /** 未知标的传 instrument_id 字符串，安全退化为“名称待补充+代码”。 */
  info: InstrumentInfo | string | null
  showCode?: boolean
}>(), { showCode: false })

const info = computed<InstrumentInfo | null>(() =>
  typeof props.info === 'object' && props.info !== null ? props.info : null)
const code = computed(() =>
  typeof props.info === 'object' && props.info !== null
    ? props.info.instrument_id
    : typeof props.info === 'string' ? props.info : '')

async function copyCode() {
  try { await navigator.clipboard.writeText(code.value) } catch { /* 剪贴板不可用时静默 */ }
}
</script>

<style scoped>
.instrument-label { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.inst-name {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inst-name.pending { color: var(--text-secondary); }
.inst-code {
  background: transparent;
  border: 1px solid var(--border-weak);
  color: var(--text-faint);
  font-size: var(--fs-caption);
  padding: 1px 8px;
  border-radius: 6px;
  cursor: copy;
  white-space: nowrap;
}
.inst-code:hover { color: var(--text-secondary); border-color: var(--border-strong); }
</style>
