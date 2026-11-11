<template>
  <div class="cockpit-page">
    <DualOpsLayout>
      <template #left>
        <OpsPaneFrame
          title="Trader"
          subtitle="交易执行侧 · 只读观察"
          ariaNavLabel="Trader 窗格导航">
          <template #nav>
            <button
              v-for="p in TRADER_PAGES" :key="p"
              type="button"
              class="cockpit-nav-btn"
              :class="{ active: traderPage === p }"
              :aria-pressed="traderPage === p"
              @click="setTraderPage(p)">
              {{ TRADER_LABELS[p] }}
            </button>
          </template>
          <TraderPane />
        </OpsPaneFrame>
      </template>

      <template #right>
        <OpsPaneFrame
          title="TradeMind"
          subtitle="行情与策略侧 · 只读观察"
          ariaNavLabel="TradeMind 窗格导航">
          <template #nav>
            <button
              v-for="p in TM_PAGES" :key="p"
              type="button"
              class="cockpit-nav-btn"
              :class="{ active: tmPage === p }"
              :aria-pressed="tmPage === p"
              @click="setTmPage(p)">
              {{ TM_LABELS[p] }}
            </button>
          </template>
          <TradeMindPane />
        </OpsPaneFrame>
      </template>
    </DualOpsLayout>
  </div>
</template>

<script setup lang="ts">
import DualOpsLayout from '../layouts/DualOpsLayout.vue'
import OpsPaneFrame from '../components/ops/OpsPaneFrame.vue'
import TraderPane from '../components/ops/TraderPane.vue'
import TradeMindPane from '../components/ops/TradeMindPane.vue'
import { TRADER_PAGES, TM_PAGES, useCockpitNavigation } from '../composables/useCockpitNavigation'
import type { TraderPage, TmPage } from '../composables/useCockpitNavigation'

const { traderPage, tmPage, setTraderPage, setTmPage } = useCockpitNavigation()

const TRADER_LABELS: Record<TraderPage, string> = {
  overview: '总览',
  account: '账户',
  instrument: '标的',
  explorer: '探索',
  states: '状态',
  stream: '运行流',
}

const TM_LABELS: Record<TmPage, string> = {
  overview: '总览',
  market: '行情',
  assets: '资产',
  strategy: '策略',
  activity: '交易记录',
  reports: '日报',
  run: '运行中心',
  stream: '运行流',
}
</script>

<style scoped>
/* 驾驶舱嵌在 WorkbenchLayout 顶栏之下：撑满剩余视口，内部交给 DualOpsLayout / OpsPaneFrame 滚动。 */
.cockpit-page {
  flex: 1;
  min-height: 0;
  box-sizing: border-box;
  padding: var(--page-pad);
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
}

.cockpit-nav-btn {
  appearance: none;
  border: 1px solid transparent;
  border-radius: var(--radius-small);
  background: transparent;
  padding: 4px 12px;
  font-size: var(--fs-caption);
  color: var(--text-secondary);
  cursor: pointer;
  transition: color var(--motion-fast) var(--ease-out), background var(--motion-fast) var(--ease-out);
}

.cockpit-nav-btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.cockpit-nav-btn.active {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: rgba(85, 199, 255, 0.25);
}
</style>
