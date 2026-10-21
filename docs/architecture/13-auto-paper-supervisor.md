---
id: ARCH-013
type: architecture-change
title: 容器自动盘中模拟监督
status: implemented
created: 2026-10-13
updated: 2026-10-13
related_tasks: [TASK-20261013-03]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 容器自动盘中模拟监督

## 背景、目标与非目标
原单次paper在午休拒绝后需要手动重启；新增长期自动服务按上海交易日历启动既有有界paper。仍为模拟交易，账户/模型/风控/预算门禁不变。

## 方案与边界
单容器supervisor顺序管理serve或paper子进程，互斥账户writer。休市/午休及退避时运行workbench；盘中停止workbench再运行paper，所以盘中工作台暂不可访问。paper单次不超过3600秒/720轮且截止当前连续时段结束；结束后按结果退避并自动继续。未知worker未排空等异常阻止再次paper，保留人工核对状态。自动运行状态写runtime/auto-paper-state.json，交易证据仍由PaperRun生成。

## 部署与安全
compose新增auto profile/service，替代原workbench使用同一8321端口，不能并行启用；restart unless-stopped，停止宽限150秒。继承既有宿主无认证代理到当前容器环境，不持久写.env/代理值或打印。原手动入口检测auto容器，拒绝并发。容器内无Docker socket/CLI权限，不创建外部定时任务。

## 实施范围与验证
execution/auto_paper.py、compose.yaml、scripts/run_container.py及tests/test_auto_paper.py；真实午休部署不调用模型，测试验证时段期限/切换/退避/排空/健康。长期盘中及真实交易完整闭环另行验收。当前事实文档按Task同步。

## 最终结果
已实施并部署：宿主447 passed（32.36秒）、最终镜像容器447 passed（18.10秒），各一个既有Starlette警告。11:46实际auto容器healthy，心跳有效、午休等待serve，原workbench停止；中文API/UI正常、现金1000/今日调用和成交0。镜像f2a53f717d78…、restart unless-stopped，代理已继承（仅验证存在，不打印值）。正式启动器午休重建/停止排空及手动paper并发拒绝实际验证。

真实下午切换/全天运行尚未发生，使用模拟时间测试而非改钟或收费验证。原交易完整验收不自动完成；操作、环境、overview/code-map/flows/current-status/索引已同步，具体证据见[TASK-20261013-03](../tasks/TASK-20261013-03-容器自动盘中监控.md)。
