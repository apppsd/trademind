// TASK-20261026-13 请求层测试：错误分类、信封校验、文本安全。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HttpError, fetchEnvelope, fetchJson, isEnvelope } from '../src/services/client'

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

const GOOD_ENVELOPE = {
  schema_version: 1,
  account_alias: 'paper-001',
  as_of: '2026-10-26T02:00:00+00:00',
  generated_at: '2026-10-26T02:00:00+00:00',
  data_status: 'ok',
  source: 'test',
  age_seconds: 0,
  stale_after_seconds: 10,
  partial: false,
  missing_reasons: [],
  data: { hello: 'world' },
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchJson 错误分类', () => {
  it('HTTP 500 归类为 http 且带中文限流/权限文案', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 429, text: async () => '' } as unknown as Response)))
    try {
      await fetchJson('/x')
      expect.unreachable()
    } catch (err) {
      expect(err).toBeInstanceOf(HttpError)
      expect((err as HttpError).kind).toBe('http')
      expect((err as HttpError).status).toBe(429)
      expect((err as HttpError).message).toContain('限流')
    }
  })

  it('网络拒绝归类为 network', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed') }))
    await expect(fetchJson('/x')).rejects.toMatchObject({ kind: 'network' })
  })

  it('非 JSON 响应归类为 parse，且原始 HTML 不进入错误消息', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, text: async () => '<html><body>gateway error</body></html>',
    } as unknown as Response)))
    try {
      await fetchJson('/x')
      expect.unreachable()
    } catch (err) {
      expect((err as HttpError).kind).toBe('parse')
      expect((err as HttpError).message).not.toContain('<html')
      expect((err as HttpError).message).not.toContain('gateway')
    }
  })

  it('空响应归类为 parse', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, text: async () => '  ' } as unknown as Response)))
    await expect(fetchJson('/x')).rejects.toMatchObject({ kind: 'parse' })
  })

  it('超时归类为 timeout（fake timers 触发内部 abort）', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const p = fetchJson('/x', { timeoutMs: 1000 })
    const assertion = expect(p).rejects.toMatchObject({ kind: 'timeout' })
    await vi.advanceTimersByTimeAsync(1001)
    await assertion
    vi.useRealTimers()
  })

  it('外部取消归类为 abort，不算超时', async () => {
    const external = new AbortController()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_, reject) => {
      init.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const p = fetchJson('/x', { timeoutMs: 10_000, signal: external.signal })
    const assertion = expect(p).rejects.toMatchObject({ kind: 'abort' })
    external.abort()
    await assertion
  })
})

describe('信封校验', () => {
  it('合法信封通过', () => {
    expect(isEnvelope(GOOD_ENVELOPE)).toBe(true)
  })

  it('缺字段/错误状态/非对象不通过', () => {
    expect(isEnvelope({ ...GOOD_ENVELOPE, data_status: 'weird' })).toBe(false)
    expect(isEnvelope({ ...GOOD_ENVELOPE, generated_at: 123 })).toBe(false)
    expect(isEnvelope('nope')).toBe(false)
    expect(isEnvelope(null)).toBe(false)
  })

  it('fetchEnvelope 对契约异常响应抛 parse', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ unexpected: true })))
    await expect(fetchEnvelope('/x')).rejects.toMatchObject({ kind: 'parse' })
  })

  it('fetchEnvelope 返回强类型数据', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(GOOD_ENVELOPE)))
    const env = await fetchEnvelope<{ hello: string }>('/x')
    expect(env.data.hello).toBe('world')
    expect(env.data_status).toBe('ok')
  })
})
