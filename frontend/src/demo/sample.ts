/** 离线演示样例数据（仅 26-03 骨架布局审阅用）。
 * 硬性要求：显式标注“离线演示”，绝不进入生产默认页，
 * 不得被误认为真实账户/行情；26-08 起由真实只读 API 替换。 */

export const DEMO_METRICS: { label: string; value: string; unit: string; subtext: string; asOfLabel: string | null }[] = [
  { label: '总资产', value: '1,012.34', unit: '元', subtext: '现金含冻结 100.00 元', asOfLabel: '10:32' },
  { label: '今日交易盈亏', value: '-2.30', unit: '元', subtext: '不含模型费用', asOfLabel: null },
  { label: '扣模型费后暂估', value: '-2.50', unit: '元', subtext: '估算 · 模型费另计', asOfLabel: null },
  { label: '可用现金', value: '812.34', unit: '元', subtext: '冻结 200.00 元', asOfLabel: null },
]

/** 演示曲线：单调随机形状，仅占位，不来自任何真实标的。 */
export const DEMO_CURVE: [string, number][] = Array.from({ length: 48 }, (_, i) => {
  const h = 9 + Math.floor(i / 24)
  const m = (i * 15) % 60
  return [`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`, 1000 + Math.sin(i / 6) * 6 + i * 0.15]
})

export const DEMO_WATCH = [
  { name: '中概互联ETF', code: '513050.SH', price: '0.949', change: '+0.53%', action: '按策略观望', status: '资金可达' },
  { name: '纳指100ETF', code: '513100.SH', price: '2.241', change: '-0.18%', action: '仅观察', status: '超出单标的上限' },
  { name: '黄金ETF', code: '518880.SH', price: '8.197', change: '+0.00%', action: '仅观察', status: '超出单标的上限' },
] as const

export const DEMO_EVENTS = [
  { time: '09:31', text: '策略计划更新：中概互联ETF 观望（条件未满足）' },
  { time: '09:30', text: '等待开市数据热身完成，开始接收分钟行情' },
  { time: '08:59', text: '账户健康检查通过，预算充足' },
] as const
