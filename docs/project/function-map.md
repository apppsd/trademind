# 功能地图

这张表只回答两件事：系统现在有哪些功能，每项落在哪段代码。带源码摘录、可点选的三栏页在 [feature-code-map/index.html](feature-code-map/index.html)，规格在 [feature-code-map/map-spec.json](feature-code-map/map-spec.json)。逻辑边界仍以 [系统概览](system-overview.md) 为准，文件职责仍以 [代码地图](code-map.md) 为准，运行版本与未验收项仍以 [当前状态](current-status.md) 为准。这里不重复那些文档的证据和历史。

对照日期：2026-11-11。150 正在跑的是 `62b5be1` 加它自己的未提交文件。本机工作树比那个版本多出一批未部署改动。下表的「150 正在用」指这条主链已经在 150 上运行；「本机未加载」指代码在本机，不能当成 150 已生效。

## 现在在跑的主链

三只 ETF，配置 profile 为 `jev_fast_decision`。flash 低频写策略计划，JEV 读原始行情、账户和有效计划后给出买、卖、持有或等待。程序计算数量，Guard 可以否决，OMS 和纸面撮合器负责模拟成交。模型没有订单权。没有实盘通道。

| 顺序 | 功能 | 代码 |
|---|---|---|
| 1 | 监督器按上海日历启动有界 paper；等待、休市、失败退避时只提供只读服务 | `scripts/run_local.py`、`src/trademind/execution/auto_paper.py` |
| 2 | 一个库加一个账户只有一个写者；第二写者直接失败 | `src/trademind/execution/writer_lock.py`、`src/trademind/storage/sqlite_url.py` |
| 3 | 交易日历：覆盖外拒绝，覆盖内非交易日按休市 | `src/trademind/market/calendar.py`、`src/trademind/market/calendar_update.py` |
| 4 | ETF 1 分钟行情、质量接受或拒绝、在途请求上限 | `src/trademind/market/adapters/sina_etf.py`、`src/trademind/market/router.py`、`src/trademind/market/quality.py` |
| 5 | 只使用当时可见的行情、特征和账户快照 | `src/trademind/context/builder.py`、`src/trademind/context/features.py`、`src/trademind/context/fast_state.py` |
| 6 | flash 生成并缓存策略计划，受每日费用和次数限制 | `src/trademind/agents/strategy_planner.py`、`src/trademind/models/deepseek.py`、`src/trademind/models/protocols.py`、`src/trademind/models/daily_budget.py` |
| 7 | JEV 把计划收成四个动作之一 | `src/trademind/agents/fast_decider.py`、`src/trademind/agents/fast_pipeline.py`、`src/trademind/models/jev.py` |
| 8 | 编排：程序风险退出优先，然后才是模型意图、数量和期限 | `src/trademind/pipeline.py`（`_run_decision` 编排；`_open_decision_snapshot`、`_choose_decision_intent`、`_authorize_decision`）、`src/trademind/runner.py`（`t0-paper`） |
| 9 | 硬风控、模拟订单、纸面撮合、账本 | `src/trademind/guard/guard.py`（`check` 编排；`_screen_order`、`_screen_risk`、`_approve_order`）、`src/trademind/execution/oms.py`、`src/trademind/execution/paper.py`、`src/trademind/portfolio/ledger.py` |
| 10 | 审计、盘中健康、中文日报 | `src/trademind/storage/db.py`、`src/trademind/execution/paper_run.py`、`src/trademind/execution/intraday_health.py`、`src/trademind/reports/daily.py`、`src/trademind/reports/daily_sections.py`、`src/trademind/reports/locale.py` |
| 11 | 只读工作台。读账户和报告，不初始化交易、不写账本 | `src/trademind/api/workbench_routes.py`、`src/trademind/api/read_models.py`、`src/trademind/api/observer_app.py`、`frontend/` |

入口命令在 `src/trademind/runner.py`：`t0-paper` 跑主链，`t0-data` 只查行情，`t0-fast-check` 做无凭据预检，`serve` / `observer` 提供页面，`daily-report` 只读生成日报。150 的常驻进程是 `scripts/run_local.py` 包着同一套监督器。

## 本机已写、150 还没加载

这些改动在本机工作树里，不改变上表的职责划分，但不能写成 150 已经生效。

| 功能 | 代码 | 边界 |
|---|---|---|
| 纯等待不占用全局模型冷却窗 | `src/trademind/models/daily_budget.py`、`src/trademind/agents/strategy_planner.py`、`src/trademind/agents/fast_decider.py` | [ARCH-029](../architecture/29-model-charge-window-paper-candidates.md) |
| 纸面新仓先检查最小手买不买得起；买不起只观察 | `src/trademind/evaluation/tradability.py`、`src/trademind/pipeline.py` | 只作用于 `paper-t0-local-1k` 的新仓。已有持仓和程序风险退出仍走原路 |
| 下单前按当前时钟复核参考价；价格按 tick 取整 | `src/trademind/pipeline.py`、`src/trademind/execution/paper.py`、`src/trademind/execution/price_quant.py` | [TASK-20261108-002](../tasks/TASK-20261108-002-撮合与执行旁路P0快修.md)、[TASK-20261108-003](../tasks/TASK-20261108-003-行情有效性与估值质量快修.md) |
| 持仓估值必须质量合格、可见、新鲜 | `src/trademind/context/builder.py` | 不合格不进入权益，Guard 拒绝新风险 |

## 写了但没有接上当前运行

| 功能 | 代码 | 现在的事实 |
|---|---|---|
| 旧双模型链：分析、JEV 审核、flash 再汇总 | `src/trademind/agents/pipeline.py`、`src/trademind/agents/research.py`、`src/trademind/agents/trader.py`、`src/trademind/agents/risk.py` | 保留作对照。150 当前 profile 不是这条链 |
| 结果何时成熟、反思何时可被以后看见 | `src/trademind/memory/memory.py` | 计算代码在。没有生产调用方，不会自动进入下一轮决策 |
| A 股名单、可交易性、股票快速链 | `src/trademind/market/stock_discovery.py`、`src/trademind/market/stock_selector.py`、`src/trademind/market/stock_profiles.py`、`src/trademind/agents/stock_fast_pipeline.py` | `t0.stock_chain` 默认关。选择器还没有生产调用点 |
| 可转债、LOF、港股、期货的隔离模拟 | `src/trademind/market/multi_asset.py`、`src/trademind/market/rules.py`、`src/trademind/market/access.py`、`src/trademind/execution/multi_asset_paper.py` | 离线实验。开空和平空被拒绝。`execute_order` 默认拒绝，主链不走这里 |
| 左右双窗驾驶舱 | `frontend/src/pages/CockpitPage.vue`、`frontend/src/layouts/DualOpsLayout.vue` | 本机样例。左窗默认样例数据。未部署到 150 |
| 提示评测、回放、成本压力、可达性诊断 | `src/trademind/evaluation/prompt_eval.py`、`src/trademind/evaluation/replay.py`、`src/trademind/evaluation/cost_stress.py`、`src/trademind/evaluation/tradability.py` | 离线只读。不直接下单 |

## 值得改，以及先不要改

顺序是建议，不是已开工的任务。不接实盘，不更换模型，不放松 Guard，不在盘中重启 150。

1. **先看清为什么没成交。** 逐条条件已经在 `context/fast_state.py` 的 `action_gate`、健康读取和日报里。运行中心以前按 `blockers` / `budget_meta` 去读进度文件，这两个键并不存在，所以页面看起来像没有原因。只读展示已改接到 `read_intraday_health`。相关开放问题仍是 [Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md) 和 [Issue27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)。这不改变成交。
2. **记忆不要假装已经闭环。** 若要接，先在 `memory/memory.py` 做只读成熟标记，不要让反思自动改变下一笔订单。
3. **新功能只进快速链。** 运行入口是 `pipeline.py` 里 `jev_fast_decision` 这一支。不要把改动做进 `agents/pipeline.py`，也不要打开 `t0.stock_chain`。
4. **几处过长的函数已经按原样拆开。** 健康读取在 `execution/intraday_health.py`，`paper_run.py` 仍用原来的名字转出。日报 `generate_report` 只负责编排，取数在 `reports/daily_sections.py`，返回字段不变。`pipeline._run_decision` 分成开快照、选意图、授权三段；快速链若换成模型轮快照，授权仍用换过的那份。`guard.check` 分成订单初筛、风险测算、批准三步，否决顺序不变。`render_markdown` 没有拆。
5. **驾驶舱和期货开空先停住。** 驾驶舱还差视觉验收和真实只读接线，见 [Task30-05](../tasks/TASK-20261030-05-流水线交互视觉原型与性能门禁.md)、[Task30-06](../tasks/TASK-20261030-06-跨项目只读适配与验收方案.md)。期货开空会引入主链没有的持仓语义。

行情质量本身不是再加一个功能能解决的。2026-11-11 早盘 513100.SH 有一段时间没有可见 1 分钟线。那是数据源现象，记录在当前状态里。收盘后可以继续只读观察，不在盘中更换付费源。
