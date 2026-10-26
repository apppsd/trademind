<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-backdrop" @click.self="$emit('close')">
      <aside
        class="drawer-panel" role="dialog" aria-modal="true" :aria-label="title"
        ref="panelEl">
        <header class="drawer-head">
          <h2 class="drawer-title">{{ title }}</h2>
          <button class="drawer-close" type="button" aria-label="关闭详情" @click="$emit('close')">✕</button>
        </header>
        <div class="drawer-body">
          <slot />
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// 短详情右侧抽屉；图表与长策略用完整页（需求第5节，避免多层嵌套抽屉）。
import { onUnmounted, ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
}>()
const emit = defineEmits<{ close: [] }>()

const panelEl = ref<HTMLElement | null>(null)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
watch(() => props.open, (open) => {
  if (open) {
    window.addEventListener('keydown', onKeydown)
    // 焦点进入抽屉，支持键盘关闭
    requestAnimationFrame(() => panelEl.value?.focus())
  } else {
    window.removeEventListener('keydown', onKeydown)
  }
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(4, 8, 16, 0.6);
  backdrop-filter: blur(2px);
  z-index: var(--z-drawer);
  display: flex;
  justify-content: flex-end;
}
.drawer-panel {
  width: min(480px, 92vw);
  height: 100vh;
  background: var(--bg-card-2);
  border-left: 1px solid var(--border-weak);
  display: flex;
  flex-direction: column;
  outline: none;
  animation: drawer-in var(--motion-slow) var(--ease-out);
}
@keyframes drawer-in {
  from { transform: translateX(24px); opacity: 0; }
  to { transform: none; opacity: 1; }
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 22px;
  border-bottom: 1px solid var(--border-weak);
}
.drawer-title { margin: 0; font-size: var(--fs-title); font-weight: 600; }
.drawer-close {
  background: transparent;
  border: 1px solid var(--border-weak);
  color: var(--text-secondary);
  border-radius: var(--radius-small);
  width: 30px; height: 30px;
  cursor: pointer;
}
.drawer-close:hover { color: var(--text-primary); }
.drawer-body { padding: 18px 22px; overflow-y: auto; flex: 1; }
</style>
