// 驾驶舱 SAMPLE 演示开关（TASK-09a-1）：模块级门控，不动 window.fetch。
// 仅由驾驶舱入口（TradeMindPane，后续任务接入）在挂载前调用 setActive；
// workbenchApi 各取数函数在入口处读本开关，命中即返回本地样例信封，
// 不发出任何网络请求。默认关闭，关闭时一切行为与真实链路完全一致。
let active = false

export function setCockpitSampleActive(v: boolean): void {
  active = v
}

export function isCockpitSampleActive(): boolean {
  return active
}
