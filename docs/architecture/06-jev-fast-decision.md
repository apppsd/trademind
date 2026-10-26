---
id: ARCH-006
type: architecture-change
title: DeepSeek低频策略与JEV快速决策
status: implementing
created: 2026-10-09
updated: 2026-10-26
related_tasks: [TASK-20261009-01, TASK-20261009-02, TASK-20261009-03, TASK-20261009-04, TASK-20261009-05, TASK-20261009-06, TASK-20261009-07, TASK-20261009-08, TASK-20261009-09]
supersedes: [ARCH-002]
superseded_by: []
recorded_retrospectively: false
---

# ARCH-006 DeepSeek 低频策略与 JEV 快速决策

## 2026-10-26 独立复验与评估契约补足

[Task26-01](../tasks/TASK-20261026-01-独立复验并补足审查批次.md)：strategy-5退出契约独立Mock闭环通过，Issue25-01工程resolved。新增离线诊断/压力工具的初版边界缺口已补足：tradability缺估值/挂单承诺时不作资金可达判断；cost_stress输入v2强制档案as_of、bar.available_at、资金限额和整手/tick规则，按提交与可见成交事件冻结/入账，共享bar参与率，并区分现金流、未平仓权益、模型估值/unknown。历史v1不会无依据地自动补造可见时点。此变化仅在评估域，不改变生产Guard/OMS/PaperBroker/模型预算或运行配置；方法/样本见[修订报告](../research/20261025-cost-stress-verification.md)。真实盘中、闭环与收益仍未验，本ARCH保持implementing，下方各Checkpoint为历史。

## 状态

implementing，02契约及04～07离线工程已完成；03/08/09仍有真实验证或档案/校准缺口。替代 ARCH-002 的后续目标；现有运行链仍是 ARCH-002 工程实现，作为对照保留。ARCH-003/004 风控运行与行情池、ARCH-005 观察能力继续有效。

## 背景与目标

当前一次有效候选通常串行 flash 候选、JEV 审核、flash 汇总；JEV state 只有模型摘要，不能完整读取行情/账户。目标是复用低频策略，把即时动作交给读取真实事实的 JEV，减少重复分析，并验证延迟、费用及交易表现。详情唯一维护在 [接口需求](../requirements-jev-fast-decision.md)。

## 非目标

不新增实盘、杠杆、做空、加密交易和全市场订单；不承诺收益或毫秒级行情，不改变现有资金上限，不取消风险退出。

## 变更前

src/trademind/agents/pipeline.py：flash Trend/Risk/Critic 候选 → JEV 对 entry_suitable/adverse_move/evidence_sufficient 审核 → flash 最终意图 → Guard/OMS。WAIT/HOLD 可短路；确定性退出独立于模型。真实官方 JEV、行情与费用仍未验收。

## 方案与数据流影响

可见行情/逐证券资格 → 程序风险退出与轻量门禁 → 有效 StrategyPlan（必要时 flash 刷新） → 原始 FastDecisionState + 计划 → JEV 原子题 → 程序组合/规范四动作 → 数量/提交前 Guard → 模拟 OMS/账本 → 审计及对照报告。

JEV 不确定：WAIT + 有界刷新请求，下轮再评估；不是固定每单 flash 汇总。所有步骤有 run/trace/plan/decision 关联。

## 核心设计决策与接口影响

2026-10-09 TASK-20261009-10 补充：新链 CLI 使用单个模型工作线程，主线程每5秒运行一轮，仍独占 Guard/OMS。工作线程只有模型/缓存/预算权限；完成结果重新检查账户版本、最短有效期与当前计划条件，过期丢弃。市场数据读取仍同步，故不是实时风控时延保证。直接 Pipeline 单次调用保留同步兼容。

成交、期限和交易记忆共享 Ledger.atomic；失败同时回滚数据库与内存，重启可从当前持仓周期的首次买入成交及意图恢复缺失期限。意图有效期不得晚于 JEV Outcome。已知 flash/JEV usage 结算，未知或失败保留待确认费用；总费用门禁包含两者。回放按新旧 profile 与记录的 t0/risk/paper 配置运行，使用响应完成时间、有效期和持仓期限；缺配置/计划等证据仍为 incomplete。

计划、即时事实与动作三个版本化契约，缺失/过期拒绝新风险；模型意图无订单权限。程序控制数据截止、题目组合、合法动作与大小。原子题不能彼此依赖；概率和 confidence 不等同市场胜率。保留旧 profile 并新增显式新 profile，具体契约按 TASK-02 冻结。

## 模块影响与代码范围（拟定，认领前核实）

- src/trademind/agents/strategy_planner.py：新增低频 flash 计划；fast_decider.py：新增 JEV 即时动作。
- src/trademind/agents/pipeline.py：显式 profile 分派，保留旧链。
- src/trademind/domain/strategy.py：新增契约；src/trademind/context/fast_state.py：原始事实构建；已有 context 模块按 code-map 实际布局调整。
- src/trademind/providers/：官方 JEV 请求/答案/usage 适配，实际文件认领时核实。
- src/trademind/config.py、scheduler/、storage/、cli.py：缓存配置、预算、持久化、调度、入口；实际目录以现有代码为准。
- Guard/OMS/paper：复用现有合法执行边界，按 TASK-07 验证。

上述新增路径是提案，TASK-02 先按现有布局冻结文件所有权，不进行包布局重构。

## 数据所有权影响

原始事实归数据/账户模块；策略计划归 planner；JEV 响应归决策审计；订单/余额仍归 OMS/账本。计划和决策新增持久化需 schema_version、证据引用与非破坏迁移；账户版本变化必须提交前再校验。隔离两组实验账户，配置/Prompt/模型/规则指纹固定；不落认证头和凭据。

## 部署 / 运行影响

保留单进程及当前 CLI。新 profile 显式启用，未完成真实门禁前不能作为默认链。计划缓存按交易日恢复；调用与费用共享现有持久化预算保护。未增加后台任务或自动五日运行。

## 测试范围

契约/时点/过期/账户变化；官方 schema/缺题/非法概率/未知费用；计划缓存与重启；预算竞争/修复重试/限流；WAIT 不冻结、SELL 不开空；确定性退出在模型故障下仍经 Guard/OMS；旧 V1/ETF 回归；同数据对照及真实有界盘中验收。

## 实施中的设计变化

未实施。调度默认数值、题目及阈值待 TASK-02/03 冻结，变化更新本记录和需求，不把提案冒充当前配置。

## 最终实施结果与验证

实现范围与测试证据见 [20261009 验证](../research/20261009-fast-decision-verification.md)，266项通过。真实模型/行情/费用与真实对照校准未通过，不能标implemented。

## 当前事实同步

system-overview、code-map、flows、setup 已同步新显式profile的工程能力，旧默认链保留。落地时按任务契约逐项更新，全部关键模块与真实验收有证据才可标 implemented。

## 关联内容

[Tasks](../tasks/index.md)；[新增差距 Issue](../issues/ISSUE-20261009-01-JEV决策输入与职责差距.md)；[研究依据](../research/20261009-jev-decision-design.md)；[旧 ARCH-002](02-t0-etf-shadow.md)。

TASK-02 已冻结契约及文件范围，03/04 开始实施；真实门禁未通过，状态 implementing，不变运行默认。

实施设计变化：依赖分工程可用和真实验证；协议离线通过可推进05～08，真实模型/费用/行情门禁仍阻断09真实通过。无需读取凭据或付费请求来验证协议负例。新计划和调度复用 coalesce 存储，无 schema 破坏迁移。

最终已实施模块：domain/strategy.py，agents/strategy_planner.py、fast_decider.py、fast_pipeline.py，context/fast_state.py；修改models/jev.py、daily_budget.py、deepseek.py、provider_factory.py、domain/enums.py、config.py、pipeline.py、runner.py、context/builder.py。评价工具evaluation/fast_comparison.py，预检scripts/check_fast_startup.py。持久化复用coalesce_state，无新增表迁移；不改变部署进程。首次BUY成交保存持有deadline，完全清仓后清除；程序风险退出优先。

## 2026-10-10协议费用收尾

Task03 completed，官方三题型认证/usage和预算负例齐备；运行报告/采集边界由ARCH-009记录。原真实同数据校准与完整闭环尚未验收，本记录保持implementing。证据见[收尾报告](../research/20261010-engineering-completion.md)。

## 2026-10-11 提示契约实施Checkpoint

Task-20261011-01完成strategy-2/fast-2：从实际StrategyPlan派生草稿Schema，绑定条件字段/单位/AND-OR、费率和硬约束，程序独占身份/时间/版本并拒绝非法草稿。缓存版本失效仍按现有冷却；JEV四题独立，正常critic/可选L1不直接否决，BUY入场题不约束合法SELL。provider per-call审计版本不修改共享默认或官方HTTP协议。模块/数据所有权/资金/风控边界未变，同次实施更新本记录，状态仍implementing。证据与未验条件见../research/20261011-prompt-verification.md。

## 2026-10-12 中文说明与日报关联Checkpoint

TASK-20261012-04 / [ARCH-012](12-daily-trading-reports.md)已实施strategy-4中文说明：planner四字段拒绝纯英文，DeepSeek请求追加中文要求，预算上界/审计使用最终指令。原strategy-3注册及哈希不重写，版本失效保留冷却；JEV四题/资金/执行权限未变。有效策略新增历史表由ARCH-012记录，原快速链真实闭环仍未通过，本记录继续implementing。宿主/容器430测试及中文页面实证见[验证](../research/20261012-daily-report-verification.md)。

## 2026-10-25 退出动作契约实施Checkpoint

[TASK-20261025-03](../tasks/TASK-20261025-03-修复可开仓计划的退出动作契约.md) / [ISSUE-20261025-01](../issues/ISSUE-20261025-01-买入计划退出条件未关联SELL动作.md)：可开仓计划契约收紧——允许BUY的StrategyPlan必须同时允许SELL且有非空可计算exit_conditions；任何允许SELL的计划必须有exit_conditions。落地为三条边界：草稿边界（strategy_planner.get 与离线 prompt_eval 共用 domain/strategy.py::exit_contract_violation，违规走既有 invalid strategy contract 拒绝/分类/退避）；缓存边界（cached() 应用子集 open_without_close_violation，旧非法缓存按未命中拒绝并在健康状态记录 invalid_cached_plan，不改写历史存储行）；pydantic 模型未改（历史档案保持可解析）。策略提示升级 strategy-5（新增契约文本与开仓/退出配对示例），版本失效使全部旧缓存按规则处理。fast_state 的 SELL 资格门禁未变；程序 time_stop/stop_loss/session_close 仍独立经 Guard/OMS。测试：契约4+提示扩展+快速链3（定向128 passed）；批次收尾全量702项=701 passed+1项既有午夜时间依赖测试失败（ISSUE-20261026-01，与本修复无关）；Issue25-01 保持open待独立复验，真实闭环归09-09。本记录保持implementing。
