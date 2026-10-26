// 资源状态基元（TASK-20261026-13）：单资源单在途、世代守卫、快照保留。
// 一个慢接口只影响自己的块，不锁整页；错误保留最后成功快照并给出中文原因。
import { readonly, ref, getCurrentScope, onScopeDispose, type Ref } from 'vue'
import { HttpError } from '../services/client'

export type ResourceStatus = 'idle' | 'loading' | 'refreshing' | 'ok' | 'error'

export interface FetchResult<T> {
  data: T
  asOf: string | null
  dataStatus: string | null
  missingReasons: string[]
}

export interface ResourceState<T> {
  status: Readonly<Ref<ResourceStatus>>
  snapshot: Readonly<Ref<T | null>>
  error: Readonly<Ref<HttpError | null>>
  /** 最后一次成功快照的信封元数据；无快照时为 null。 */
  asOf: Readonly<Ref<string | null>>
  dataStatus: Readonly<Ref<string | null>>
  missingReasons: Readonly<Ref<string[]>>
  /** 有快照但本次刷新失败：快照陈旧，页面必须显示陈旧标记。 */
  stale: Readonly<Ref<boolean>>
  /** 首次加载（无任何快照），页面显示骨架而非错误。 */
  firstLoad: Readonly<Ref<boolean>>
  failureCount: Readonly<Ref<number>>
}

export interface ResourceOptions {
  timeoutMs?: number
}

export function useResource<T>(
  fetcher: (signal: AbortSignal | undefined, opts: { timeoutMs?: number }) => Promise<FetchResult<T>>,
  options: ResourceOptions = {},
): ResourceState<T> & { load: () => Promise<void>; refresh: () => Promise<void>; cancel: (reset?: boolean) => void } {
  const status = ref<ResourceStatus>('idle')
  const snapshot = ref<T | null>(null)
  const error = ref<HttpError | null>(null)
  const asOf = ref<string | null>(null)
  const dataStatus = ref<string | null>(null)
  const missingReasons = ref<string[]>([])
  const stale = ref(false)
  const firstLoad = ref(true)
  const failureCount = ref(0)

  // holder 对象：let 变量在闭包内会被 TS 控制流收窄成 never，属性读取不受影响。
  const live: { inFlight: { gen: number; promise: Promise<void> } | null } = { inFlight: null }
  let generation = 0
  let controller: AbortController | null = null

  function cancel(reset = false): void {
    generation += 1
    controller?.abort()
    controller = null
    live.inFlight = null
    if (reset) {
      snapshot.value = null
      asOf.value = null
      dataStatus.value = null
      missingReasons.value = []
      firstLoad.value = true
      stale.value = false
      error.value = null
    }
    status.value = snapshot.value === null ? 'idle' : 'ok'
  }
  if (getCurrentScope()) onScopeDispose(() => cancel())

  function load(): Promise<void> {
    // 单资源单在途：已有请求未完成时复用同一个 Promise（首屏无双刷新）。
    if (live.inFlight) return live.inFlight.promise
    const gen = ++generation
    const requestController = new AbortController()
    controller = requestController
    status.value = firstLoad.value ? 'loading' : 'refreshing'
    const task = (async () => {
      try {
        const result = await fetcher(requestController.signal, { timeoutMs: options.timeoutMs })
        // 世代守卫：迟到旧响应无权覆盖更新一代的结果。
        if (gen !== generation) return
        snapshot.value = result.data
        asOf.value = result.asOf
        dataStatus.value = result.dataStatus
        missingReasons.value = result.missingReasons
        error.value = null
        stale.value = false
        failureCount.value = 0
        status.value = 'ok'
      } catch (err) {
        if (gen !== generation) return
        if (err instanceof HttpError && err.kind === 'abort') {
          status.value = snapshot.value !== null ? 'ok' : 'idle' // 取消不算失败
          return
        }
        error.value =
          err instanceof HttpError ? err : new HttpError('network', null, '未知请求错误')
        failureCount.value += 1
        if (snapshot.value !== null) stale.value = true
        status.value = 'error'
      } finally {
        await Promise.resolve()
        if (live.inFlight && live.inFlight.gen === gen) {
          live.inFlight = null
          firstLoad.value = false
        }
      }
    })()
    live.inFlight = { gen, promise: task }
    return task
  }

  return {
    status: readonly(status) as Readonly<Ref<ResourceStatus>>,
    snapshot: readonly(snapshot) as Readonly<Ref<T | null>>,
    error: readonly(error) as Readonly<Ref<HttpError | null>>,
    asOf: readonly(asOf) as Readonly<Ref<string | null>>,
    dataStatus: readonly(dataStatus) as Readonly<Ref<string | null>>,
    missingReasons: readonly(missingReasons) as Readonly<Ref<string[]>>,
    stale: readonly(stale) as Readonly<Ref<boolean>>,
    firstLoad: readonly(firstLoad) as Readonly<Ref<boolean>>,
    failureCount: readonly(failureCount) as Readonly<Ref<number>>,
    load,
    refresh: load,
    cancel,
  }
}

/** 空数组≠接口失败：页面用 status 区分空态与错误态，此函数仅供空态判断。 */
export function isEmptyList<T>(rows: readonly T[] | null | undefined): boolean {
  return Array.isArray(rows) && rows.length === 0
}
