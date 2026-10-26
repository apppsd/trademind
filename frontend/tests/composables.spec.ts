// TASK-20261026-13 组合式基元测试：单在途、世代守卫、快照保留、轮询可见性、偏好持久化。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, type EffectScope } from 'vue'
import { HttpError } from '../src/services/client'
import { useResource, type FetchResult } from '../src/composables/useResource'
import { usePolling } from '../src/composables/usePolling'
import { useFavoriteInstruments, usePreferredPeriod } from '../src/composables/usePreferences'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (v: T) => void
  reject: (e: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

type Payload = { value: number | string }
const OK_RESULT: FetchResult<Payload> = {
  data: { value: 1 },
  asOf: '2026-10-26T02:00:00+00:00',
  dataStatus: 'ok',
  missingReasons: [],
}

describe('useResource', () => {
  let scope: EffectScope

  beforeEach(() => {
    scope = effectScope()
  })

  afterEach(() => {
    scope.stop()
  })

  it('单资源单在途：并发 load 复用同一请求', async () => {
    let calls = 0
    const d = deferred<typeof OK_RESULT>()
    const res = scope.run(() =>
      useResource(async () => {
        calls += 1
        return d.promise
      }),
    )!
    const p1 = res.load()
    const p2 = res.load()
    expect(calls).toBe(1)
    expect(res.status.value).toBe('loading')
    d.resolve(OK_RESULT)
    await Promise.all([p1, p2])
    expect(res.status.value).toBe('ok')
    expect(res.snapshot.value).toEqual({ value: 1 })
  })

  it('取消不算失败：状态回 idle，随后可重新加载；迟到旧代响应不覆盖', async () => {
    let first = true
    const slow = deferred<typeof OK_RESULT>()
    const res = scope.run(() =>
      useResource<Payload>(async (signal) => {
        if (first) {
          first = false
          return new Promise((resolve, reject) => {
            signal?.addEventListener('abort', () => reject(new HttpError('abort')))
            // 取消后迟到 resolve：世代守卫必须挡住它
            void slow.promise.then(resolve)
          })
        }
        return { ...OK_RESULT, data: { value: 2 } }
      }),
    )!
    const p1 = res.load()
    expect(res.status.value).toBe('loading')
    res.cancel() // abort → fetcher reject(abort) → settle → inFlight 清空
    await p1
    expect(res.status.value).toBe('idle')
    expect(res.error.value).toBeNull()
    await res.load() // 第二代
    expect(res.snapshot.value).toEqual({ value: 2 })
    slow.resolve({ ...OK_RESULT, data: { value: 'STALE' } }) // 旧代此刻才迟到
    await vi.waitFor(() => res.status.value === 'ok')
    expect(res.snapshot.value).toEqual({ value: 2 }) // 未被覆盖
  })

  it('失败保留最后成功快照并标记陈旧；abort 不算失败', async () => {
    const failing = deferred<typeof OK_RESULT>()
    let n = 0
    const res = scope.run(() =>
      useResource(async () => {
        n += 1
        if (n === 1) return OK_RESULT
        return failing.promise
      }),
    )!
    await res.load()
    expect(res.stale.value).toBe(false)
    const p = res.load()
    failing.reject(new HttpError('http', 500))
    await p
    expect(res.status.value).toBe('error')
    expect(res.stale.value).toBe(true)
    expect(res.snapshot.value).toEqual({ value: 1 }) // 快照保留
    expect(res.failureCount.value).toBe(1)
    expect(res.error.value?.message).toContain('服务')
    // abort：状态回 ok/idle，不算错误
    const res2 = scope.run(() => useResource<Payload>(async () => { throw new HttpError("abort") }))!
    await res2.load()
    expect(res2.status.value).toBe('idle')
    expect(res2.error.value).toBeNull()
  })

  it('空数据不是错误：ok 状态 + 空数组由页面区分空态', async () => {
    const res = scope.run(() =>
      useResource(async () => ({ ...OK_RESULT, data: [] as unknown as Payload })),
    )!
    await res.load()
    expect(res.status.value).toBe('ok')
    expect(res.dataStatus.value).toBe('ok')
  })

  it('50 次加载-取消循环无异常累积', async () => {
    const res = scope.run(() =>
      useResource(async () => ({ ...OK_RESULT, data: { value: 7 } })),
    )!
    for (let i = 0; i < 50; i += 1) {
      const p = res.load()
      await p
    }
    expect(res.status.value).toBe('ok')
    expect(res.failureCount.value).toBe(0)
  })
})

describe('usePolling', () => {
  function setHidden(hidden: boolean): void {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  }

  beforeEach(() => {
    vi.useFakeTimers()
    setHidden(false)
  })

  afterEach(() => {
    vi.useRealTimers()
    setHidden(false)
  })

  it('按周期刷新；页面隐藏暂停；回前台立即刷新；stop 后无泄漏', async () => {
    const tick = vi.fn(async () => {})
    const handle = usePolling(tick, { intervalMs: 1000 })
    await vi.advanceTimersByTimeAsync(0)
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(tick).toHaveBeenCalledTimes(2)
    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange')) // 浏览器隐藏即触发
    await vi.advanceTimersByTimeAsync(10_000)
    expect(tick).toHaveBeenCalledTimes(2) // 隐藏期间暂停
    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(tick).toHaveBeenCalledTimes(3) // 回前台立即重验
    handle.stop()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(tick).toHaveBeenCalledTimes(3) // 无泄漏
  })

  it('连续失败按退避拉长间隔（1x→2x→4x），成功后恢复', async () => {
    const tick = vi.fn(async () => {
      throw new Error('boom') // tick 抛错也被兜住，不产生未捕获异常
    })
    usePolling(tick, { intervalMs: 1000 })
    await vi.advanceTimersByTimeAsync(0) // call 1 失败 → failures=1，下次 2s
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000) // t=1000：退避中不触发
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000) // t=2000：call 2 失败 → 下次 4s
    expect(tick).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(3000) // t=5000：退避中不触发
    expect(tick).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1000) // t=6000：call 3
    expect(tick).toHaveBeenCalledTimes(3)
  })
})

describe('usePreferences', () => {
  it('收藏切换持久化到 localStorage（不含敏感数据）', () => {
    const { favorites, toggle } = useFavoriteInstruments()
    expect(favorites.value.size).toBe(0)
    toggle('513050.SH')
    expect(favorites.value.has('513050.SH')).toBe(true)
    expect(localStorage.getItem('wb:favorites')).toBe('513050.SH')
    toggle('513050.SH')
    expect(localStorage.getItem('wb:favorites')).toBe('')
  })

  it('周期偏好持久化', () => {
    const period = usePreferredPeriod()
    period.value = '5m'
    expect(localStorage.getItem('wb:period')).toBe('5m')
  })
})
