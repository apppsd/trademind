---
id: ARCH-016
type: architecture-change
title: 持仓估值失效隔离与新风险否决边界
status: implemented
created: 2026-10-14
updated: 2026-10-14
related_tasks: [TASK-20261013-13]
related_issues: [ISSUE-20261013-05]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 持仓估值失效隔离与新风险否决边界

## 背景、目标与非目标

实施前，决策路径按单一标的取参考价：任一持仓行情失效（stale/缺分钟/ProviderError）会让整个`run_decision`失败，同一账户其他健康持仓的确定性风险退出（止损/持有期限/尾盘）被一并阻断。目标：估值按持仓独立尝试，单点失效只记录、不阻断其他标的的风险退出；同时任何持仓缺合法新价时Guard否决新开风险，避免在账户价值不可验证时加仓。非目标：不改变行情质量门禁、不为失效标的补造价格、不改变风控阈值与预算口径、不放宽对新行情完备性的要求。

## 方案与边界

`pipeline.py` `run_decision`：

- 逐持仓独立估值：每个持仓单独`try`参考价，`NotFresh`/`ProviderError`记入`valuation_failures`（含标的与异常类型），不再向上中止整轮决策。
- Guard新增`valuation_complete_provider`：任何持仓估值不完整时，BUY意图以`incomplete_account_valuation`被VETO；SELL/风险退出不受该检查影响，健康标的照常退出。
- 诊断保留：`valuation_failures`在context构建前写入`result.diagnostics`，并在T0构建与fast链整体替换diagnostics后再次补写，保证失败记录在任何退出路径可见。
- 双视图并集：context builder自身回退成本价的`valuation_incomplete`与估值循环失败取并集，Guard BUY否决与日初基线规则始终看最坏情况。
- 日初基线：`on_new_trading_day(prices_complete=...)`，行情不完整不落成新一日权益基线，防止以残缺估值污染当日损益基准。

失效标的本身不交易、不补价、不改持仓；全部报价失效时整轮仍拒绝且不调用模型。

## 部署与安全

改动`src/trademind/pipeline.py`（估值循环、provider接线、diagnostics保留）、`src/trademind/guard/guard.py`（估值完备性检查）、`src/trademind/portfolio/ledger.py`、`src/trademind/context/builder.py`（cost回退上报）。无新表、无新进程、无凭据；诊断载荷只含标的ID与异常类型/消息。

## 实施范围与验证

2026-10-14宿主实测：`tests/test_fast_architecture_repairs.py`新增`test_stale_holding_does_not_block_other_symbol_risk_exit`（B标的风险退出成交、失效标的无订单无成交、无新日基线、全程无模型调用）、`test_incomplete_valuation_vetoes_new_buy`（BUY VETO）、`test_all_quotes_stale_refuses_without_orders`（全失效拒绝且零订单）；连同`test_fast_decision.py`/`test_t0_runtime_repairs.py`/`test_t0_risk_guard.py`共76项通过。全量回归481项通过（见[TASK-20261013-13](../tasks/TASK-20261013-13-隔离持仓行情故障与风险退出.md)）。

## 最终结果

代码边界已实施并有测试证据。真实盘中多种行情故障组合下的表现未验，由TASK-20261013-05/06的真实运行验收判断；本记录定义数据流边界，与ARCH-015（排空终态）并存，不替代ARCH-015。
