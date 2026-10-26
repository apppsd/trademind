// 唯一 GET 请求层（TASK-20261026-13）：信封校验、错误分类、超时与取消。
// 边界：本层只服务 GET 快照读；未来任何 POST 控制端点不得复用这里的自动
// 重试/轮询逻辑（写操作永不自动重试）。
import { markOnline } from './online'
import type { DataStatus, Envelope } from '../types/common'

export type HttpErrorKind = 'timeout' | 'network' | 'http' | 'parse' | 'abort'

const KIND_MESSAGE: Record<HttpErrorKind, string> = {
  timeout: '请求超时，请稍后重试',
  network: '网络连接失败，请检查本地服务是否可达',
  http: '服务返回错误',
  parse: '服务响应格式异常',
  abort: '请求已取消',
}

const STATUS_MESSAGE: Record<number, string> = {
  401: '无访问权限（未认证）',
  403: '无访问权限（被拒绝）',
  404: '资源不存在',
  429: '请求过于频繁，已被限流',
  500: '服务内部错误',
  502: '服务暂时不可用',
  503: '服务暂时不可用',
}

/** 结构化请求错误。message 只含安全中文文本，绝不包含服务端原始 HTML。 */
export class HttpError extends Error {
  readonly kind: HttpErrorKind
  readonly status: number | null

  constructor(kind: HttpErrorKind, status: number | null = null, message?: string) {
    const statusText = status !== null && STATUS_MESSAGE[status] ? `（${STATUS_MESSAGE[status]}）` : ''
    super(message ?? KIND_MESSAGE[kind] + statusText)
    this.name = 'HttpError'
    this.kind = kind
    this.status = status
  }
}

export interface FetchOptions {
  /** 超时毫秒数，默认 8000；超时以 AbortController 实现。 */
  timeoutMs?: number
  /** 外部取消信号（切页/切换标的时传入），触发后错误类型为 abort。 */
  signal?: AbortSignal
}

const DATA_STATUSES: readonly DataStatus[] = ['ok', 'partial', 'stale', 'unavailable', 'error']

export function isEnvelope(value: unknown): value is Envelope<unknown> {
  if (typeof value !== 'object' || value === null) return false
  const e = value as Record<string, unknown>
  return (
    typeof e.schema_version === 'number' &&
    typeof e.data_status === 'string' &&
    DATA_STATUSES.includes(e.data_status as DataStatus) &&
    'data' in e &&
    typeof e.generated_at === 'string'
  )
}

/** GET JSON：非 JSON/空响应/HTTP 失败统一分类为中文 HttpError；原文本不进 DOM。 */
export async function fetchJson<T>(url: string, opts: FetchOptions = {}): Promise<T> {
  if (opts.signal?.aborted) throw new HttpError('abort')
  const controller = new AbortController()
  let timedOut = false
  let rejectAbort: (reason: HttpError) => void = () => {}
  const aborted = new Promise<never>((_, reject) => { rejectAbort = reject })
  const abort = () => {
    controller.abort()
    rejectAbort(new HttpError(timedOut ? 'timeout' : 'abort'))
  }
  const timer = setTimeout(() => { timedOut = true; abort() }, opts.timeoutMs ?? 8000)
  opts.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (opts.signal?.aborted) abort()
    const response = (async () => {
      const res = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } })
      if (!res.ok) throw new HttpError('http', res.status)
      const text = await res.text()
      if (!text.trim()) throw new HttpError('parse')
      try { return JSON.parse(text) as T } catch { throw new HttpError('parse') }
    })()
    return await Promise.race([response, aborted])
  } catch (err) {
    if (err instanceof HttpError) throw err
    if (timedOut) throw new HttpError('timeout')
    if (controller.signal.aborted) throw new HttpError('abort')
    throw new HttpError('network')
  } finally {
    clearTimeout(timer)
    opts.signal?.removeEventListener('abort', abort)
  }
}

/** GET 并做运行时信封校验（TS 类型不能替代运行时检查）。 */
export async function fetchEnvelope<T>(url: string, opts: FetchOptions = {}): Promise<Envelope<T>> {
  const body = await fetchJson<unknown>(url, opts)
  if (!isEnvelope(body)) throw new HttpError('parse', null, '服务响应契约异常（信封缺失）')
  markOnline((body as Envelope<T>).account_alias)
  return body as Envelope<T>
}
