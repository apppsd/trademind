// API 在线态（TASK-20261026-13）：任一只读接口成功即置位。
// 保守锁存：超时/断网用 stale/error 状态表达，不把页面弹回演示模式。
import { ref } from 'vue'

export const apiOnline = ref(false)

/** 顶栏交易阶段文案（由总览/运行中心页面按后端状态写入）。 */
export const phaseLabel = ref<string | null>(null)

export const accountAlias = ref<string | null>(null)

export function markOnline(alias?: string): void {
  if (alias) accountAlias.value = alias
  apiOnline.value = true
}
