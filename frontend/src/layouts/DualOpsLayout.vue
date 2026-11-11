<template>
  <div class="dual-ops-layout">
    <div class="dual-ops-pane"><slot name="left" /></div>
    <div class="dual-ops-pane"><slot name="right" /></div>
  </div>
</template>

<script setup lang="ts">
// 统一驾驶舱对开布局：左 trader / 右 TradeMind，两窗格独立滚动。
</script>

<style scoped>
.dual-ops-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--card-gap);
  flex: 1;
  min-height: 0;
}

.dual-ops-pane {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
}

@media (max-width: 900px) {
  .dual-ops-layout {
    grid-template-columns: 1fr;
  }
}
</style>
