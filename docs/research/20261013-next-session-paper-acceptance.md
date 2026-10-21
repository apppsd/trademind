# 后续交易日模拟交易闭环验收报告（2026-10-13，离线工程阶段）

Task: [TASK-20261013-10](../tasks/TASK-20261013-10-保障后续交易日模拟交易闭环.md) · Owner: Claude Code · 验收: Codex（待）
本报告只记录2026-10-13离线可完成部分；真实交易日项未验，不提前声明。

## 本轮修改

- `src/trademind/pipeline.py`：仅 `close_model_worker` 及新增 `_classify_drained_result`/`_audit_drain`。排空不再把一切pending future判为未消费，改为显式终态并写 `control_audit`（action=model_drain）；仅 `worker_still_active`/`unknown_result_shape`/`unknown_result_state`/`audit_write_failed` 保持 `unconsumed_result=true`。排空从不执行任何结果。报告shutdown新增 `drain_terminal_state`/`drain_detail`。
- `tests/test_paper_run_report.py`：排空契约测试6项新增+1项扩展终态断言。
- 未修改：paper_run.py、auto_paper.py、daily_budget.py、监督器 `result_policy`、模型/响应解析、任何配置。

## A～E证据矩阵

| 标准 | 状态 | 证据/原因 |
|---|---|---|
| A 盘前可用 | 部分离线完成，盘中项未验 | 静态：三ETF均T+0、100份/手，佣金0.01%（min 0.1元）；1000现金-100预留下单价≤8.999元可达最小合法买量（本地profile核算，2026-10-13）。新鲜度/完整窗口/容器指纹/预算预留：归真实交易日盘前（13-06门禁），本日闭市未做收费探针。 |
| B 有效策略与动作链 | 未满足前置 | 13-04/05/12-05尚未完成，独立复核未发生（不得以完成声明替代）。排空误锁已修（见C）。两个刷新时点合法中文计划+history：待真实盘中。 |
| C 收盘锁定与次日恢复 | 代码+隔离测试完成，真实恢复未验 | 根因：旧`close_model_worker`把已完成/已取消future也判未消费（Issue13-03）。修复+正反例测试：截止未消费、worker活跃超时（保持锁定）、过期废弃、账户版本变化、可执行结果绝不执行且现金不变、模型异常、取消未启动、未知形态保持锁定。存量容器halted=true须13-06按授权+排空确认+对账处理。跨午休/收盘/次日/重启真实恢复未验。 |
| D 真实模拟交易闭环 | 未验 | 需后续实际交易日、自然策略与真实JEV；本日闭市且依赖未完成，不得以离线证据替代。 |
| E 诚实交付 | 工程证据已留 | 隔离fixture BUY→SELL正向链路测试通过：`test_async_valid_result_executes_on_next_tick_with_original_trace`（BUY执行+原trace）、`test_replay_new_holding_timeout_differs_from_old`（有仓SELL退出）、`test_fast_decision.py` 全链。仅工程证据，不替代D。 |

## 验证命令与结果（宿主，未调用模型/网络）

- `.venv/Scripts/python.exe -m pytest tests/ --deselect tests/test_strategy_failure_diagnostics.py::test_timeout_and_unknown_failure_classification` → **469 passed, 1 deselected, 1 既有Starlette警告（8.93s）**。排除项为13-04（Antigravity，in_progress）在修的 `failure_category_text` 文案断言，与本改动无文件交互。
- `.venv/Scripts/python.exe -m pytest tests/test_paper_run_report.py tests/test_auto_paper.py` → 29 passed（排空12+监督器等17）。
- 基线说明：原447为本Task开始前数字；469含13-04新增的在修测试文件。本Task净增6项测试。

## 未验清单（移交后续）

1. 13-04空输出根因、13-05盘中退避、12-05计数口径：各自Owner按原标准。
2. 13-06：新镜像构建/部署（含本修复）、存量halted授权处理、免费门禁与有界真实核验。
3. 真实交易日：盘前A项（新鲜度/可买性/指纹）、盘中B项（两时点合法计划、逐标的调度不饿死）、收盘后核对 `shutdown.drain_terminal_state` 与次日自动恢复、D项BUY→SELL→清仓证据。
4. 逐标的调度公平性：单worker串行+全局评估冷却为既有设计，本轮代码审阅未发现永久饿死路径；若真实盘中发现，先建Issue再修。

## 残余风险

- worker真超时仍活跃时锁定属预期保护，需人工核对，不是缺陷。
- 审计载荷只含终态/标/action/trace/时间/异常类型，无凭据与原始响应；报告白名单测试继续覆盖。
- 容器运行中的旧镜像行为不变（无自动更新），在13-06部署前收盘仍会按旧逻辑误锁。
