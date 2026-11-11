// 工作台资源封装（TASK-20261026-13）：信封解包 + 可选轮询，页面只声明取数函数。
// SAMPLE 演示门（TASK-09a-1）：样例模式下不联网、不标记在线、不起轮询。
import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'
import { markOnline } from '../services/online'
import { isCockpitSampleActive } from '../services/cockpitSampleGate'
import { usePolling, type PollingHandle } from './usePolling'
import { useResource, type ResourceState } from './useResource'
import type { Envelope } from '../types/common'
import type { ReqOptions } from '../services/workbenchApi'

export type EnvelopeResource<T> = ResourceState<Envelope<T>> & {
  load: () => Promise<void>
  refresh: () => Promise<void>
  cancel: (reset?: boolean) => void
}

export function useWorkbenchResource<T>(
  fn: (opts: ReqOptions) => Promise<Envelope<T>>,
  pollMs?: number,
): EnvelopeResource<T> {
  const res = useResource<Envelope<T>>(async (signal, o) => {
    const env = await fn({ signal, timeoutMs: o.timeoutMs })
    // 样例模式下不标记在线：账号别名是 SAMPLE，不代表真实服务可达。
    if (!isCockpitSampleActive()) markOnline(env.account_alias)
    return {
      data: env,
      asOf: env.as_of ?? null,
      dataStatus: env.data_status,
      missingReasons: env.missing_reasons ?? [],
    }
  })
  let handle: PollingHandle | null = null
  // 样例快照静态不变，轮询无意义：即使配置了 pollMs 也不启动。
  // 注意：门状态在组合式函数初始化时读取一次，入口须在挂载前先 setActive。
  if (pollMs && !isCockpitSampleActive()) {
    handle = usePolling(async () => {
      await res.load()
      if (res.status.value === 'error') handle?.notifyFailure()
      else handle?.notifySuccess()
    }, { intervalMs: pollMs })
  }
  const clock = ref(Date.now())
  if (getCurrentScope()) {
    const timer = setInterval(() => { clock.value = Date.now() }, 1000)
    onScopeDispose(() => clearInterval(timer))
  }
  const stale = computed(() => {
    const env = res.snapshot.value
    if (!env) return res.stale.value
    const asOf = env.as_of ? Date.parse(env.as_of) : NaN
    const ttl = env.stale_after_seconds
    return res.stale.value || env.data_status === 'stale' ||
      (typeof ttl === 'number' && Number.isFinite(asOf) && clock.value - asOf > ttl * 1000)
  })
  return { ...res, stale, refresh: () => res.load() }
}
