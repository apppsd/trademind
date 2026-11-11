<template>
  <section class="ops-pane">
    <header class="ops-pane-head">
      <div class="ops-pane-title-row">
        <h2 class="ops-pane-title">{{ title }}</h2>
        <span class="ops-pane-source" :title="`${sourceLabel} 数据源标识`">{{ sourceLabel }}</span>
      </div>
      <p v-if="subtitle" class="ops-pane-subtitle">{{ subtitle }}</p>
    </header>
    <nav class="ops-pane-nav" :aria-label="ariaNavLabel">
      <slot name="nav" />
    </nav>
    <div class="ops-pane-body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  subtitle?: string
  sourceLabel?: string
  ariaNavLabel: string
}>(), {
  subtitle: '',
  sourceLabel: 'SAMPLE',
})
</script>

<style scoped>
/* 半窗格密度：紧凑标题、小字号、窄间距，为左右对开布局设计。 */
.ops-pane {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card-1);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-small);
  overflow: hidden;
}

.ops-pane-head {
  padding: 8px 12px 0;
}

.ops-pane-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.ops-pane-title {
  margin: 0;
  font-size: var(--fs-secondary);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.ops-pane-source {
  flex: none;
  font-size: var(--fs-caption);
  font-family: var(--font-mono);
  color: var(--text-faint);
  border: 1px solid var(--border-weak);
  border-radius: 999px;
  padding: 1px 8px;
  letter-spacing: 0.06em;
}

.ops-pane-subtitle {
  margin: 2px 0 0;
  font-size: var(--fs-caption);
  color: var(--text-faint);
}

.ops-pane-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border-weak);
}

.ops-pane-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px 12px;
  font-size: var(--fs-secondary);
  color: var(--text-secondary);
}
</style>
