---
id: ARCH-015
type: architecture-change
title: 退出排空结果终态分类与安全恢复边界
status: implemented
created: 2026-10-13
updated: 2026-10-13
related_tasks: [TASK-20261013-10]
related_issues: [ISSUE-20261013-03]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 退出排空结果终态分类与安全恢复边界

## 背景、目标与非目标

ARCH-013部署后首个收盘（2026-10-13 14:57）出现非预期锁定：报告`model_pending=true`、`worker_active=false`，监督器halted并跨日持续（ISSUE-20261013-03）。根因是`Pipeline.close_model_worker`把`unconsumed_result`定义为"存在pending future"，不区分future已完成、已取消或仍在运行。目标：排空时给出显式、可审计的终态，正常收盘不再误锁次日；非目标：不改变监督器保护策略、不改预算计数口径（12-05）、不改响应解析（13-04）、不放宽任何风控/门禁。

## 方案与边界

排空（`close_model_worker`）从不执行任何结果，只分类并审计（`control_audit`，action=`model_drain`）：

- `no_pending_result`：无pending。
- `cancelled_before_start`：future取消成功，模型调用从未发生，无结果。
- `model_error`：future异常；失败已按调用层记录，属已分类失败。
- `expired_result_not_executed`：结果过期（valid_until已过）或账户版本已变；明确废弃。
- `valid_result_not_executed`：结果仍有效但排空不执行；持仓由确定性风险退出覆盖，计划缓存供下一轮重评。
- `worker_still_active`：超时仍未结束——保持锁定（活线程可能仍写账本）。
- `unknown_result_shape`/`unknown_result_state`：结果不可分类——保持锁定，不静默删除。
- `audit_write_failed`：终态无法审计——保持锁定（未审计的恢复不是安全恢复）。

仅后四类保持`unconsumed_result=true`。监督器`result_policy`、halted持久化与人工核对边界不变（ARCH-013的保护策略保留）；报告shutdown新增`drain_terminal_state`/`drain_detail`字段供13-05盘中摘要与人工诊断消费。

## 部署与安全

改动仅`src/trademind/pipeline.py`（close_model_worker及分类/审计辅助函数）与`tests/test_paper_run_report.py`；与13-04（paper_run.py等）、12-05（daily_budget计数）、13-05（auto_paper摘要）无文件重叠。审计载荷只含终态、instrument、action、trace_id、ISO时间与异常类型名，无凭据/原始响应。

## 实施范围与验证

宿主隔离测试：`tests/test_paper_run_report.py` 12项通过（新增截止未消费WAIT结果、过期废弃、账户版本变化、可执行结果绝不执行、模型异常、取消未启动、未知形态保持锁定正反例；既有35秒超时保持锁定契约不变）；全量回归469通过、1项预存失败属13-04在修范围（`test_strategy_failure_diagnostics.py::test_timeout_and_unknown_failure_classification`，与本改动无文件交互）。三ETF静态合法数量可达性离线核算：均T+0、100份/手，1000现金（预留100）下单价≤8.999元可达最小买量。

## 最终结果

代码边界已实施并有测试证据；新镜像未构建/部署（TASK-20261013-06统一执行），容器存量halted锁定须经授权+排空确认+账户对账后处理，真实交易日收盘不误锁、次日自动恢复待实际观察。在此之前本记录与ARCH-013并存：ARCH-013描述监督器结构，本记录定义排空终态边界并修正其"未知结果"判定范围。
