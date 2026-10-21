---
id: ARCH-003
type: architecture-change
title: T0分钟运行与持久化安全门禁
status: implemented
created: 2026-10-08
updated: 2026-10-08
related_tasks: [TASK-20261008-02]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-003 T0分钟运行与持久化安全门禁

## 背景与目标

ARCH-002双模型规划保留；补齐本地实现缺失的运行接线。JEV协议本次排除。

## 变更前

主决策使用日线；退出辅助函数未调用；计数仅内存；CLI局部改配置、重复时点、未来撮合和错误成功。

## 方案与核心设计决策

T0使用分钟Context及可见参考价；已有持仓先执行确定性风险退出，经原Guard/OMS，随后才允许模型评估新意图。计数从账户成交/审批事实恢复。模型调用额度和费用预留持久化到SQLite，收费价格未配置时关闭请求。CLI统一显式配置，真实时间步进，不模拟未来行情。

## 模块/接口/数据流影响

config→Pipeline分钟事实→代码退出或双模型意图→Guard/OMS；DeepSeek请求→每日持久化预算→实际HTTP→费用结算；runner/status/serve/recover同配置。使用现有SQLite事务、coalesce与审计存储；不增加进程或部署服务。

## 数据所有权与代码范围

Ledger继续唯一写账户；Guard审批写授权，模型预算独立写预算状态；pipeline负责生命周期和决策记录。范围详见关联Task。

## 非目标

不修改JEV官方适配，不接实盘，不以离线回归证明行情/API可用。

## 测试范围

分钟新鲜度、代码退出、恢复计数、模型每日/重试/费用门禁、CLI一致性及隔离评测。

## 最终实施结果与验证

非JEV分钟运行、安全退出、持久计数/DeepSeek保守费用门禁、CLI配置与隔离历史评测已实现，197项测试通过。证据：[复验报告](../research/20261008-non-jev-fix-verification.md)。JEV协议/费用和盘中真实能力未验收，ARCH-002仍为完整双模型规划，不标全量implemented。
