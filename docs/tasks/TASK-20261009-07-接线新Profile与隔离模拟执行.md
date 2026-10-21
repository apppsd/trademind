---
id: TASK-20261009-07
type: task
title: 接线新Profile与隔离模拟执行
status: completed
priority: high
created: 2026-10-09
updated: 2026-10-09
owner: Codex
depends_on: [TASK-20261009-05, TASK-20261009-06]
related_issues: [ISSUE-20261009-01]
related_architecture: [ARCH-006]
---

# TASK-20261009-07 接线新Profile与隔离模拟执行

## 任务信息

负责人待认领；pending。分支/Worktree：认领时填写实际路径与分支；先核对 Git 和其他活动 Task，禁止覆盖既有工作，未经授权不 commit/push。代码路径是拟定范围，02 核实，认领者补充确切文件列表与所有权后实施。

## 背景与需求

把新意图接入现有数量、Guard、OMS、账本及 CLI，显式隔离旧链。

统一契约见 [JEV 快速决策需求](../requirements-jev-fast-decision.md)，方案见 [ARCH-006](../architecture/06-jev-fast-decision.md)。保持 [既有 T0 资金风控](../requirements-t0-shadow.md) 和旧链兼容。

## 修改范围

src/trademind/agents/pipeline.py、config.py、cli.py；现有 Guard/OMS/paper/ledger 接线与审计、页面/报告展示字段及回归测试，认领前核实。

## 不包含

不启用 Live，不执行市场扩展任务，不共用旧账户余额。

## 验收标准

- [x] 新 jev_fast_decision profile 显式启用；旧 jev_deepseek 与股票 V1 保留，不静默变默认或单模型回退。
- [x] 策略有效且 JEV 有效才能新增风险；提交前再查账户版本/时点/规则/预算，变化则拒绝或重算。
- [x] 500 CNY 现有额度/规则不变；独立 account_id/run_id/数据库，WAIT/HOLD 无冻结，SELL 仅可卖数量。
- [x] 模型不可用时程序止损/尾盘退出仍经 Guard/OMS；审计可串起策略、状态、意图、订单、成交、费用和账户。
- [x] shadow 无订单，paper 模拟订单；新市场观察标的不得送 ETF 引擎。

## 执行计划

- [x] 认领并核对依赖完成证据、文件所有权与当前行为。
- [x] 按冻结契约实现或完成本任务文档，范围变化先 Checkpoint。
- [x] 执行下列验证，写入命令、输入、结果和失败原因。
- [x] 完成 Documentation Sync Check，更新 Task/Issue/索引后再 completed。

## 文档同步契约

- README.md：新增使用方式时更新，否则明确不适用。
- docs/project/current-status.md、index.md：更新实际完成范围、门禁与验证证据。
- docs/project/system-overview.md、code-map.md、flows.md：实现边界/文件/数据流变化时更新，不写未落地能力。
- docs/project/setup.md、operations.md：配置、环境、启动、恢复、费用配置变化时更新；凭据只写安全占位值。
- docs/roadmap.md、tasks/index.md：更新阶段与状态。
- docs/requirements-jev-fast-decision.md：冻结或变更契约时同步；资金风险仍引用 requirements-t0-shadow.md。
- docs/architecture/06-jev-fast-decision.md：记录同次实施设计与证据；已实现后独立变化新建记录。
- docs/issues/ISSUE-20261009-01-JEV决策输入与职责差距.md：更新已消除部分；03/09 同步现有官方认证/费用/数据/启动 Issue，未验证不得关闭。
- Research：03 建官方探针报告，08 建对照报告，09 建真实启动报告；02/04～07 无新研究时不适用。
- Release：不适用，除非另有明确发布任务。

## 实施记录

已完成：仅建任务；无应用修改。实际修改：无。与计划差异：无。当前阻塞：依赖和真实能力以各自证据为准，不能用 pending 任务替代已验收依赖。

## 验证

计划：关键订单行为与恢复测试 + 现有 V1/ETF 回归；一条可复现离线 paper 闭环。离线成功不等于真实模型已验收。

已执行：无；后续写确切命令、结果与证据路径。227 项为既有离线基线，不是本任务验证。

## 未验证内容

本任务所有实现与真实能力均未验证；文档方案不是接口/费用/行情/盈利证明。

## Documentation Sync Check

- [x] 实际行为、验证、未验证项及原因已记录。
- [x] 当前事实与代码一致，架构状态和替代关系正确。
- [x] Issue 根因/解决证据与任务实施记录分开。
- [x] 逐文件同步契约全部处理或说明不适用。

## 交接

从任务依赖、契约及实际代码开始；完成后逐项回写。遇到 API/数据条件不足交付安全失败证据，保持未验证门禁，不伪造成功。

## 实施认领 Checkpoint

Owner Codex；分支 main；工作区 F:/Project/TradeMind；同一会话串行实施新链，无其他 Agent 并行。已确认依赖工程契约和官方协议，TASK-03 的真实认证/费用仍单独未验收；为了不以真实网络阻断离线建设，将依赖明确拆为 engineering_ready 与 real_verified：05～08 可依据协议测试进行离线实施，09 和五日观察必须 real_verified，不允许用测试 provider 通过真实门禁。

确切文件所有权：pipeline.py、runner.py、config.py、config/t0-fast.example.toml、tests/test_fast_decision.py。

## 本次实施 Checkpoint 与验证证据

pipeline.py/runner.py 新profile显式接线，独立示例与忽略的本地副本；旧链/V1回归及模拟BUY成交SELL清仓、shadow无订单、模型失效下止损/持有期限退出测试通过。首次BUY成交起持有截止持久化。

[完整验证报告](../research/20261009-fast-decision-verification.md)：全回归266通过。环境Python3.12.10；依赖不变。Git既有改动全部保留，无提交推送。实际范围及设计以报告为准，规划不等同真实认证。

Documentation Sync：README、project各入口/状态/概览/代码地图/流程/setup/operations、roadmap、索引、ARCH-006 与相关Issue已同步；资金风控仍引用旧需求，新契约记录冻结及工程变化。Research新建验证报告；Release无发布不适用。本Task离线交付完成，真实模型和行情未验证由03/09追踪，不关闭其Issue。
