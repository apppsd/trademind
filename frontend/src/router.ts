import { createRouter, createWebHashHistory } from 'vue-router'

/** hash 导航：不占用 /api/ 与 /reports/ 路径。旧路径全部并入统一驾驶舱，页面键挂 query。 */
function toCockpit(trader: string, tm: string) {
  return { path: '/cockpit', query: { trader, tm } }
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: () => toCockpit('overview', 'overview') },
    { path: '/cockpit', name: 'cockpit', component: () => import('./pages/CockpitPage.vue') },
    { path: '/ops-stream', redirect: () => toCockpit('stream', 'stream') },
    { path: '/overview', redirect: () => toCockpit('overview', 'overview') },
    { path: '/market', redirect: () => toCockpit('overview', 'market') },
    { path: '/assets', redirect: () => toCockpit('overview', 'assets') },
    { path: '/strategy', redirect: () => toCockpit('overview', 'strategy') },
    { path: '/activity', redirect: () => toCockpit('overview', 'activity') },
    { path: '/reports', redirect: () => toCockpit('overview', 'reports') },
    { path: '/run', redirect: () => toCockpit('overview', 'run') },
  ],
})
