# 离线成本与撮合压力核查报告（TASK-20261025-05）

初版执行日：2026-10-25；2026-10-26独立复验发现时序/输入门禁缺口后已补足并重算下列产物（tool_version=2）。初版“全绿”等于覆盖充分的结论不成立，根因与修复见[Task26-01](../tasks/TASK-20261026-01-独立复验并补足审查批次.md)、[Issue26-03](../issues/ISSUE-20261026-03-成本回放时序与输入门禁不完整.md)。工具：`src/trademind/evaluation/cost_stress.py` + `scripts/evaluate_t0_cost_stress.py`（离线、只读、无网络、无生产写入、无模型调用）。**不构成策略正期望的证据**。

## 1. 方法与输入契约

- 输入为显式 JSON 档案包（`bundle_version=2`）：增加档案截止`as_of`、每根bar的`available_at`、账户现金储备/单票/总敞口限额；意图须满足核验T0 ETF的整手与tick规则、正数量/价格，bar须满足价格/量/时序约束；仅支持无初始冻结/持仓的隔离账户。缺失费用保持unknown，pending>0使净结果unknown。旧v1包直接拒绝，不能默认补造真实可见时点；本报告合成包的available_at=bar_time是显式合成假设。真实零交易包无bar，不涉及补造行情。
- **A. 往返盈亏平衡成本表**：给定名义本金（100/200 元）单次往返的手续费（生产 `compute_fees` 语义：`max(名义×费率, 最低佣金)` + 卖出印花税 + 过户费，Quantize 0.01）+ 双边滑点，得出盈亏平衡收益率。无需任何真实成交。
- **B. 执行假设压力回放**：按提交、行情可见、成交事件排序；提交时冻结含费现金或可卖持仓，同刻先提交后入账，不预支同刻/未来成交。按后续合格bar的available_at选第k+1根，超过as_of不使用。买价high×(1+bps)向上对齐tick、卖价low×(1−bps)向下对齐tick，保留限价；未知/零量或单价bar不假设可成交。参与率在同bar所有订单间共享、按整手向下取整；每意图一次尝试，未成交余量按显式评估规则取消，缺未来bar则保持pending/incomplete。账户重算现金/冻结/敞口/可卖量，拒绝非法后续订单。未平仓时另给现金流和权益损益，不能把买入支出叫亏损；输出可见bar采样权益曲线与回撤。工具仍不模拟生产完整调度、交易日历或模型对新账户路径的再决策。

## 2. 冻结场景（敏感性参数，非真实券商报价）

| 场景 | 滑点/边 | 最低佣金/边 | 延迟 | 参与率 |
|---|---|---|---|---|
| baseline | 5bps | 0.1 | 0 bar | 不限 |
| slippage-10bps / 20bps | 10 / 20 bps | 0.1 | 0 | 不限 |
| commission-min-1 / 5 | 5bps | 1 / 5 元 | 0 | 不限 |
| delay-1 / delay-2 | 5bps | 0.1 | +1 / +2 bar | 不限 |
| participation-10pct | 5bps | 0.1 | 0 | bar 成交量 10% |

## 3. 结果摘要

### A. 往返盈亏平衡成本（手算可复核）

| 名义本金 | 5bps+0.1 | 5bps+1元 | 5bps+5元 |
|---|---|---|---|
| 100 元 | 0.30 元（0.30%） | 2.10 元（2.10%） | 10.10 元（10.10%） |
| 200 元 | 0.40 元（0.20%） | 2.20 元（1.10%） | 10.20 元（5.10%） |

手算示例（100 元、5bps、min 0.1）：买费 `max(100×0.0001, 0.1)+100×0.00001=0.10`，卖费同 0.10，滑点 `2×100×0.0005=0.10`，合计 0.30。**最低佣金 5 元/边时 100 元往返成本 10.10 元（10.1%）——小额往返被最低佣金直接摧毁**。摊销当日模型成本需另加 `模型成本/本金` 的收益（见 B 包：0.197 元 ≈ 本金的 0.02%～0.2%，视敞口而定；上限 5 元不是每天必花）。

### B. 合成往返回放（足够档案，全部场景可评估）

输入：[20261025-cost-stress-synthetic-bundle.json](20261025-cost-stress-synthetic-bundle.json)（**合成数据，非真实成交**；价格取自 2026-10-23 local-1k 的 513050.SH 0.948–0.951 档位），报告：[20261025-cost-stress-synthetic-report.md](20261025-cost-stress-synthetic-report.md)。

| 场景 | 交易现金净额 | 费用合计 | 组合净额（含模型成本 0.20） |
|---|---|---|---|
| baseline | −0.70 | 0.20 | −0.90 |
| slippage-10bps | −0.70 | 0.20 | −0.90 |
| slippage-20bps | −0.90 | 0.20 | −1.10 |
| commission-min-1 | −2.50 | 2.00 | −2.70 |
| commission-min-5 | −10.50 | 10.00 | −10.70 |
| delay-1 / delay-2 | −0.70 | 0.20 | −0.90 |
| participation-10pct | −0.70 | 0.20 | −0.90 |

关键观察：

- 同限价同数量路径下，提高成本不改善净结果（−0.70 → −0.70 → −0.90）；tick取整可使相邻滑点场景相等，不能宣称严格递减。费用每笔只扣一次，账本可逐笔复算（tests/test_cost_stress.py）。初版向最近tick取整低估不利成交，原−0.50基线仅为首次历史，已由上表替代。
- 触价变化会改变路径：把限价压到滑点边界后，20bps 场景不成交、状态转 `insufficient_data`，未成交原因明确列出；报告不对跨路径净值强加单调性（::test_worse_fill_can_break_the_path_without_forced_monotonicity）。
- 延迟场景只使用意图之后第 k+1 根可见 bar（::test_delay_uses_only_bars_after_intent_and_skips_extra_bars）；缺 bar 时状态 `incomplete`，不用未来信息补（::test_missing_bars_report_incomplete_without_future_information）。
- 隔离账户重算合法性：初始现金不足时 BUY 被拒、其后的 SELL 因无持仓连锁被拒（::test_replay_recomputes_cash_and_blocks_illegal_orders）。
- 零交易仍计入实际模型成本（−0.20）；模型成本 unknown 时组合净额为 unknown 而非 0（::test_zero_trades_still_carry_model_cost_and_unknown_stays_unknown）。

### C. 真实本地档案：证据不足（如实报告）

输入：[20261025-cost-stress-local-zero-trade-bundle.json](20261025-cost-stress-local-zero-trade-bundle.json)（`paper-t0-local-1k` 2026-10-23当日日报实际1441条已留档WAIT，**零 BUY/SELL 意图**；初版写24次WAIT/HOLD是误记，已独立核对日报纠正；模型成本settled为0.196991805180元配置估值，不是供应商账单），报告：[20261025-cost-stress-local-zero-trade-report.md](20261025-cost-stress-local-zero-trade-report.md)。

结论：全部 8 场景状态 `insufficient_data`，交易现金净额 0，组合净额 = −0.196991805180 元（纯模型成本）。**本地真实档案目前不能支持任何关于执行成本或盈利能力的结论**；真实样本外对照归 TASK-20261009-08。

## 4. 验证状态

- 初版自动化：`tests/test_cost_stress.py`12项通过（2026-10-25），未覆盖后发现边界；2026-10-26补足独立反例与固定时钟后，最新命令/计数见Task26-01。
- 手算复核：A 表 6 组数字与 §3 手算一致（含 min=5 的 10.10/10.20）。
- 未验证 / 超出范围：真实成交、真实盘口队列、真实券商费率、策略正期望；本工具的参与率/滑点是保守假设，不校准 JEV 为市场胜率，不接入实盘。

## 5. 交接与关联

- TASK-20261009-08（真实对照报告）可直接引用本报告 A/B 表与两份证据包；档案不足时按 §C 口径表述，不得改称收益证据。
- 关联 Issue：ISSUE-20261007-14（模型成本与摩擦成本）；ARCH-006（评估域接口新增 `evaluation/cost_stress.py`，随本 Task 记录）。
