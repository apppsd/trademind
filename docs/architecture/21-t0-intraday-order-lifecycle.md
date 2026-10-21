---
id: ARCH-021
type: architecture-change
title: T0日内模拟订单生命周期（日单/尾盘撤销/SELL保留）
status: implemented
created: 2026-10-17
updated: 2026-10-17
related_tasks: [TASK-20261016-09, TASK-20261013-06]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-021 T0日内模拟订单生命周期

## 2026-10-17独立复核补充（已修复）
首次日单/尾盘撤销已实施，但[Task17-03](../tasks/TASK-20261017-03-复验五项完成声明与订单生命周期.md)发现取消释放未沿用操作时点，虚拟时钟下写入未来账户事件。**同日修复**：`cancel(at=)/_cancel(order_id, at=)`贯通操作时钟，`expire_stale_orders`传`now`；回归钉住释放事件`effective_at`=操作时点（非墙钟）。Task17-04独立复验确认反例过、时点一致；恢复implemented。冻结语义不变。

## 背景
[Issue16-06](../issues/ISSUE-20261016-06-日内模拟买单缺少跨日失效与撤单边界.md)：前日短时计划产生的未成交BUY可保留为次日新仓来源（隔离复现：10-12的BUY在10-13成交开仓），冻结资金持续占用。Guard批准TTL只限定提交时间，不是订单时效；OMS.expire_approvals只释放无订单的授权。

## 语义（冻结，2026-10-17实施）
- **BUY=日单**：交易日变更（北京日）即过期撤销；`no_new_entry_time`（默认14:50）后仍未成交的BUY提前撤销——尾盘不再开新仓。
- **SELL保留**：减仓/风险退出永不自动撤销，跨日仍可成交（降风险方向）。
- **UNKNOWN/SUBMITTING不动**：保持既有恢复门禁（显式对账后才可终结）。
- **Guard TTL与订单时效分离**：`guard.expires_at` 只约束提交；订单生命周期由本机制管理。
- **撤销原子且幂等**：复用 `_cancel`（CANCEL_PENDING→CANCELLED + `ledger.release` 冻结余量 + 审计）；部分成交余单撤销释放剩余冻结，已成交部分照常入账。
- **跨午休不失效**：按北京日判断，同日（含午休前后）继续有效。
- **V1股票日线模式不适用**：仅 `t0.enabled` 时启用（T+0/日内语义）；V1的 `fill_lag_bars` 次日成交是设计行为，保留。
- 无Schema迁移：复用orders载荷的created_at/side/status。

## 数据流
`pipeline._poll_fills(now)` 开头调用 `oms.expire_stale_orders(now, t0.no_new_entry_time)` → 过期BUY → CANCELLED + 冻结释放 + oms cancel审计 → 存活订单继续正常撮合。

## 影响与边界
不产生新风险；已挂SELL（清仓）不被取消。UNKNOWN仍锁恢复。真实跨日撤销观察归Task13-06后续会话。

## 验证
`tests/test_t0_oms_execution.py` 新增跨日失效/尾盘撤销/SELL保留/UNKNOWN豁免/部分成交幂等回归；全量pytest通过（见Task16-09验证节）。
