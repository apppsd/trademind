---
id: ARCH-012
type: architecture-change
title: 每日交易报告与策略历史
status: implemented
created: 2026-10-12
updated: 2026-10-12
related_tasks: [TASK-20261012-04]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 每日交易报告与策略历史

## 背景、目标与非目标
运行报告按批次且策略缓存覆盖历史；新增按上海日的资金/成交/费用和策略文档。只读账本，不发模型/行情请求，不修改订单。原交易验收不变。

## 方案与边界
新增reports.daily从只读SQLite事务快照读取事件/成交/模型预算/记录及策略历史；输出每账户每日期JSON、Markdown、HTML。工作台lifespan周期线程生成今日和昨日（覆盖收盘/跨日费用更新），paper.finish后生成；CLI可补历史。没有系统外常驻服务，进程停止时不自动执行。原单writer仍负责账户，报告线程只写报告文件。

## 数据与接口
新增strategy_plan_history按account_id+plan_id存有效脱敏计划，production Schema保持原计划契约；既有账户表不重写。历史记录只从新版接入后开始，不补造过去计划。
报告财务口径：事件现金、成本/成交费用、入出金；无新鲜持仓估值不声称已确认总盈亏。模型日预算及未知预留分列，扣模型费用后的经济结果不改账本现金。多账户无法可靠归属的旧模型调用明确标注；本期主CNY SQLite账户，不把独立港股/期货内存账本纳入。

## 部署、风险与验证
仅现有Python/SQLite/FastAPI，无新依赖；Schema初始化IF NOT EXISTS追加表；容器需重建才能自动生成。金额Decimal；时间归上海；HTML转义与文本脱敏；一致性快照与每文件原子输出，不是三文件事务。
覆盖平仓/持仓/资金流/跨日/未知费用/历史缺失/自动生成失败不影响交易。完成后同步README、setup、operations、overview、flows、code-map、current-status及索引；实际结果/命令进Research。

## 中文输出与证据
报告展示由reports/locale.py翻译程序动作/状态/原因/条件；旧英文策略通过运行时SHA256人工译文展示，原始JSON证据不重写。新strategy-4约定四个说明字段中文，planner拒绝纯英文；DeepSeek真实请求统一追加中文要求，审计/预算使用最终提示。JEV枚举及Schema键保持协议格式。旧strategy-3注册内容及哈希保留，缓存版本更新仍保留冷却。

## 最终结果
已实施：宿主及隔离容器各430测试通过；新版镜像340babf97299…部署，healthy/日报HTTP200，今日中文及昨日自动归档已验。[完整证据](../research/20261012-daily-report-verification.md)。未新增收费调用，中文真实响应、真实盈利与原完整交易闭环不以离线测试替代。
