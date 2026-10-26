<template>
  <div class="empty-state">
    <div class="empty-icon" aria-hidden="true"></div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="hint" class="empty-hint">{{ hint }}</p>
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  /** 中文空态标题，如“暂无成交”“尚无有效计划”。 */
  title: string
  /** 可执行的建议入口文案（说明下一步看哪里）。 */
  hint?: string | null
}>(), { hint: null })
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 42px 20px;
  text-align: center;
  min-height: 180px;
}
.empty-icon {
  width: 40px; height: 40px;
  border-radius: 50%;
  border: 1px dashed var(--border-weak);
  background: var(--bg-inset);
  margin-bottom: 4px;
}
.empty-title { margin: 0; color: var(--text-secondary); font-size: var(--fs-body); }
.empty-hint { margin: 0; color: var(--text-faint); font-size: var(--fs-caption); max-width: 460px; }
</style>
