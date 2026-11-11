<template>
  <OverviewPage v-if="traderPage === 'overview'" />
  <AccountPage v-else-if="traderPage === 'account'" />
  <ExplorerPage v-else-if="traderPage === 'explorer'" />
  <StatesPage v-else-if="traderPage === 'states'" />
  <InstrumentPage v-else-if="traderPage === 'instrument'" />

  <!-- 运行流为 OpsStreamPane 本地 SAMPLE（TASK-09b2）：复用脱敏样例信封，不请求后台；完整运行流页后续接入 -->
  <OpsStreamPane v-else :pane="opsStreamSample[0]!" :motion="false" load-note="SAMPLE" />
</template>

<script setup lang="ts">
// trader 窗格内容切换（TASK-08b/08c/09b2）：五个演示页；运行流为 OpsStreamPane 本地 SAMPLE。
// 页面键来自 useCockpitNavigation（/cockpit?trader=…），与左侧导航共用同一 query 口径。
import OverviewPage from '../../pages/ops/trader/OverviewPage.vue'
import AccountPage from '../../pages/ops/trader/AccountPage.vue'
import ExplorerPage from '../../pages/ops/trader/ExplorerPage.vue'
import StatesPage from '../../pages/ops/trader/StatesPage.vue'
import InstrumentPage from '../../pages/ops/trader/InstrumentPage.vue'
import OpsStreamPane from '../OpsStreamPane.vue'
import { opsStreamSample } from '../../demo/opsStreamSample'
import { useCockpitNavigation } from '../../composables/useCockpitNavigation'

const { traderPage } = useCockpitNavigation()
</script>
