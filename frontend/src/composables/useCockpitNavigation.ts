import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { RouteLocationRaw } from 'vue-router'

/** 左窗格 trader 侧页面键。 */
export const TRADER_PAGES = ['overview', 'account', 'instrument', 'explorer', 'states', 'stream'] as const
/** 右窗格 TradeMind 侧页面键。 */
export const TM_PAGES = ['overview', 'market', 'assets', 'strategy', 'activity', 'reports', 'run', 'stream'] as const

export type TraderPage = (typeof TRADER_PAGES)[number]
export type TmPage = (typeof TM_PAGES)[number]

export function isTraderPage(value: unknown): value is TraderPage {
  return typeof value === 'string' && (TRADER_PAGES as readonly string[]).includes(value)
}

export function isTmPage(value: unknown): value is TmPage {
  return typeof value === 'string' && (TM_PAGES as readonly string[]).includes(value)
}

const COCKPIT_PATH = '/cockpit'
const DEFAULT_PAGE = 'overview'

/**
 * 统一驾驶舱双窗格导航：trader / tm 两个页面键都挂在 query 上，
 * 各自独立切换互不影响；query 非法或缺失时回退 overview，
 * URL 可直接分享、刷新后状态不丢。
 */
export function useCockpitNavigation() {
  const route = useRoute()
  const router = useRouter()

  const traderPage = computed<TraderPage>(() =>
    isTraderPage(route.query.trader) ? route.query.trader : DEFAULT_PAGE)
  const tmPage = computed<TmPage>(() =>
    isTmPage(route.query.tm) ? route.query.tm : DEFAULT_PAGE)

  const isCockpit = computed(() => route.path === '/cockpit' || route.path.endsWith('/cockpit'))

  /** 只改 trader 一个 query 键，其余键（含 tm）原样保留；extraQuery 追加附加键（如 traderInstrument）。 */
  function setTraderPage(page: TraderPage, extraQuery?: Record<string, string>) {
    router.push({ path: route.path, query: { ...route.query, ...extraQuery, trader: page } })
  }

  /** 只改 tm 一个 query 键，其余键（含 trader）原样保留。 */
  function setTmPage(page: TmPage) {
    router.push({ path: route.path, query: { ...route.query, tm: page } })
  }

  /** 驾驶舱内返回带 tm query 的当前位置；独立页面返回 /{page}。 */
  function tmLocation(page: TmPage): RouteLocationRaw {
    if (isCockpit.value) return { path: route.path, query: { ...route.query, tm: page } }
    return { path: `/${page}` }
  }

  /** 回到双总览：整体重置到 /cockpit，query 只保留两个页面键。 */
  function resetDualOverview() {
    router.push({ path: COCKPIT_PATH, query: { trader: DEFAULT_PAGE, tm: DEFAULT_PAGE } })
  }

  return { traderPage, tmPage, isCockpit, tmLocation, setTraderPage, setTmPage, resetDualOverview }
}
