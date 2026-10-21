---
id: ARCH-011
type: architecture-change
title: 多市场交易资格、隔离执行引擎与候选筛选
status: implemented
created: 2026-10-11
updated: 2026-10-11
related_tasks: [TASK-20261008-06, TASK-20261008-07, TASK-20261008-08, TASK-20261008-09]
related_issues: [ISSUE-20261008-05]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-011 多市场交易资格、隔离执行引擎与候选筛选

## 状态与背景

implemented。在 ARCH-005 落地只读观察与全市场快照发现后，`ISSUE-20261008-05` 记录了执行引擎与交易规则未适配的架构缺口。本次架构实施为境内可转债、LOF、港股（直连/港股通）以及境内期货建立了严格的规则库、账户权限门禁、隔离账本与候选筛选机制。

## 目标与非目标

- **目标**：
  1. 建立逐只交易规则库（`market.rules`）与账户权限门禁（`market.access`），支持官方源验证、生效期、T+0/T+1、停牌/强赎/退市、最小单位、价格档位与费率。
  2. 实现可转债（10张/手、20%临停）与 LOF（仅允许QDII T+0，境内股票T+1严格否决）独立纸面模拟执行。
  3. 实现港股独立港币账本（HKD零串币）与期货保证金制度（开平仓、保证金占用、合约乘数、多空盯市与平仓盈亏）。
  4. 实现多市场候选池筛选器（`market.selector`）与 CLI `trademind market-select`，严格按流动性排序与预算配额限制，杜绝模型调用预算随标的数量放大。
- **非目标**：
  - 不连接券商实盘，不将 500 元本金与杠杆放大，不撮合连续主力合成代码（如 RB0/RB888），不跨币种自动换汇。

## 方案与核心设计决策

1. **分级规则与权限拦截**：
   - 细分账户权限（`cn_fund`、`convertible_bond`、`hk_stock_direct`、`hk_stock_connect`、`futures_standard`、`futures_special`、`futures_financial`）。
   - 港股通（Southbound Connect）标的名单逐只核验；无港股直连权限时，非港股通标的拦截（`hk_connect_ineligible`）。
   - 可转债强赎公告（`is_called_early=True`）与停牌拦截（`instrument_suspended`）。
2. **多资产隔离账本（MultiAssetLedger）**：
   - 双币种独立核算：`cash_cny` 与 `cash_hkd`，禁止跨币种穿透扣款。
   - 期货保证金核算：以 `price * qty * contract_multiplier * margin_rate` 占用保证金，平仓释放保证金并结算差额盈亏；现货以全额本金+费用结算。
   - 严格守恒律：单轮 BUY→SELL→清仓保证 `cash_end == cash_start + pnl - fees`。
3. **确定性候选筛选与预算控制**：
   - 筛选器按 流动性（Turnover）在各市场排序，每个市场严格取 `top_k_per_market`（默认1），总数受 `max_total_candidates`（默认3）硬顶截断。
   - 候选增加不放大每日调用预算，筛选过程零模型调用（`model_calls: 0`）。

## 模块与接口影响

- `src/trademind/domain/enums.py`: 扩展 `Market`（`XHKG`, `XSGE`, `XDCE`, `XZCE`, `CCFX`, `XINE`）。
- `src/trademind/domain/instruments.py`: 允许期货品种配置 `short_selling_allowed=True`。
- `src/trademind/market/rules.py`: Canonical 规则库与 `MarketRule` 数据结构。
- `src/trademind/market/access.py`: `evaluate_eligibility` 与权限核验逻辑。
- `src/trademind/market/profiles.py`: `convertible_bond_profile`、`lof_profile`、`hk_stock_profile`、`futures_profile`、`get_multi_asset_profile`。
- `src/trademind/execution/multi_asset_paper.py`: `MultiAssetLedger` 与 `MultiAssetPaperBroker`。
- `src/trademind/market/selector.py`: `MultiAssetCandidateSelector`。
- `src/trademind/runner.py`: 增加 `market-select` 子命令。

## 验证证据

- `tests/test_market_rules.py`: 20 项测试全部通过（覆盖境内ETF、LOF、可转债、港股直连/港股通、期货普通/特殊/金融类别）。
- `tests/test_multi_asset_execution.py`: 8 项测试全部通过（覆盖可转债T0清仓守恒、手大小校验、强赎拦截、LOF T0/T1差异、港币隔离、期货保证金与盈亏）。
- `tests/test_candidate_selector.py`: 3 项测试全部通过（流动性排序、预算硬顶截断、CLI零模型调用）。
- 全量回归测试：`414 passed, 0 failed`。
