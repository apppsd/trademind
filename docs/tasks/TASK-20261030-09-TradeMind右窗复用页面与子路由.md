---
id: TASK-20261030-09
type: task
title: TradeMind右窗复用页面与子路由
status: draft
priority: medium
created: 2026-10-30
updated: 2026-10-30
owner: 待认领（Claude；Codex仅规划）
depends_on: [TASK-20261030-07]
related_issues: [ISSUE-20261030-03]
related_architecture: [ARCH-027]
---

# TASK-20261030-09 TradeMind右窗复用页面与子路由

## 任务信息

- 交付层级：本地SAMPLE/readonly工程交付；不增加真实接线能力。
- 启用条件：07窗格/导航合同可用，先向05及现有页面Owner交接；未认领。规划main / F:/Project/TradeMind；实施需补Owner、分支/Worktree。
- 拟议范围：新增 `frontend/src/components/ops/TradeMindPane.vue`、`demo/tradeMindCockpitSample.ts`及窗内资源provider；复用 `pages/{Overview,Market,Assets,Strategy,Activity,Reports,RunCenter}Page.vue`，必要时改窗内链接/布局；`composables/useWorkbench.ts`、`useResource.ts`、`services/workbenchApi.ts`仅为可选注入SAMPLE资源与取消轮询的最小适配；新增 `tests/tradeMindPane.spec.ts`。已有 `adapters/opsReadonly.ts`不扩白名单；07壳/router由07 Owner改。

## 背景与需求

复用七个已存在页面的呈现与交互，挂到右窗子路由，不能将整套页面复制到新目录。右导航：总览/行情/资产/策略/交易记录/日报/运行中心，运行流为可选附加页。依据[Issue30-03](../issues/ISSUE-20261030-03-双系统大屏导航与内容失衡.md)，内部RouterLink、事件链接及日报外链均须审计，避免逃出双壳。具体query与资源模式见[简报](../research/BRIEF-20261030-dual-ops-cockpit-claude.md)。

首发复用现有呈现，给双壳注入固定SAMPLE资源；注入范围限于驾驶舱，旧只读调用入口的默认语义不变。不能只在fetch失败后才补样例，也不能因为挂载旧页就启动既有5/15/30/60秒轮询。状态/账户身份按窗，旧全局online状态不代表双侧LIVE。

## 不包含

不改后端/交易/数据Schema、资金风控、模型或trader；不扩API、启用轮询、读凭据、代理真实主机或联150。不改依赖，不部署/重启/commit/push。真实只读运行接线仍归06。

## 验收标准

- [ ] 七页在右侧可用，沿用原有图表/表格/筛选与中文语义，半屏重排；左侧持续可见。
- [ ] 所有页内详情、总览事件、资产→日报、策略→交易记录均在右窗切换并保留左query/筛选/滚动；直接链接、刷新及历史可恢复。
- [ ] SAMPLE覆盖列表、账户曲线/持仓、行情标的详情、策略计划、活动详情、日报日期与运行状态；日期无样例时显示样例空态，不访问真实API。
- [ ] 初次挂载、手动刷新、切页、停留及日报Markdown/下载操作均零业务网络请求；下载可用本地样例Blob或明确禁用并解释，不保留真实接口外链。切页只挂载当前右页，卸载清理计时器/资源。
- [ ] loading/empty/missing/stale/error/unknown与source/as_of按窗保留；无真实字段被混入SAMPLE，无全局LIVE误标。
- [ ] 工程回归验证原页功能与已有只读适配边界，10再验双方密度/视觉。

## 执行计划

- [ ] 盘点七页取数、轮询、内部/外部链接，先实现驾驶舱内SAMPLE注入边界。
- [ ] 逐页挂右宿主，修正窗内链接和宽度；测试左状态不受影响。
- [ ] 完成工程测试与网络断言，交10联合验收。

## 实施记录与验证

2026-10-30 Codex源码确认Overview有多个轮询资源、Reports有真实Markdown外链；将其纳入实施风险，未改代码或启动API。规划检查见简报，当前draft。

## 文档同步契约 / Documentation Sync Check

- 本卡、`docs/tasks/index.md`、Issue30-03及`docs/issues/index.md`：复用范围、测试与未验项。
- 实现后 `docs/project/code-map.md`（宿主/provider）、`flows.md`（窗内跳转与样例资源）、`current-status.md`（SAMPLE与原只读能力区别）；README/setup/operations入口统一由07同步。
- 简报若改变模式或链接合同需先更新；ARCH-027由07/10 Owner统一记同次演进，保持implementing；无后端/安全/持久化变化，roadmap不变。
- [x] 规划未伪装已实现；[ ] Owner交接、七页回归/网络检查与联合验收后完成文档同步。
