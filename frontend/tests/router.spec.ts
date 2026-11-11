import { describe, expect, it } from 'vitest'
import { router } from '@/router'

describe('workbench router', () => {
  it('loads the actual lazy cockpit page', async () => {
    await router.push('/cockpit')
    expect(router.currentRoute.value.path).toBe('/cockpit')
    const matched = router.currentRoute.value.matched
    expect(matched.length).toBeGreaterThan(0)
    expect(matched[0]?.components?.default).toBeTruthy()
  })

  it('redirects / to the cockpit dual overview so the landing shows both systems', async () => {
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/cockpit')
    expect(router.currentRoute.value.query).toMatchObject({ trader: 'overview', tm: 'overview' })
  })

  it.each([
    ['/overview', { trader: 'overview', tm: 'overview' }],
    ['/market', { trader: 'overview', tm: 'market' }],
    ['/assets', { trader: 'overview', tm: 'assets' }],
    ['/strategy', { trader: 'overview', tm: 'strategy' }],
    ['/activity', { trader: 'overview', tm: 'activity' }],
    ['/reports', { trader: 'overview', tm: 'reports' }],
    ['/run', { trader: 'overview', tm: 'run' }],
    ['/ops-stream', { trader: 'stream', tm: 'stream' }],
  ])('folds legacy path %s into the cockpit panes', async (legacy, query) => {
    await router.push(legacy)
    expect(router.currentRoute.value.path).toBe('/cockpit')
    expect(router.currentRoute.value.query).toMatchObject(query)
  })

  it('uses hash history so /api/ and /reports/ stay untouched', async () => {
    await router.push('/market')
    expect(window.location.hash).toContain('#/cockpit')
  })
})
