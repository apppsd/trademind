import { createRouter, createWebHashHistory } from 'vue-router'

/** hash 导航：不占用 /api/ 与 /reports/ 路径。 */
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'overview', component: () => import('./pages/OverviewPage.vue') },
    { path: '/market', name: 'market', component: () => import('./pages/MarketPage.vue') },
    { path: '/assets', name: 'assets', component: () => import('./pages/AssetsPage.vue') },
    { path: '/strategy', name: 'strategy', component: () => import('./pages/StrategyPage.vue') },
    { path: '/activity', name: 'activity', component: () => import('./pages/ActivityPage.vue') },
    { path: '/reports', name: 'reports', component: () => import('./pages/ReportsPage.vue') },
    { path: '/run', name: 'run', component: () => import('./pages/RunCenterPage.vue') },
  ],
})
