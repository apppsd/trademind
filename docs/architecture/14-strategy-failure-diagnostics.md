---
id: ARCH-014
type: architecture-change
title: 策略失败诊断与盘中健康反馈
status: planning
created: 2026-10-13
updated: 2026-10-13
related_tasks: [TASK-20261013-04, TASK-20261013-05, TASK-20261013-06]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 策略失败诊断与盘中健康反馈

## 背景与目标
当前适配器把空completion一律当refused，auto监督器盘中只展示进程正常，真实策略11失败/1成功未反映到运行状态。规划安全摘要、明确失败分类、模型级有界退避和盘中健康反馈，保持确定性风险执行及单writer。

## 变更前与方案
现有ModelCall缺少finish_reason等诊断摘要；supervisor只在子进程结束后读最终报告。拟由provider产生安全可选摘要→现有审计持久化→paper发布或只读构建当前轮健康摘要→监督/API/日报展示；失败分类驱动仅新模型请求的退避，不中断Guard/OMS风险退出。

## 非目标与决策边界
不保存凭据/原始推理/完整响应，不变模型/费用限额，不将推理文本作交易策略；不调整日历/买入门槛或保证成交。盘中UI与paper互斥架构保持。Schema字段、历史兼容、退避阈值与重置条件由Task04/05认领后冻结，现在不能当已确定实现。预算计数归原Task12-05，不另立重复事务所有权。

## 模块、接口、数据与部署影响
预计涉及provider/ModelCall/审计白名单、失败评测分类、paper/planner/auto监督状态/API/报告；可选审计字段和状态摘要必须兼容历史。账户写权仍只有现有paper，监督器摘要只读，禁止第二writer或新增收费后台检查。实施代码更新需镜像验证及休市优先单writer重建；原代理/挂载保留。

## 验证与状态
Task04离线故障证据与合法中文响应；Task05失败退避/恢复/有仓风险与跨日；Task06预算内真实多个刷新时点及费用/资金核对。新参数需验证探针/请求上界，不能默认扩大1024词元或费用。规划未实施，原447测试不代表本方案通过。

## 当前事实同步
本次只更新计划Task/Issue/索引及current-status规划引用，不把方案写入system-overview/code-map/flows/setup当前事实。实施完成按Task契约同步后才可implemented；真实交易闭环/五日观察另行验收。
