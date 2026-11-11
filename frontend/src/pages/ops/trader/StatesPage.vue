<template>
  <div class="trader-states">
    <!-- 运行/质量摘要：样例 recentRuns 按结果计数，口径用 SAMPLE 徽标 + DataStatusLine -->
    <section class="pane-card">
      <div class="summary-row">
        <StatusBadge :label="sample.meta.source" />
        <ul class="run-counts">
          <li v-for="result in RUN_RESULTS" :key="result">
            <StatusBadge :label="RUN_LABEL[result]" :tone="RUN_TONE[result]" />
            <span class="num">{{ runCounts[result] }}</span>
          </li>
        </ul>
        <DataStatusLine data-status="ok" :as-of="sample.meta.asOf" />
      </div>
    </section>

    <!-- 六种数据状态演示卡：empty 用 EmptyState、error 用 ErrorState，其余为密集卡片 -->
    <div class="state-grid">
      <section
        v-for="[key, demo] in stateDemos" :key="key"
        class="pane-card state-card" :data-state="key"
      >
        <div class="state-head">
          <StatusBadge :label="key" />
          <strong v-if="key !== 'empty' && key !== 'error'" class="state-title">{{ demo.title }}</strong>
        </div>
        <EmptyState v-if="key === 'empty'" :title="demo.title" :hint="demo.message" />
        <ErrorState v-else-if="key === 'error'" :title="demo.title" :detail="demo.message" :retryable="false" />
        <p v-else class="state-msg">{{ demo.message }}</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// trader/状态演示：SAMPLE 演示页（TASK-08c）。展示样例运行质量摘要与六种数据状态文案，
// 全部来自前端样例，不接真实链路，也不触发任何请求。
import StatusBadge from '../../../components/StatusBadge.vue'
import EmptyState from '../../../components/EmptyState.vue'
import ErrorState from '../../../components/ErrorState.vue'
import DataStatusLine from '../../../components/DataStatusLine.vue'
import { traderCockpitSample as sample } from '../../../demo/traderCockpitSample'

const RUN_RESULTS = ['completed', 'partial', 'failed'] as const
type RunResult = (typeof RUN_RESULTS)[number]

const RUN_LABEL: Record<RunResult, string> = { completed: '完成', partial: '部分', failed: '失败' }
const RUN_TONE: Record<RunResult, 'ok' | 'warn' | 'danger'> = {
  completed: 'ok', partial: 'warn', failed: 'danger',
}

/** recentRuns 按结果计数（样例含 completed/partial，failed 允许为 0）。 */
const runCounts: Record<RunResult, number> = { completed: 0, partial: 0, failed: 0 }
for (const run of sample.recentRuns) runCounts[run.result] += 1

/** Object.entries 保持定义顺序：loading / empty / missing / stale / error / unknown。 */
const stateDemos = Object.entries(sample.stateDemos)
</script>

<style scoped>
.trader-states { display: flex; flex-direction: column; gap: 8px; }

.pane-card {
  background: var(--bg-inset);
  border: var(--border-card);
  border-radius: var(--radius-small);
  padding: 10px 12px;
  min-width: 0;
}

.summary-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.run-counts {
  list-style: none; margin: 0; padding: 0;
  display: flex; gap: 12px;
  font-size: var(--fs-caption);
}
.run-counts li { display: inline-flex; align-items: center; gap: 5px; color: var(--text-secondary); }

.state-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: start; }

.state-head { display: flex; align-items: center; gap: 8px; }
.state-title { font-size: var(--fs-caption); font-weight: 600; color: var(--text-primary); }

.state-msg {
  margin: 6px 0 0;
  font-size: var(--fs-caption); line-height: 1.6;
  color: var(--text-secondary);
}

/* empty/error 演示卡压低组件默认内边距，保持半窗格密度 */
.state-card :deep(.empty-state),
.state-card :deep(.error-state) { padding: 16px 12px; min-height: 0; }

@media (max-width: 1100px) {
  .state-grid { grid-template-columns: 1fr; }
}
</style>
