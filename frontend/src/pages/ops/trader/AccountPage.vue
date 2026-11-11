<template>
  <div class="trader-account">
    <section class="pane-card">
      <h3 class="card-title">
        当前持仓
        <span class="muted-note">{{ sample.holdings.length }} 只 · 市值 {{ fmtNum(sample.metrics.positionValue.value) }} 元</span>
      </h3>
      <table class="dense-table row-click">
        <thead>
          <tr>
            <th>代码</th><th>名称</th>
            <th class="r">数量</th><th class="r">成本价</th><th class="r">最新价</th><th class="r">浮动盈亏</th>
            <th class="r">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in sample.holdings" :key="h.code" class="clickable" tabindex="0"
            :aria-label="`查看标的 ${h.name}（${h.code}）`"
            @click="openInstrument(h.code)" @keydown.enter="openInstrument(h.code)">
            <td class="mono">{{ h.code }}</td>
            <td>{{ h.name }}</td>
            <td class="r">{{ h.qty }}</td>
            <td class="r">{{ fmtNum(h.cost) }}</td>
            <td class="r">{{ fmtNum(h.last) }}</td>
            <td class="r" :class="signClass(h.pnl)">{{ fmtSigned(h.pnl) }}</td>
            <td class="r go">查看 →</td>
          </tr>
        </tbody>
      </table>
      <p class="table-hint">点击持仓行进入标的页（样例仅内置 600036 详情）。</p>
    </section>

    <section class="pane-card">
      <h3 class="card-title">已平仓</h3>
      <table class="dense-table">
        <thead>
          <tr><th>代码</th><th>名称</th><th class="r">数量</th><th class="r">盈亏</th><th>平仓时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in sample.closed" :key="t.code">
            <td class="mono">{{ t.code }}</td>
            <td>{{ t.name }}</td>
            <td class="r">{{ t.qty }}</td>
            <td class="r" :class="signClass(t.pnl)">{{ fmtSigned(t.pnl) }}</td>
            <td>{{ fmtDash(shanghaiTime(t.closedAt)) }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="pane-card">
      <h3 class="card-title">本月费用（元）</h3>
      <dl class="fee-grid">
        <div class="fee-item"><dt>佣金</dt><dd>{{ fmtNum(sample.fees.commission) }}</dd></div>
        <div class="fee-item"><dt>印花税</dt><dd>{{ fmtNum(sample.fees.stamp) }}</dd></div>
        <div class="fee-item"><dt>其他</dt><dd>{{ fmtNum(sample.fees.other) }}</dd></div>
        <div class="fee-item total"><dt>合计</dt><dd>{{ fmtNum(sample.fees.total) }}</dd></div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
// trader/账户：SAMPLE 演示页（TASK-08b）。持仓行点击 → 标的页，只改 query 不发任何交易请求。
import { traderCockpitSample as sample } from '../../../demo/traderCockpitSample'
import { useCockpitNavigation } from '../../../composables/useCockpitNavigation'
import { fmtDash, shanghaiTime, signClass } from '../../../utils/format'

const { setTraderPage } = useCockpitNavigation()

/** 切到 instrument 页并带上 traderInstrument=code；其余 query 键（含 tm）原样保留。 */
function openInstrument(code: string) {
  setTraderPage('instrument', { traderInstrument: code })
}

function fmtNum(value: number): string {
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtSigned(value: number): string {
  if (value > 0) return `+${fmtNum(value)}`
  if (value < 0) return `-${fmtNum(-value)}`
  return fmtNum(value)
}
</script>

<style scoped>
.trader-account { display: flex; flex-direction: column; gap: 8px; }

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

/* A股红涨绿跌：与 utils/format signClass 对齐 */
.up { color: var(--up); }
.down { color: var(--down); }
.flat { color: var(--text-faint); }

.row-click .clickable { cursor: pointer; }
.row-click .clickable:hover td { background: var(--bg-hover); }
.row-click .clickable:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.go { color: var(--accent); white-space: nowrap; }

.table-hint { margin: 8px 0 0; font-size: var(--fs-caption); color: var(--text-faint); }

.fee-grid { margin: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.fee-item dt { font-size: var(--fs-caption); color: var(--text-faint); }
.fee-item dd { margin: 2px 0 0; font-size: var(--fs-secondary); color: var(--text-primary); }
.fee-item.total dd { color: var(--accent); }
</style>
