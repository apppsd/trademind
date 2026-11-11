---
id: ARCH-029
type: architecture-change
title: 模型调用路径冷却与纸面观察候选分离
status: implemented
created: 2026-10-30
updated: 2026-10-30
related_tasks: [TASK-20261030-02, TASK-20261030-03]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-029 模型调用路径冷却与纸面观察候选分离

## 状态、背景与目标

工程已实现（仅离线交付，未部署）。ARCH-006 的账户级300秒窗口原在计划与条件判定前写入，纯WAIT也占窗口。采用用户选定 Design A：保留间隔与共享预算，只有进入实际模型调用路径才推进窗口。另为 paper-t0-local-1k 复用已有可达性诊断，将观察集合与新仓模型候选分开。

## 非目标

不更改资金/风险参数、模型、合法手数、Guard/OMS、程序退出、V1或股票T+1语义；不部署、不连接150、不调用付费API，不承诺成交。

## 方案与核心决策

- 快速链先检查持久化账户窗口与预算；planner / decider 将可选 before_model_call 回调传给 provider 的 admit_request。provider 在实际预算预留处调用回调：BEGIN IMMEDIATE 内复检窗口、成功预留预算后写 fast-eval，一起提交后发送 HTTP。一个评估可包含flash与JEV，共用一次窗口预约。
- 缓存且仅WAIT、刷新节流/失败退避及provider准入前的角色额度/费率/费用拒绝不预约、不消耗该次预算；旧窗口和meta不变。成功准入后失败仍保留窗口；崩溃发生在提交与HTTP之间也保守保留，不以重启释放。准入预约不是费用结算证明，实际HTTP attempts/usage仍由provider审计与预算记录确认。已准入flash后JEV拒绝，不撤销flash窗口。
- 事务在网络请求前提交，不跨网络持锁；并发赢家落盘后其余连接复检拒绝。沿用旧key和last_run_at，兼容旧时间戳。股票allow_t_plus_one保留旧的前置窗口写入。
- runner现有持仓优先与每轮轮转顺序保留。纯WAIT不再抢占；无缓存需要flash时仍可占窗口。多个收费候选仍受全账户300秒限制，不保证每个短信号覆盖，也不新增公平调度器。
- 预算预约、失败/重试/探针计数不变：每日最多100请求、5 CNY，flash/修复共30、JEV60、探针10；费用不足先拒绝。一个窗口可包含至多一次策略生成（provider内允许一次格式修复）与一次JEV；窗口数不能替代真实请求上界。
- 纸面候选部分已由Task30-03串行实施，使用 evaluation/tradability.py 唯一数学来源，逐tick重算，无永久黑名单。已有仓位退出保持原路。

## 模块、接口与数据流影响

fast_pipeline负责窗口预约；StrategyPlanner.get与FastDecider.decide增加默认None回调，直接调用兼容。存储Schema与数据所有权不变，无新增进程/部署配置。Pipeline以mode/account/stock_chain限定paper_new_entry_filter开关；ContextBuilder只为指定账户携带原始可见估值价格时点，paper_entry_report适配至既有诊断，无重复数学实现。观察原因放入intent.reason及diagnostics.tradability。

## 代码与测试范围

src/trademind/agents/{fast_pipeline,strategy_planner,fast_decider}.py；evaluation/tradability.py；快速链、可达性及股票兼容定向测试。离线替身只验接口接线，不能证明真实JEV会BUY。

## 实施中的设计变化

采用调用前持久化预约，避免调用后写入造成并发超放；拒绝将网络包在数据库事务中，以保留独立程序退出能力。

## 最终实施结果与验证

Task30-02先95项通过，再实施Task30-03；最终六套件148项通过，原CLI对照复现1000档1可达/2观察与假设5000档3可达。两个Task均工程completed。真实150加载、paper与费用对账未验，Issue30-01保持open。

## 当前事实同步

需求契约已补充引用本记录；system-overview、flows、code-map、operations、current-status及两索引已同步。setup记录隔离测试解释器（旧venv失效）；README/roadmap不适用，无使用或阶段变化。

## 关联内容

[ARCH-006](06-jev-fast-decision.md)；[Task索引](../tasks/index.md)；[Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md)。仅细化其中冷却语义及纸面候选入口，其他契约保留。
