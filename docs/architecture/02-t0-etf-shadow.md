---
id: ARCH-002
type: architecture-change
title: 外部双模型T0 ETF影子模拟
status: superseded
created: 2026-10-07
updated: 2026-10-09
related_tasks: [TASK-20261007-39, TASK-20261007-40, TASK-20261007-41, TASK-20261007-42, TASK-20261007-43, TASK-20261007-44, TASK-20261007-45, TASK-20261007-46, TASK-20261007-47, TASK-20261007-48, TASK-20261007-49, TASK-20261007-52, TASK-20261007-57, TASK-20261007-58, TASK-20261007-59, TASK-20261008-01, TASK-20261008-02, TASK-20261008-03]
followup_tasks: [TASK-20261007-55, TASK-20261007-56]
supersedes: []
superseded_by: [ARCH-006]
recorded_retrospectively: false
---

# ARCH-002 外部双模型 T0 ETF 影子模拟

## 2026-10-09 替代说明

本记录后续目标已由 [ARCH-006](06-jev-fast-decision.md) 替代，status 为 superseded。下方保留截至 2026-10-08 的原方案和未完成真实验收历史，不再作为最新实施指令；旧链仍为当前代码及实验对照，ARCH-003/004/005 不受此替代影响。新方案尚未实施。

## 历史确认方案：JEV + deepseek-flash最小模拟

用户恢复既定双模型架构，DeepSeek型号固定deepseek-flash，仍planning。当前40→41/42→52/43/45→57→46→58→59。41真实JEV适配/认证必需，52/57已改双模型，旧文件名保留。下方single_call/disabled阶段是此前未实施规划，不覆盖此段。

当前数据流：同一可见分钟Context→flash候选（Trend/Risk/Critic三意见）→外部JEV批量窄题目→flash最终四动作意图→确定性数量/Guard→模拟OMS/账本→基础页面与日志。最小候选可同一次flash明确三个维度，不能称三独立Agent验证；后续可独立角色实验。新风险必须有有效双模型证据，WAIT短路/确定性退出原因单独记录。

不得静默换型号、单模型或JEV stub开仓；保留授权、时点、TTL、费用与隔离。先58可用再59迭代，独立角色/完整监控/五日观察后置。工程链路已实施（203 项离线测试通过），运行接线与全池轮询的独立变化已建 [ARCH-003](03-t0-runtime-safety.md)/[ARCH-004](04-t0-market-pool.md)；完整双模型真实验收未通过，本记录保持 planning。

## 状态、背景与目标

planning（工程已实现，真实双模型验收未通过）。用户决定外部 JEV + DeepSeek，先影子测试。当前 V1 日线股票和自定 JEV HTTP 协议不能直接满足新目标。[需求与参数唯一来源](../requirements-t0-shadow.md)。

## 非目标

无实盘、无新基础模型训练、无盈利保证，不破坏 V1 股票/Replay，不把 JEV 当内部 EV 公式。

## 变更前

实际单进程 Python + SQLite，Provider 隔离，paper/shadow/replay；shadow Guard 直接 VETO，不生成虚拟成交。V1 五分析师/辩论/Trader/风险/组合完整链，股票 profile，日线数据。事实见 [system-overview](../project/system-overview.md)。

## 方案与核心设计决策

复用已有原子账本、授权、OMS、恢复、审计与页面，增加独立实验 profile/config。不新建常驻服务或另造账本。

1. market 增加有来源日期的 ETF 规则白名单、分钟报价采集与质量校验。context 建立分钟时点与聚合特征，regime 为描述性事实，不独自触发买卖。
2. models 增加 TypeSafe 官方 Jev adapter，题目/答案 map 适配既有内部 judgment 契约，保留语义及 missing，不强行把 Score 转成胜率。自定 /v1/judgments 与官方 /v1/systemone 不混用；provider 配置值由 TASK-41 固定并同步。
3. 新 T0 agent profile 使用 Trend/Risk/Critic 候选 → JEV 窄问题评价 → DeepSeek 结构化汇总意图。V1 agent profile 保留，不全局删改旧角色。
4. V2 由代码按冻结的预算计算最大合法多头数量；最终 Guard 原子授权，OMS 重检。AI 无资金或数量执行权，代码无意图生成权。
5. 两种安全边界：模型失败禁止新风险；持仓止损/到时/收盘退出由明确确定性策略生成减仓意图，所有退出仍经过 Guard 和 OMS。JEV 无权阻断必要退出。
6. 在线实验使用 paper + 独立账户/库，不取消 shadow_no_execution。回放只用隔离存档，不读交易凭据、不调用在线模型。
7. 模型日预算与冷却持久化；全部角色、汇总、修复、重试都计入预算。分钟采集不等于分钟必调模型；持仓前置风险检测无需模型。

## 模块影响与代码范围（执行提案，认领时确认）

| 模块 | 候选文件 | 变化 |
|---|---|---|
| 契约/config | domain/instruments.py、intents.py、judgments.py、enums.py、config.py | ETF规则、WAIT、语义、实验预算 |
| models | models/jev.py（拟新增）、provider_factory.py、base.py、runner.py | 官方协议与脱敏探针 |
| market/context | profiles.py、adapters/live.py、base.py、quality.py、builder.py、features.py | 分钟数据、白名单、时点和特征 |
| agents | prompts.py、trader.py、research.py、risk.py、pipeline.py | 三角色 profile 与汇总 |
| guard/execution | guard.py、oms.py、paper.py、ledger.py | 小额数量、减仓通道、逐证券T0、撮合 |
| orchestrator/ops | scheduler.py、runner.py、api/app.py、api/static/index.html | 持续采集、隔离运行、日报与可观察控制 |
| storage/evaluation | schema.sql、db.py、replay.py、calibration.py | 版本化实验记录、恢复及结果报告 |

路径均相对 src/trademind。不要创建空模块，具体文件由各任务所有权契约限定。

## 接口与数据流影响

真实行情 → 时间/质量/市场检查 → 特征与账户快照 → 调度/预算 → DeepSeek 三角色 → JEV 结构化审核 → DeepSeek 最终意图 → 确定性数量/硬风控 → 原子授权 → 模拟OMS → 后续有效行情撮合 → 原子成交/账本 → 日报。

已有持仓并行进行代码风险检查；无模型退出仍沿授权/OMS路径。BUY/SELL/HOLD/WAIT 的含义及回退见需求，未知动作拒绝，不默认 BUY。
Quote/Bar 与决策必须保留 source_time/available_at；intent 绑定账户版本与 TTL。减仓通道不会放松账户一致性或数量合法性。

## 数据所有权

沿用 market 拥有行情、models 拥有调用与题目答案、agents 拥有意图、guard 拥有授权、execution 拥有订单成交、portfolio 拥有账本、evaluation 拥有只读报告。每条新实验记录带 run_id，旧库迁移可回滚且无破坏性清理。秘密仅留 .env，审计不存请求头、Key 或含密钥异常。

## 部署与运行影响

本地单进程延续。实验配置、数据库、账户与 V1 隔离；重启恢复同日调用/损失基准、资金冻结和运行指纹。官方交易日历和交易时段决定测试起止，未知数据延迟不称实时。新命令由 TASK-47 实现并验证后进入 operations，当前不编造可运行命令。

## 测试范围

官方契约及异常；逐证券 T0/T1/交易单位/费用；分钟可见性和休市；WAIT/合法SELL；小资金不足最小单位；预算和超时；模拟成交不偷看未来；事务回滚、重启和去重；多模式账户隔离；真实无凭据泄露探针、盘中观测与报告。各 Task 有验收清单。

## 实施中的设计变化

原方案双模型直接接入；中途经用户指示经历"暂缓JEV单模型先行"阶段（TASK-51/60），随后用户恢复双模型必需（TASK-61），单模型规划不再作为实施规则。44/47/48 的范围已由 57/58/52 等覆盖。工程实施中新增的运行接线与全池轮询变化已分别记录为 [ARCH-003](03-t0-runtime-safety.md)、[ARCH-004](04-t0-market-pool.md)（均 implemented）。变更参数/题目/成交假设必须先版本化，不在观察期间悄悄调优。

## 最终实施结果与验证

工程链路已实现：TASK-20261007-40～59 与 20261008-01～03 全部 completed，离线测试基线 203 项通过；分钟决策、确定性风险退出、持久化预算/费用门禁、独立 paper 账户、全池轮询与 Prompt 对照均已落地。但完整双模型真实验收未通过：JEV 真实认证（ISSUE-20261007-12）、盘中真实行情（ISSUE-20261007-13/20261008-04）、真实 500 账户闭环（ISSUE-20261007-14）仍 open，故本记录保持 planning，待前瞻验收后决定状态。

## 当前事实同步

现在只更新项目入口指向本方案，system-overview 保留旧行为并纠正官方来源表述。落地时由任务逐项同步 system-overview/code-map/flows/setup/operations/README/current-status；roadmap 区分工程和观察。

## 关联内容

- 前序：[ARCH-001](01-ai-trading-system.md)，增量扩展，当前无整份替代关系。
- [Task 索引](../tasks/index.md)；[Issue 索引](../issues/index.md)。
- [官方来源核验](../research/20261007-t0-shadow-verification.md)。
