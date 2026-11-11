<template>
  <div class="trader-explorer">
    <div class="filter-row">
      <input v-model="keyword" class="filter-input" type="search"
        placeholder="筛选：代码 / 名称 / 备注" aria-label="筛选候选标的" />
      <span class="filter-count">{{ filtered.length }} / {{ sample.candidates.length }}</span>
    </div>

    <section class="pane-card">
      <h3 class="card-title">计划区（plan）<span class="muted-note">{{ planRows.length }} 只</span></h3>
      <table v-if="planRows.length" class="dense-table row-click">
        <thead>
          <tr><th>代码</th><th>名称</th><th class="r">分值</th><th>备注</th><th class="r">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in planRows" :key="c.code" class="clickable" tabindex="0"
            :aria-label="`查看标的 ${c.name}（${c.code}）`"
            @click="openInstrument(c.code)" @keydown.enter="openInstrument(c.code)">
            <td class="mono">{{ c.code }}</td>
            <td>{{ c.name }}</td>
            <td class="r score-cell">
              <span class="score-track" aria-hidden="true"><i class="score-bar" :style="{ width: c.score + '%' }"></i></span>
              <span>{{ c.score }}</span>
            </td>
            <td class="note-cell">{{ c.note ?? '—' }}</td>
            <td class="r go">查看 →</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="table-hint">无匹配候选。</p>
    </section>

    <section class="pane-card">
      <h3 class="card-title">观察区（observe）<span class="muted-note">{{ observeRows.length }} 只</span></h3>
      <table v-if="observeRows.length" class="dense-table row-click">
        <thead>
          <tr><th>代码</th><th>名称</th><th class="r">分值</th><th>备注</th><th class="r">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in observeRows" :key="c.code" class="clickable" tabindex="0"
            :aria-label="`查看标的 ${c.name}（${c.code}）`"
            @click="openInstrument(c.code)" @keydown.enter="openInstrument(c.code)">
            <td class="mono">{{ c.code }}</td>
            <td>{{ c.name }}</td>
            <td class="r score-cell">
              <span class="score-track" aria-hidden="true"><i class="score-bar" :style="{ width: c.score + '%' }"></i></span>
              <span>{{ c.score }}</span>
            </td>
            <td class="note-cell">{{ c.note ?? '—' }}</td>
            <td class="r go">查看 →</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="table-hint">无匹配候选。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
// trader/探索：SAMPLE 演示页（TASK-08b）。plan / observe 两区分列候选，行点击 → 标的页。
import { computed, ref } from 'vue'
import { traderCockpitSample as sample } from '../../../demo/traderCockpitSample'
import { useCockpitNavigation } from '../../../composables/useCockpitNavigation'

const { setTraderPage } = useCockpitNavigation()

const keyword = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return sample.candidates
  return sample.candidates.filter((c) =>
    c.code.toLowerCase().includes(kw)
    || c.name.toLowerCase().includes(kw)
    || (c.note ?? '').toLowerCase().includes(kw))
})
const planRows = computed(() => filtered.value.filter((c) => c.bucket === 'plan'))
const observeRows = computed(() => filtered.value.filter((c) => c.bucket === 'observe'))

/** 切到 instrument 页并带上 traderInstrument=code；其余 query 键（含 tm）原样保留。 */
function openInstrument(code: string) {
  setTraderPage('instrument', { traderInstrument: code })
}
</script>

<style scoped>
.trader-explorer { display: flex; flex-direction: column; gap: 8px; }

.filter-row { display: flex; align-items: center; gap: 8px; }
.filter-input {
  flex: 1; min-width: 0;
  background: var(--bg-inset);
  border: 1px solid var(--border-weak);
  border-radius: var(--radius-small);
  padding: 5px 10px;
  font-size: var(--fs-caption);
  color: var(--text-primary);
}
.filter-input::placeholder { color: var(--text-faint); }
.filter-count { flex: none; font-size: var(--fs-caption); color: var(--text-faint); font-variant-numeric: tabular-nums; }

.pane-card {
  background: var(--bg-inset);
  border: var(--border-card);
  border-radius: var(--radius-small);
  padding: 10px 12px;
  min-width: 0;
}

.card-title {
  margin: 0 0 8px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: var(--fs-caption); font-weight: 600; color: var(--text-secondary);
}
.muted-note { font-weight: 400; color: var(--text-faint); }

.dense-table { width: 100%; border-collapse: collapse; font-size: var(--fs-caption); }
.dense-table th {
  text-align: left; font-weight: 400; color: var(--text-faint);
  padding: 3px 6px; border-bottom: 1px solid var(--border-weak); white-space: nowrap;
}
.dense-table td {
  padding: 4px 6px; vertical-align: top; color: var(--text-secondary);
  border-bottom: 1px solid rgba(142, 165, 199, 0.07);
}
.dense-table tbody tr:last-child td { border-bottom: none; }
.r { text-align: right; }

.score-cell { white-space: nowrap; }
.score-track {
  display: inline-block; width: 56px; height: 4px; margin-right: 6px;
  background: var(--bg-card-2); border-radius: 2px; overflow: hidden; vertical-align: middle;
}
.score-bar { display: block; height: 100%; background: var(--accent); border-radius: 2px; }

.note-cell { color: var(--text-faint); }

.row-click .clickable { cursor: pointer; }
.row-click .clickable:hover td { background: var(--bg-hover); }
.row-click .clickable:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.go { color: var(--accent); white-space: nowrap; }

.table-hint { margin: 0; font-size: var(--fs-caption); color: var(--text-faint); }
</style>
