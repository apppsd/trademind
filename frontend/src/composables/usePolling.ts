// 轮询基元（TASK-20261026-13）：页面可见性感知的定时刷新。
// 页面隐藏时暂停（或降频），回到前台立即先重验一次；卸载必须无定时器泄漏。
// 仅用于 GET 快照；写操作永不进入轮询。
import { onScopeDispose, getCurrentScope } from 'vue'

export interface PollingOptions {
  intervalMs: number
  /** 页面隐藏时的间隔；0 表示完全暂停（默认）。 */
  hiddenIntervalMs?: number
  /** 立即执行一次（默认 true）。 */
  immediate?: boolean
  /** 连续失败后的重试间隔上限（退避封顶），默认 30s。 */
  backoffCapMs?: number
}

export interface PollingHandle {
  /** 供调用方在 success/failure 回调里汇报，驱动退避。 */
  notifySuccess(): void
  notifyFailure(): void
  stop(): void
}

export function usePolling(
  tick: () => Promise<void> | void,
  options: PollingOptions,
): PollingHandle {
  const hiddenIntervalMs = options.hiddenIntervalMs ?? 0
  const backoffCapMs = options.backoffCapMs ?? 30_000
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false
  let failures = 0

  function currentDelay(): number {
    const base =
      typeof document !== 'undefined' && document.hidden
        ? hiddenIntervalMs
        : options.intervalMs
    if (base <= 0) return 0
    // 指数退避：1x, 2x, 4x ... 封顶 backoffCapMs；成功后由 notifySuccess 归零。
    const factor = 2 ** Math.min(failures, 5)
    return Math.min(base * factor, backoffCapMs)
  }

  function schedule(): void {
    if (stopped) return
    if (timer !== null) clearTimeout(timer)
    const delay = currentDelay()
    if (delay <= 0) return // 隐藏时完全暂停
    timer = setTimeout(() => {
      timer = null
      void run()
    }, delay)
  }

  async function run(): Promise<void> {
    if (stopped) return
    try {
      await tick()
    } catch {
      // tick 内部应自行吞掉业务错误；这里兜底防未捕获异常。
      failures += 1
    }
    schedule()
  }

  function onVisibilityChange(): void {
    if (stopped) return
    if (!document.hidden) {
      // 用户返回：先重验数据年龄（立即刷新一次），成功后恢复正常频率。
      failures = 0
      void run()
    } else {
      if (timer !== null) clearTimeout(timer)
      timer = null
      schedule()
    }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  const handle: PollingHandle = {
    notifySuccess() {
      failures = 0
    },
    notifyFailure() {
      failures += 1
    },
    stop() {
      stopped = true
      if (timer !== null) clearTimeout(timer)
      timer = null
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibilityChange)
      }
    },
  }

  if (getCurrentScope()) {
    onScopeDispose(() => handle.stop())
  }
  if (options.immediate !== false) void run()
  else schedule()
  return handle
}
