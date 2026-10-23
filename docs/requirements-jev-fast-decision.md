# JEV 快速决策需求与接口契约

更新：2026-10-09。用户已确认方案；02契约及04～07离线工程已完成，03真实认证、08真实对照校准和09启动闭环仍未完成。架构见 [ARCH-006](architecture/06-jev-fast-decision.md)，实施顺序见 [Tasks](tasks/index.md)。现有资金、风控、费用、行情及撮合要求唯一维护在 [T0 需求](requirements-t0-shadow.md)，本文件只规定新增角色与接口。

## 目标与范围

先让 deepseek-flash 低频生成策略计划，外部 TypeSafe JEV 根据该计划及当前真实行情、账户事实，快速输出 BUY/SELL/HOLD/WAIT 意图，再由程序计算数量、硬风控和模拟执行。JEV 不再仅审核 flash 的候选。正常决策后不固定再调用 flash；JEV 无法确定时可申请刷新策略。

第一轮使用已核验 T0 ETF 白名单；独立 paper 账户资金以 [T0 需求](requirements-t0-shadow.md)冻结表为准（历史 500 CNY；2026-10-22 起新增本地 1000 CNY 实验 `paper-t0-local-1k`/独立库，限额不放宽）。可转债、LOF、港股和期货保留多市场观察及已有独立适配队列，不接入旧 ETF 订单引擎。不接实盘、不做空、不加杠杆。免费分钟数据限制了反应粒度，“快速”指决策链减少串行大模型调用，实际延迟需测量，不能称高频交易。

## 三方职责

| 组件 | 输出 | 权限边界 |
|---|---|---|
| deepseek-flash | 版本化 StrategyPlan：趋势、风险、反证、适用条件、退出条件 | 不直接生成订单，不更改硬风控 |
| 外部 JEV | 基于原始事实与有效计划的窄题目答案，组合为四动作意图 | 不决定数量，不更改规则，不授权订单 |
| 程序 | 数据计算、条件核验、动作规范、数量、预算、Guard、OMS、审计 | 可硬性否决；不得将 WAIT 改成 BUY |

Trend/Risk/Critic 保留为 flash 的三个分析维度，允许一次结构化分析；不能宣称三个独立 Agent。确定性止损、日损暂停和尾盘退出沿用当前实现，不等待模型刷新。

## StrategyPlan

必须有 plan_id、schema_version、instrument_id、market/currency、created_at、data_cutoff、available_at、valid_until、model、prompt_version、输入证据引用、策略假设、适用市场状态、允许动作、入场/退出条件、最大持有期限、失效条件、风险及反证。

条件使用程序能校验的字段与运算符，禁止执行模型生成的代码或自然语言表达式。计划应允许“本轮无可交易策略”。缺字段、标的不一致、未来数据、过期、规则版本不匹配时无效。计划只限定意图，不授予订单权限；账户与资金在每次决策重新读取。

缓存按证券、交易日、配置/Prompt/规则版本分区；重启只恢复仍有效且有证据引用的计划，不沿用上一交易日计划。落盘不能保存凭据或请求认证头。

## FastDecisionState：JEV 必须看到的事实

| 事实组 | 必需内容 |
|---|---|
| 标识/时点 | instrument、market、currency、trace_id、observed_at、source_time、fetched_at、available_at、时区、交易阶段 |
| 行情 | 当前可见价格、已收盘 1 分钟线、由可见数据聚合的 5/15 分钟特征、单位、有效性、延迟；盘口/NAV/IOPV 可缺失但必须注明 |
| 账户 | account_id、账户版本、现金、冻结、仓位、可卖数量、挂单、成本、当日损失及剩余额度 |
| 条件 | 逐证券规则版本、T0资格、合法动作集合、交易费用与滑点假设、计划版本、有效期、明确预测/持有期限 |
| 质量 | 缺失项、拒绝原因、来源、证据引用；缺失不伪造为 0 |

原始行情与程序计算字段是事实区；flash 分析是有来源的策略区，不能替代原始事实。新闻等外部文本按不可信资料处理，不接受其指令。订单提交前再次校验账户版本、时点、计划有效性与风控，变化时重算或拒绝，不能执行陈旧意图。

## JEV 题目与 DecisionOutcome

官方请求字段与模型 ID 必须通过 TASK-03 对照官方文档及真实探针冻结，不能沿用未经验证的旧 prompt/options 结构。

建议原子题目：Choice 在当前合法动作集合中选择动作；Noul 判断证据是否足够、计划是否仍适用、入场必要条件是否满足；必要时用 Choice/Score 表示独立风险等级。具体字段、题目、rubric 和阈值由 TASK-02/03 冻结。各题直接阅读同一 state，不能假设一个答案会自动成为另一题的输入。

输出需保存 decision_id、原始动作、规范动作、各题语义/答案/概率分布/置信字段、plan_id、账户版本、数据截止、模型/Prompt版本、耗时、usage/cost、reason 与证据引用。检查题目 ID 完整且唯一、类型、有限数值、概率范围和官方约定的归一性。缺少必需答案、非法动作、低可信、超时、协议错误或费用不可核算时不新增风险，输出 WAIT + 明确原因。不得伪造概率或默认通过。

BUY 增持；SELL 只减已有可卖仓位；HOLD 维持已有仓位；空仓 HOLD 规范为 WAIT，空仓 SELL 拒绝。WAIT/HOLD 不冻结资金。JEV probability/confidence 是题目输出分布信息，不是交易胜率；Noul 的 yes 概率也须按题目语义解释。旧 0.70 参数不能自动套用于所有新题，EV 在没有期限与样本校准前保持 unavailable。

## 调度与预算

行情仍按既有分钟频率观察；不是每次观察都调用 JEV。优先程序退出，其次持仓复核，最后通过资格/质量/冷却检查的新仓候选。每轮控制模型候选数量，累计限制包含所有标的、两个模型、修复、重试和探针。

TASK-02 待冻结的首轮建议：flash 策略常规刷新 15 分钟，计划有效期最多 20 分钟且受收盘/日切限制；不确定触发刷新同证券最短间隔 5 分钟；每轮最多 1 个模型候选、1 次策略刷新。以上为工程提案，未经实现与预算验收不算配置生效。JEV 低可信先 WAIT，不通过无上限刷新重试强行得到买入。

三只证券每分钟都调用 JEV，240 分钟可能达到 720 次，超过现有每日合计 100 次限制；因此必须预约持久化调用及费用额度后才请求，耗尽即停止模型新风险，保留程序合法退出。15 分钟全池刷新本身也可能消耗 48 次，必须在总预算中与 JEV 分配。低频、快路径与预算不是盈利证明。

费用未知不得写 0；价格、币种、汇率与 usage 解析未验证时禁止无人值守收费运行。API 延迟实测 p50/p95，不借用公开交易机器人区块时间作为模型速度。

## 模式、对照与验收

shadow 严格只读；需要订单和盈亏时运行独立 paper。新 profile 已固定为 jev_fast_decision，旧 jev_deepseek 保留对照；显式选择，不静默改默认或回退单模型。两组独立账户/数据库/预算，禁止共享余额与订单。

按时间切分调参与验收窗口；使用相同在当时已可见的行情，比较旧链、新链及确定性规则基线。归档响应必须绑定相同可见证据和版本；replay 不调用当前模型补写过去决策，不将今天生成的策略当成过去已知信息。不能因新链没有交易而强行放宽规则。缺真实盘口时报告撮合假设。

报告包括数据覆盖/延迟、计划命中与失效、调用次数、两模型费用、决策耗时 p50/p95、错误/WAIT/拒绝、成交成本、净收益、回撤、未平仓与账本一致性。模型费用单独列出并提供扣除后的净结果；未知费用为 unknown。少数样本或五交易日观察只能验收工程与暴露问题，不能宣称盈利有效。

完成顺序：契约与官方适配 → 计划/原始状态/调度 → Guard/OMS 接线 → 可复现对照 → 有界真实盘中 paper/shadow 验收 → 冻结版本后的五交易日观察。真实 JEV、数据质量及费用门禁缺一不可，离线测试不能替代。

## TASK-02 冻结契约（2026-10-09）

schema_version=1；新 profile=jev_fast_decision。计划有效期 1200 秒、常规刷新 900 秒、不确定刷新最短 300 秒；跨交易日失效。模型评估最短 300 秒（行情仍 60 秒），每个全池轮次最多 1 个标的进入模型，持仓优先；预算不能满足更多持仓频率时降频并报告。

共享硬上限仍 100 请求/日及 5 CNY/日。分配最多 30 次 flash 请求（含最多一次格式修复），60 次 JEV 请求（不自动重试），剩 10 次用于显式探针；总预算优先，价格上界不足时进一步停止。15 分钟只对当轮选中证券刷新，不对三只全池都刷新。冷却/刷新/请求配额全部持久化，失败也占请求额度。费用依 provider 单独配置核验后的 CNY token 价格；未知价格拒绝收费请求。

StrategyPlan 字段冻结：schema_version:int=1；plan_id/instrument_id/currency/market/rule_version/prompt_version/model:str；created_at/data_cutoff/available_at/valid_until:有时区 datetime；evidence_ids:list[str]；trend/risk/critic/reason:str；allowed_actions:list[BUY,SELL,HOLD,WAIT]；entry_conditions/exit_conditions/invalidation_conditions:list[Condition]；holding_horizon_seconds:正整数。Condition={field:str白名单,operator:gt/gte/lt/lte/eq,value:有限Decimal}，只比较程序特征，禁止 eval。计划超过 TTL、日切、模型或版本不匹配不可复用。

FastDecisionState 字段：schema_version、instrument_id、context_id、observed_at、data_cutoff、source_time/fetched_at/available_at、market/currency/rule_version、features:有限数值映射、bars:含完整时间与单位的已可见分钟记录、account:{id,version,cash,reserved_cash,equity,positions,open_orders}、allowed_actions、plan:StrategyPlan、quality/missing、fees/slippage、risk_limits、budget。盘口缺失明确 null，不添加虚假量。

DecisionOutcome 字段：decision_id、raw_action/action、reason、plan_id、context_id、account_version、as_of、valid_until、answers、model、prompt_version、latency_ms、cost/usage、refresh_requested。动作题 action 使用 Choice，criteria 为程序合法集合与计划集合的交集；evidence_sufficient、plan_applicable 使用 Noul，入场需 entry_suitable Noul。所有题同 state 独立求值；动作 confidence 至少 0.75，必需 Noul yes 值至少 0.80，这是待校准的保守实验门槛，不是获利概率。SELL 同样需计划/证据门禁；程序风险退出不依赖此模型门槛。

文件范围已核实：providers/ 实际不存在；官方适配在 src/trademind/models/jev.py，共享预算 models/daily_budget.py；应用入口 pipeline.py，配置 config.py，CLI runner.py，持久化 storage/db.py + schema.sql。新增 domain/strategy.py、agents/strategy_planner.py、agents/fast_decider.py、context/fast_state.py、agents/fast_pipeline.py；Prompt 随 planner/decider 版本固定。存储优先复用现有 coalesce/audit 的版本化 payload，不创建空表或替换包布局。对照在 evaluation/fast_comparison.py。认领 Task 时记录具体路径，逐任务序列推进，无并行 Agent。

## 实施证据

[20261009 验证](research/20261009-fast-decision-verification.md)：266测试通过。JEV输入CNY价格需核验，输出免费可配置0；分布confidence与Noul yes概率独立记录。最大持有期限从首次BUY模拟成交起持久化，退出经Guard/OMS。真实预检未过，不开始五日。ARCH-006 implementing。

条件字段白名单实际固定为 close、ret_1m、ret_5m、ret_15m、vwap、atr_14、volume_ratio，与当前分钟特征一致。JEV新profile要求官方endpoint；固定版本时响应型号必须一致；有效动作期限同时受最新分钟年龄180秒约束。

2026-10-11提示契约已对齐实际解释器：entry全部满足、exit/invalidation任一满足，字段只和有限数值常数比较；比率为小数，ATR/VWAP/close为CNY。模型只返回九字段草稿，程序生成所有身份/时间/证据/版本；strategy-2缓存和fast-2动作审计已接线。JEV entry_suitable只对BUY生效，不因其no阻止合法SELL；可选L1缺失不直接否定分钟事实，正常critic不等价于触发失效条件。真实提示理解与完整交易仍待验证，见[提示报告](research/20261011-prompt-verification.md)。

费用对照只把已核验token价格及真实usage结算记录视为可核算实际金额；request_upper_bound/reservation仅为预留估计，不冒充账单，缺实际费用时净结果保留unknown。
