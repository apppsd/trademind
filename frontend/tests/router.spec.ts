import { describe, expect, it, vi } from 'vitest'
// Navigation tests isolate canvas rendering; the production build covers chart imports.
vi.mock('../src/components/ChartPanel.vue', () => ({ default: { template: '<div />' } }))
import { router } from '@/router'

describe('workbench router', () => {
  // Each lazy route has its own unchanged 5s deadline. A cumulative deadline
  // for seven unrelated SFC transforms was dependent on cold-cache CPU load.
  it.each(['/', '/market', '/assets', '/strategy', '/activity', '/reports', '/run'])(
    'loads and navigates the actual lazy page %s', async (p) => {
    await router.push(p)
    expect(router.currentRoute.value.path).toBe(p)
    const matched = router.currentRoute.value.matched
    expect(matched.length).toBeGreaterThan(0)
    expect(matched[0]?.components?.default).toBeTruthy()
  })

  it('uses hash history so /api/ and /reports/ stay untouched', async () => {
    await router.push('/market')
    expect(window.location.hash).toBe('#/market')
  })
})
