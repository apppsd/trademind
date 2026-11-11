<template>
  <div class="tm-pane">
    <div class="tm-sample-banner">
      <span class="tm-banner-name">TradeMind</span>
      <span class="tm-banner-tag">SAMPLE</span>
    </div>

    <OverviewPage v-if="tmPage === 'overview'" />
    <MarketPage v-else-if="tmPage === 'market'" />
    <AssetsPage v-else-if="tmPage === 'assets'" />
    <StrategyPage v-else-if="tmPage === 'strategy'" />
    <ActivityPage v-else-if="tmPage === 'activity'" />
    <ReportsPage v-else-if="tmPage === 'reports'" />
    <RunCenterPage v-else-if="tmPage === 'run'" />

    <!-- 运行流页（TASK-09b2b）：复用 OpsStreamPane，取样例数组中的 TradeMind 窗格 -->
    <OpsStreamPane v-else :pane="opsStreamSample[1]!" :motion="false" load-note="SAMPLE" />
  </div>
</template>

<script setup lang="ts">
// TradeMind 窗格内容切换（TASK-09a-2 建链，TASK-09b2b 接入运行流）：tm 侧七个页面复用路由同款页面组件，运行流复用 OpsStreamPane 样例窗格。
// 样例门控必须在任何子页面挂载前打开：setup 体内首个语句同步置位，卸载时关闭；
// workbenchApi 各取数函数在入口读 cockpitSampleGate，命中即返回本地样例信封，不发网络请求。
import { onUnmounted } from 'vue'
import OverviewPage from '../../pages/OverviewPage.vue'
import MarketPage from '../../pages/MarketPage.vue'
import AssetsPage from '../../pages/AssetsPage.vue'
import StrategyPage from '../../pages/StrategyPage.vue'
import ActivityPage from '../../pages/ActivityPage.vue'
import ReportsPage from '../../pages/ReportsPage.vue'
import RunCenterPage from '../../pages/RunCenterPage.vue'
import OpsStreamPane from '../OpsStreamPane.vue'
import { opsStreamSample } from '../../demo/opsStreamSample'
import { setCockpitSampleActive } from '../../services/cockpitSampleGate'
import { useCockpitNavigation } from '../../composables/useCockpitNavigation'

setCockpitSampleActive(true)
onUnmounted(() => setCockpitSampleActive(false))

const { tmPage } = useCockpitNavigation()
</script>

<style scoped>
/* 半窗格滚动容器：banner 常驻顶部，页面内容超高时在本窗格内滚动。 */
.tm-pane {
  height: 100%;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tm-sample-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.tm-banner-name {
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-primary);
}

.tm-banner-tag {
  font-size: var(--fs-caption);
  font-family: var(--font-mono);
  color: var(--text-faint);
  border: 1px solid var(--border-weak);
  border-radius: 999px;
  padding: 1px 8px;
  letter-spacing: 0.06em;
}
</style>
