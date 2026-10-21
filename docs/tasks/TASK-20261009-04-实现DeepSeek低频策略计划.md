---
id: TASK-20261009-04
type: task
title: 实现DeepSeek低频策略计划
status: completed
priority: high
created: 2026-10-09
updated: 2026-10-09
owner: Codex
depends_on: [TASK-20261009-02]
related_issues: [ISSUE-20261009-01]
related_architecture: [ARCH-006]
---

# TASK-20261009-04 实现DeepSeek低频策略计划

## 任务信息

负责人待认领；pending。分支/Worktree：认领时填写实际路径与分支；先核对 Git 和其他活动 Task，禁止覆盖既有工作，未经授权不 commit/push。代码路径是拟定范围，02 核实，认领者补充确切文件列表与所有权后实施。

## 背景与需求

实现固定 deepseek-flash 生成、校验、缓存与持久化策略计划，替代每轮重复分析。

统一契约见 [JEV 快速决策需求](../requirements-jev-fast-decision.md)，方案见 [ARCH-006](../architecture/06-jev-fast-decision.md)。保持 [既有 T0 资金风控](../requirements-t0-shadow.md) 和旧链兼容。

## 修改范围

拟新增 src/trademind/domain/strategy.py、src/trademind/agents/strategy_planner.py；现有 storage/config 的计划 schema 与迁移、Prompt 文件及相关测试，认领时冻结实际路径。

## 不包含

不新增 JEV 动作组合与 OMS；不要求真实模型才能完成离线模块。

## 验收标准

- [x] 结构化 Trend/Risk/Critic 三维分析、可计算条件、退出和失效条件；无可交易策略可正常返回。
- [x] 模型固定 deepseek-flash，计划无法更改硬风控、合法证券或资金。
- [x] 缓存按证券/日/版本；过期、日切、重启、Prompt/规则变更使旧计划失效，禁止未来证据。
- [x] 非破坏持久化，plan_id 与证据关联；错误/修复受总预算约束。

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

计划：用合法/缺字段/过期/未来时间/重启/版本变化样本验证；不靠一次响应正常作为覆盖。

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

## 认领与范围核实

Owner Codex；分支 main；工作区 F:/Project/TradeMind。确切范围 domain/strategy.py、agents/strategy_planner.py，复用 Repository coalesce 持久化；对应策略契约/缓存测试。

## 本次实施 Checkpoint 与验证证据

实现 domain/strategy.py、agents/strategy_planner.py，缓存复用/重启/日切/配置与规则失效、严格条件和时点测试通过。固定deepseek-flash，不授订单权限；完成离线模块，真实运行由03/09另验。

[完整验证报告](../research/20261009-fast-decision-verification.md)：全回归266通过。环境Python3.12.10；依赖不变。Git既有改动全部保留，无提交推送。实际范围及设计以报告为准，规划不等同真实认证。

Documentation Sync：README、project各入口/状态/概览/代码地图/流程/setup/operations、roadmap、索引、ARCH-006 与相关Issue已同步；资金风控仍引用旧需求，新契约记录冻结及工程变化。Research新建验证报告；Release无发布不适用。本Task离线交付完成，真实模型和行情未验证由03/09追踪，不关闭其Issue。
