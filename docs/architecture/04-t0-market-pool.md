---
id: ARCH-004
type: architecture-change
title: T0已登记行情池与轮询
status: implemented
created: 2026-10-08
updated: 2026-10-08
related_tasks: [TASK-20261008-03]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-004 T0已登记行情池与轮询

## 背景与目标

此前运行入口一次只处理一个instrument；用户希望所有已登记T0都有数据监控。

## 变更前与方案

单标的改为配置池默认逐标的轮询；显式instrument保留。独立t0-data入口只加载行情相关安全配置，不加载模型凭据，不创建Pipeline。行情Router短缓存用于复用已观测分钟数据；运行先抓取再取决策时点，避免fetched_at晚于as_of导致真实数据永远不可见。

## 模块/接口/数据流影响

配置池→免费行情Router→抓取后时点/质量检查→逐标的状态；paper入口同池依次调用原Pipeline，资金/预算仍共享。使用现有SQLite/单进程，无部署变化。不改JEV协议或放宽风控。

## 数据所有权与代码范围

行情短缓存为临时只读观测数据，账本仍由Ledger维护。config/runner/router及测试和文档，详见关联Task。

## 非目标

不将“本地全部已登记”称为“全市场全部T0”；全市场扩展须核验规则。默认总资金500、总敞口300、单只200/最多1个持仓不变。

## 测试与实施结果

3只配置、全池轮询、数据独立入口和30秒观测缓存已实现；203项离线测试通过。免费实测受连接阻塞，见[报告](../research/20261008-t0-pool-verification.md)，不标真实行情已可用。
