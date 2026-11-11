// TASK-10a 双系统驾驶舱验收：query 双键落地、trader/tm 独立切换、
// SAMPLE 门控开关、tmLocation 保留其余键。风格沿用 router.spec.ts。
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApp, effectScope, type App, type EffectScope } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { router } from '@/router'
import { useCockpitNavigation } from '@/composables/useCockpitNavigation'
import { isCockpitSampleActive, setCockpitSampleActive } from '@/services/cockpitSampleGate'

// useRoute/useRouter 依赖 inject：用 app.runWithContext 提供注入上下文，
// composable 本体按任务要求跑在独立 effectScope 内，用完即停。
let app: App | null = null
let scope: EffectScope

beforeEach(async () => {
  if (!app) {
    app = createApp({ render: () => null })
    app.use(router)
  }
  await router.isReady()
  scope = effectScope()
})

afterEach(() => {
  scope.stop()
  setCockpitSampleActive(false) // 门控是模块级状态，不留给后续用例
})

function useNavInScope() {
  return app!.runWithContext(() => scope.run(() => useCockpitNavigation())!)
}

describe('dual-ops cockpit acceptance', () => {
  it('lands on /cockpit with both trader and tm keys and resolves the lazy component', async () => {
    await router.push('/cockpit?trader=overview&tm=overview')
    expect(router.currentRoute.value.path).toBe('/cockpit')
    expect(router.currentRoute.value.query).toMatchObject({ trader: 'overview', tm: 'overview' })
    const matched = router.currentRoute.value.matched
    expect(matched.length).toBeGreaterThan(0)
    expect(matched[0]?.components?.default).toBeTruthy()
  })

  it('switching trader keeps tm untouched', async () => {
    await router.push('/cockpit?trader=account&tm=market')
    const nav = useNavInScope()
    nav.setTraderPage('explorer')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ trader: 'explorer', tm: 'market' })
  })

  it('sample gate toggles the module-level flag off by default', () => {
    expect(isCockpitSampleActive()).toBe(false)
    setCockpitSampleActive(true)
    expect(isCockpitSampleActive()).toBe(true)
    setCockpitSampleActive(false)
    expect(isCockpitSampleActive()).toBe(false)
  })

  it('tmLocation inside cockpit rewrites tm and keeps trader', async () => {
    await router.push('/cockpit?trader=overview&tm=overview')
    const nav = useNavInScope()
    expect(nav.tmLocation('activity')).toMatchObject({
      path: '/cockpit',
      query: { trader: 'overview', tm: 'activity' },
    })
  })
})
