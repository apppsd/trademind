<template>
  <section class="ops-pane" :class="{ violet: pane.project === 'TradeMind' }" :aria-label="`${pane.project}窗格`">
    <header class="pane-head">
      <div><p class="eyebrow">{{ pane.subtitle }}</p><h2>{{ pane.project }} <span class="sample-tag" :data-mode="pane.sample ? 'sample' : 'live'">{{ pane.sample ? '样例 / SAMPLE' : '实时只读 / LIVE' }}</span></h2></div>
      <span class="readonly">只读 · 无交易写入</span>
    </header>
    <dl class="identity">
      <div><dt>主机</dt><dd>{{ pane.host ?? 'unknown（未核实身份）' }}</dd></div>
      <div><dt>账户</dt><dd>{{ pane.account ?? 'unknown' }}</dd></div>
      <div><dt>源</dt><dd>{{ pane.source }} · {{ pane.schema_version }}</dd></div>
      <div><dt>Run</dt><dd>{{ pane.run ?? 'null' }}</dd></div>
      <div><dt>数据时点</dt><dd>{{ pane.data_as_of ?? 'unknown' }}</dd></div>
      <div><dt>观察时点</dt><dd>{{ pane.observed_at }}</dd></div>
    </dl>
    <div class="pane-toolbar">
      <label v-if="pane.sample">演示数据状态 <select v-model="scenario" :aria-label="`${pane.project}演示数据状态`">
        <option v-for="(label, key) in statusLabels" :key="key" :value="key">{{ label }}</option>
      </select></label>
      <span class="status" :data-status="effectiveStatus" role="status">{{ pane.sample ? 'SAMPLE' : 'LIVE' }} · {{ statusLabels[effectiveStatus] }}</span>
      <button v-if="loadNote" class="load-note" type="button" disabled>{{ loadNote }}</button>
    </div>
    <p v-if="pane.notes?.length" class="pane-notes" role="note">{{ pane.notes.join('；') }}</p>
    <nav class="layer-nav" :aria-label="`${pane.project}展示层`">
      <button v-for="item in layers" :key="item.id" type="button" :aria-pressed="layer === item.id" @click="changeLayer(item.id)">{{ item.label }}</button>
    </nav>
    <div v-if="effectiveStatus !== 'ok'" class="scenario-message" role="status">
      <strong>{{ statusLabels[effectiveStatus] }} · {{ pane.sample ? '样例场景' : '实时状态' }}</strong><p>{{ statusMessage }}</p>
      <p>源时点保持原样；另一窗状态独立。{{ pane.sample ? '此场景不代表真实连接状态。' : '本窗不回填其他项目或账户的数据。' }}</p>
    </div>
    <template v-else>
      <div v-if="layer === 'flow' || layer === 'system'" class="flow-view">
        <div class="flow-caption"><span>{{ layer === 'system' ? '系统图 / 配置依赖' : 'Ops Stream / 运行流' }}</span><span>静态拓扑 · 无因果推断</span></div>
        <svg viewBox="0 0 1000 160" role="img" :aria-label="`${pane.project}配置依赖示意，节点详情见下方按钮`">
          <defs><linearGradient :id="`pipe-${pane.project}`"><stop stop-color="#33d6c6"/><stop offset="1" stop-color="#9b87ff"/></linearGradient></defs>
          <path d="M60 80 H940" class="pipe-base" />
          <path d="M60 80 H940" :stroke="`url(#pipe-${pane.project})`" class="pipe-light" />
          <g v-for="(node, index) in pane.nodes" :key="node.id" :transform="`translate(${60 + index * 880 / (pane.nodes.length - 1)},80)`">
            <circle r="23" class="node-halo" :data-status="node.data_status"/><circle r="8" class="node-core" :data-status="node.data_status"/>
            <text y="53" text-anchor="middle">{{ String(index + 1).padStart(2, '0') }}</text>
          </g>
          <g v-if="motion && layer === 'flow'" class="particles" aria-hidden="true">
            <circle cx="60" cy="80" r="5" class="particle"/><circle cx="60" cy="80" r="3" class="particle delayed"/>
          </g>
        </svg>
        <p class="motion-note">{{ motion && layer === 'flow' ? '示意动效 · 装饰粒子，非真实事件或流量' : '静态模式 · 不显示粒子' }}</p>
        <div class="node-list">
          <button v-for="node in pane.nodes" :key="node.id" type="button" :aria-pressed="selected?.id === node.id" @click="selected = node">
            <span class="node-dot" :data-status="node.data_status" aria-hidden="true"></span>{{ node.label }}<small>{{ nodeStatusShort(node) }} · 查看证据 ↗</small>
          </button>
        </div>
      </div>
      <div v-else-if="layer === 'account'" class="glass">
        <p class="eyebrow">ACCOUNT GLASS / 账户玻璃舱 · {{ pane.sample ? 'SAMPLE' : 'LIVE 只读' }}</p>
        <h3>{{ pane.account ?? 'unknown' }}</h3>
        <div class="account-metrics">
          <div>账户权益<strong>{{ glass?.equity ?? '—' }}</strong></div>
          <div>可用现金<strong>{{ glass?.cash ?? '—' }}</strong></div>
          <div>模型费用<strong>{{ glass?.fees ?? '—' }}</strong></div>
        </div>
        <p v-if="glass?.note" class="glass-note">{{ glass.note }}</p>
        <p v-if="!pane.sample && glass" class="glass-note">数据时点 {{ glass.as_of ?? 'unknown' }} · 盯市{{ glass.valuation_complete === true ? '完成' : glass.valuation_complete === false ? '未完成，收益率未知' : '状态 unknown' }}。</p>
        <p v-if="pane.sample">未知金额与费用保留“—”，不使用零填充。两个项目账本与预算独立，不合并。</p>
        <p>资金与风险参数无编辑入口。</p>
      </div>
      <div v-else class="events">
        <label>筛选{{ pane.sample ? '样例' : '' }}事件 <input v-model="query" type="search" :aria-label="`${pane.project}筛选事件`" placeholder="输入关键词" /></label>
        <p>{{ pane.sample ? '固定样例时间窗 · 无自动跟随；原始日志接口未接入。' : '只读白名单事件（服务端限条）· 无自动跟随轮询；缺已验证事件源时为成功空，不伪造脉冲。' }}</p>
        <button v-for="event in filteredEvents" :key="event.id" type="button" @click="selected = event">
          <time>{{ event.data_as_of ?? 'unknown' }}</time><strong>{{ event.label }}</strong><span>{{ event.evidence_ref ?? 'unknown' }}</span>
        </button>
        <p v-if="!filteredEvents.length" role="status">成功空 · 没有匹配{{ pane.sample ? '样例' : '事件' }}</p>
      </div>
    </template>
    <aside v-if="selected && effectiveStatus === 'ok'" class="evidence" aria-label="证据详情">
      <header><h3>{{ pane.sample ? '样例证据 / SAMPLE' : '只读证据 / LIVE' }}</h3><button type="button" aria-label="关闭证据详情" @click="selected = null">关闭</button></header>
      <p>{{ selected.label }}</p>
      <p v-if="'detail' in selected">{{ selected.detail }}</p>
      <dl><div v-for="key in evidenceKeys" :key="key"><dt>{{ key }}</dt><dd>{{ selected[key] ?? 'unknown' }}</dd></div></dl>
      <p>仅配置依赖；缺少 trace，不推断相邻节点因果。</p>
    </aside>
    <footer v-if="pane.sample">证据 {{ pane.evidence_ref }} · 本地样例{{ loadNote ? ' · ' + loadNote : ' · 无后台请求' }}</footer>
    <footer v-else>源 {{ pane.source }} · 只读白名单 GET · 无轮询/无写入 · 挂载与手动刷新各一次</footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { statusLabels, type OpsEvent, type OpsNode, type OpsPane, type OpsStatus } from '../demo/opsStreamSample'
const props = defineProps<{ pane: OpsPane; motion: boolean; loadNote?: string | null }>()
const scenario = ref<OpsStatus>('ok')
type Layer = 'flow' | 'system' | 'account' | 'events'
const layer = ref<Layer>('flow')
const layers: { id: Layer; label: string }[] = [
  { id: 'flow', label: '运行流' }, { id: 'system', label: '系统图' },
  { id: 'account', label: '账户玻璃舱' }, { id: 'events', label: '事件浏览' },
]
const selected = ref<OpsNode | OpsEvent | null>(null)
const query = ref('')
const filteredEvents = computed(() => props.pane.events.filter(e => e.label.includes(query.value.trim())))
const evidenceKeys = ['project', 'host', 'account', 'run', 'source', 'schema_version', 'data_as_of', 'observed_at', 'data_status', 'evidence_ref'] as const
const glass = computed(() => props.pane.account_glass ?? null)
const effectiveStatus = computed<OpsStatus>(() => (props.pane.sample ? scenario.value : props.pane.data_status))
const statusMessage = computed(() => props.pane.sample ? scenarioMessage[effectiveStatus.value] : liveMessage[effectiveStatus.value] ?? '实时源返回非成功状态；显示原状态，不回填其他数据。')
const scenarioMessage: Record<OpsStatus, string> = {
  ok: '成功读取样例。', loading: '加载中示例，无网络请求。',
  empty: '成功读取，但没有记录；不生成粒子或虚构零值。',
  missing: '源或必要字段缺失；不补造事件和关联。',
  stale: '样例快照已过期；停止动效，不当作实时数据。',
  error: '读取失败示例；无数据，不回填其他项目或账户。',
  unknown: '无法确定源状态；unknown 不等于零。',
}
const liveMessage: Partial<Record<OpsStatus, string>> = {
  empty: '实时源成功读取但无记录；成功空不是错误。',
  missing: '实时源或必要字段缺失；金额/事件置 unknown，不补造。',
  stale: '实时快照已过期；按源时点展示，不当作当前数据。',
  error: '实时源返回错误；本窗不回填其他项目数据。',
  unknown: '实时源状态无法确定；unknown 不等于零。',
  loading: '实时源读取中。',
}
function nodeStatusShort(node: OpsNode): string {
  return node.data_status === 'ok' ? (node.sample ? '样例' : '有证据') : statusLabels[node.data_status]
}
function changeLayer(value: Layer) { layer.value = value; selected.value = null }
</script>

<style scoped>
.ops-pane { --pane-accent: #33d6c6; min-width: 0; border: 1px solid #284253; border-radius: 18px; overflow: hidden; background: linear-gradient(155deg, #10232eeb, #0c1225f5 62%); box-shadow: inset 0 1px #53e6ce14, 0 18px 60px #0003; }
.ops-pane.violet { --pane-accent: #b5a0ff; border-color: #3b3759; }
.pane-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 22px; border-bottom: 1px solid #9cbfda20; }
h2 { margin: 6px 0 0; font-size: clamp(21px, 2vw, 29px); letter-spacing: .03em; }
.eyebrow { margin: 0; font-size: 11px; letter-spacing: .1em; color: var(--pane-accent); }
.sample-tag, .readonly { display: inline-block; font-size: 11px; font-weight: 500; border: 1px solid #f5b04d66; border-radius: 5px; padding: 3px 7px; color: #ffca78; vertical-align: middle; }
.sample-tag[data-mode='live'] { color: #7deddc; border-color: #33d6c666; }
.readonly { color: var(--pane-accent); border-color: #8faacc44; }
.identity { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 20px 22px; margin: 0; background: #070c1855; }
dt { color: #9eacc0; font-size: 11px; } dd { margin: 3px 0 0; font-size: 12px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.pane-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; padding: 14px 22px; font-size: 12px; }
select, input, button { font: inherit; color: inherit; }
select, input { padding: 7px; border: 1px solid #62799266; border-radius: 6px; background: #0b1323; max-width: 100%; }
.status { color: var(--pane-accent); } .status:not([data-status='ok']) { color: #ffca78; }
.load-note { font-size: 11px; color: #9eacc0; border-style: dashed; cursor: default; }
.pane-notes { margin: 0; padding: 0 22px 12px; font-size: 11px; color: #e9c18a; overflow-wrap: anywhere; }
.layer-nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 22px 14px; }
button { cursor: pointer; border: 1px solid #8198b333; background: #152238; border-radius: 8px; padding: 9px 12px; text-align: left; }
button:hover, button[aria-pressed='true'] { border-color: var(--pane-accent); color: var(--pane-accent); background: #26344b; }
.layer-nav button { flex: 1 1 auto; font-size: 12px; text-align: center; }
.flow-view { padding: 18px 22px 22px; background-image: linear-gradient(#6f8eb10a 1px, transparent 1px), linear-gradient(90deg, #6f8eb10a 1px, transparent 1px); background-size: 28px 28px; }
.flow-caption { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: #a7b5c9; font-size: 11px; }
svg { width: 100%; display: block; overflow: visible; margin: 20px 0 4px; }
.pipe-base { stroke: #28374c; stroke-width: 12; fill: none; }
.pipe-light { stroke-width: 2; fill: none; opacity: .8; }
.node-halo { fill: #101e32; stroke: var(--pane-accent); stroke-width: 2; }
.node-core { fill: var(--pane-accent); }
.node-halo[data-status='unknown'], .node-halo[data-status='missing'] { stroke: #7d8ba1; stroke-dasharray: 4 3; }
.node-core[data-status='unknown'], .node-core[data-status='missing'] { fill: #7d8ba1; }
.node-dot[data-status='unknown'], .node-dot[data-status='missing'] { background: #7d8ba1; }
text { fill: #a9b9cf; font-size: 16px; }
.particle { fill: #e3fffa; filter: drop-shadow(0 0 7px #65fff0); animation: travel 7s linear infinite; }
.delayed { animation-delay: -3.5s; }
@keyframes travel { from { transform: translateX(0); opacity: 0; } 10%, 90% { opacity: 1; } to { transform: translateX(880px); opacity: 0; } }
.motion-note { color: #9eacc0; font-size: 11px; margin: 6px 0 20px; }
.node-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
.node-list button { font-size: 13px; min-width: 0; overflow-wrap: anywhere; }
.node-list small { display: block; margin-top: 8px; color: #a5b7ca; font-size: 10px; }
.node-dot { display: inline-block; width: 6px; height: 6px; margin-right: 8px; background: var(--pane-accent); border-radius: 50%; }
.scenario-message, .glass, .events { padding: 24px 22px; min-height: 240px; font-size: 13px; color: #b4c2d5; }
.glass-note { font-size: 11px; color: #9eacc0; overflow-wrap: anywhere; }
.account-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 20px 0; }
.account-metrics strong { display: block; font-size: 28px; color: #e8eef8; overflow-wrap: anywhere; }
.events input { width: 100%; margin-top: 8px; }
.events button { width: 100%; display: grid; gap: 5px; margin-top: 10px; overflow-wrap: anywhere; }
.events time, .events span { font-size: 11px; color: #9eacc0; }
.evidence { margin: 0 22px 22px; padding: 16px; background: #080e1c; border: 1px solid var(--pane-accent); border-radius: 10px; font-size: 12px; }
.evidence header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
h3 { font-size: 14px; margin: 0; overflow-wrap: anywhere; }
.evidence dl { display: grid; gap: 8px; } .evidence dl div { display: grid; grid-template-columns: 110px minmax(0,1fr); gap: 8px; }
footer { padding: 14px 22px; border-top: 1px solid #8faacc22; font-size: 10px; color: #9eacc0; overflow-wrap: anywhere; }
@media (max-width: 480px) { .identity { grid-template-columns: 1fr; } .pane-head, .identity, .pane-toolbar, .flow-view, .glass, .events { padding: 16px; } .layer-nav { padding-inline: 16px; } .evidence { margin-inline: 16px; } .evidence dl div { grid-template-columns: 1fr; gap: 0; } }
@media (prefers-reduced-motion: reduce) { .particles { display: none; } .particle { animation: none; } }
</style>
