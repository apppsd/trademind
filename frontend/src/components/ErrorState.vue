<template>
  <div class="error-state">
    <p class="error-title">{{ title }}</p>
    <p v-if="detail" class="error-detail">{{ detail }}</p>
    <button v-if="retryable" class="error-retry" type="button" @click="$emit('retry')">重试</button>
  </div>
</template>

<script setup lang="ts">
// 错误只显示分类中文文案与脱敏追踪标识；不输出原始异常/服务器HTML。
withDefaults(defineProps<{
  title?: string
  detail?: string | null
  retryable?: boolean
}>(), { title: '该模块暂时不可用', detail: null, retryable: false })
defineEmits<{ retry: [] }>()
</script>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 38px 20px;
  text-align: center;
}
.error-title { margin: 0; color: var(--danger); font-size: var(--fs-body); }
.error-detail { margin: 0; color: var(--text-faint); font-size: var(--fs-caption); max-width: 520px; }
.error-retry {
  margin-top: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(85, 199, 255, 0.3);
  border-radius: var(--radius-small);
  padding: 6px 20px;
  cursor: pointer;
  font-size: var(--fs-secondary);
}
.error-retry:hover { background: rgba(85, 199, 255, 0.22); }
</style>
