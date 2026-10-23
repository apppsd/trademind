# 代码地图

自动监控：execution/auto_paper.py（时段期限、互斥子进程、退避/异常锁定、心跳；跨平台探活 `_pid_alive`、Windows msvcrt 互斥锁与停止标志排空/强制+halt 路径）；scripts/run_auto.py（VM启动/停止/状态与代理继承），run_container.py阻止手动paper与auto并发；compose.yaml auto服务。本地无Docker入口：scripts/run_local.py（start/status/stop，脱终端启动、停止标志、强制结束前pid+映像校验）+ config/t0-local.example.toml（本地实验账户/库）；tests/test_auto_paper.py/test_auto_launcher.py/test_local_lifecycle.py覆盖。[ARCH-013](../architecture/13-auto-paper-supervisor.md)、[ARCH-026](../architecture/26-local-windows-runtime.md)。

每日交易报告：reports/daily.py（只读快照、上海日资金重建、成交/盈亏/模型费用、策略/决策、JSON/MD/HTML；read_snapshot自2026-10-14支持window参数——调用/意图/计划按上海日±2天、context 7天回看走迁移2索引，账户事件保持全量重放），reports/locale.py（中文动作/状态/原因/条件及历史译文）；runner daily-report CLI；api/app.py lifespan每分钟生成今日/昨日及报告页面，static首页链接；paper_run.finish自动生成，paper_run.read_intraday_health同按日窗口读取。schema.sql追加strategy_plan_history，strategy_planner写有效脱敏计划并校验中文说明；fast_prompts注册strategy-4，models/deepseek统一中文要求与实际审计指纹，runner探针预算计入最终指令。[证据](../research/20261012-daily-report-verification.md)。

提示评测：evaluation/prompt_eval.py（案例/响应指纹、生产草稿校验、四题语义及独立程序结果统计、同输入比较）；scripts/evaluate_prompt_cases.py（保存响应CLI，零联网）；tests/fixtures/prompt_eval/cases.json（24例/12类，prompt-cases-1）。fast_prompts.strategy_examples是配对示例资源，当前策略版本strategy-4、即时题版本fast-2；strategy-3注册及历史hash保留。旧评测证据见[报告](../research/20261011-prompt-eval-verification.md)，中文变更见本页日报证据。

快速链提示资源：agents/fast_prompts.py（strategy-3/fast-2、派生Schema与四题）；strategy_planner.py绑定输入与草稿校验/缓存版本，fast_decider.py与fast_pipeline.py记录新决策/意图版本；models/jev.py允许每次调用单独传审计版本。验证见[本轮提示报告](../research/20261011-prompt-verification.md)。

新增execution/paper_run.py（脱敏原子报告/配置哈希/运行差量）、evaluation/fast_archives.py（冻结共同分钟与隔离前瞻双链采集）、scripts/collect_fast_archives.py（有界只读默认入口）；runner/pipeline/daily_budget/run_container负责运行时限、停止与跨日费用归档。测试与实证见[本轮报告](../research/20261010-engineering-completion.md)。

Task18安全工程：runner免费门禁先于收费双探针，daily_budget只读检查两次预算；Pipeline先保留程序风险退出，再以WAIT_DATA_QUALITY拒绝缺指标模型worker。新profile的ready不代表交易闭环，详见[Task18事实与限制](../research/20261009-task18-verification.md)。

Task16分钟特征已修复：连续1m窗口遇缺口/午休/隔夜重置，当天已观察量额VWAP，分钟版本minute-feat-2026-10-09；实现context/features.py与builder.py，308离线测试通过。真实盘中质量未验收，详见[验证报告](../research/20261009-task16-verification.md)。

容器显式网络入口已实施：`scripts/run_container.py check` 或 `paper --steps N`，宿主venv运行；读取既有宿主代理，仅传子进程，不修改compose/.env网络。paper先停workbench并最终恢复，默认不收费探针。工作台本身不继承此参数。操作/验证与限制见[Task15报告](../research/20261009-task15-verification.md)。

## 显式ETF分钟源（2026-10-09）

[ARCH-008](../architecture/08-etf-minute-source.md) implemented：market/adapters/sina_etf.py及DatasetRouter显式sina_etf，核验ETF限定、1m、8秒、量额单位/字段校验、未闭合分钟过滤、fetch可见时点。container示例及container/fast local已选择该源，不调用股票SDK回退。三ETF数据能读取，夜间正确stale；源完整性/盘中新鲜度待验收。更新Key后JEV经现有代理三题型HTTP200，直连451/正常启动网络条件与盘中质量仍待验收。完整证据与下一步统一见[运行连接报告](../research/20261009-runtime-connectivity.md)。

## 免费分钟质量路由（2026-10-14）

[ARCH-020](../architecture/20-free-minute-source-routing.md) implemented：`market/adapters/live.py` ETF分支（`_ETF_MINUTE_LOT=100`手→份额、量额关系自检、显式不复权、白名单门禁、闭合行过滤）；`market/router.py` 质量接受路由（`MinuteRoutingPolicy`+`from_data_config`、`MINUTE_SOURCE_PROTOCOL_STATUS`注册表、接受循环/最不坏回退、`_bounded_fetch`有界等待、退避+恢复探测、北京日请求计数、源级缓存重验、稀疏交叉观察、`minute_source_log`逐证券留证）；`market/quality.py` 新增`selected_provider`/`source_rejections`字段与`cross_source_close_divergence`；`config.py` DataConfig 10个`minute_*`策略字段；`context/builder.py`、`pipeline.py`、`runner.py` 调用契约（as_of+quote_max_age_seconds传入）；`config/t0-fast.example.toml`、`config/t0-container.example.toml` 主备示例；`scripts/check_fast_startup.py` 三态报告；`evaluation/fast_archives.py` FrozenMinutes签名适配（行为不变）。测试：`tests/test_minute_source_routing.py`（17项）、`tests/test_free_minute_sources.py`（12项）。证据：[Task14-03](../tasks/TASK-20261014-03-核验免费ETF备用源与单位映射.md)、[Task14-04](../tasks/TASK-20261014-04-实现分钟行情质量驱动多源回退.md)。

[ARCH-023](../architecture/23-minute-request-inflight-capacity.md) implemented：`market/router.py` 新增`max_inflight_requests=4`/`max_inflight_requests_per_source=1`策略默认值、原子槽位与逐源计数、主源/观察共用`_begin_minute_request`、worker `finally`释放及有界`close`；busy写入`minute_source_log`/`source_rejections`，不新增TOML字段或适配器改动。`tests/test_minute_source_routing.py`现33项（含2026-10-19启动/关闭交接3项回归，独立复核见[Task19-05](../tasks/TASK-20261019-05-复验路由关闭竞态修复.md)）；最初新增5项覆盖恢复探针/全局容量/健康备用/观察/日切/异常/迟到/关闭；证据见[Task17-13](../tasks/TASK-20261017-13-限制分钟行情超时后台请求并发.md)。

容器部署文件：Dockerfile、.dockerignore、compose.yaml、config/t0-container.example.toml；启动、构建和实际验证见 [TASK-20261009-11](../tasks/TASK-20261009-11-Rancher容器运行.md)。不修改业务代码。

最新增量（2026-10-13～14批次，Owner: Claude Code；各Task文件含实施与验证记录）：
- [TASK-20261013-12](../tasks/TASK-20261013-12-统一异步卖出退出条件语义.md)：pipeline.py退出条件与策略契约统一语义（context/fast_state.py新增conditions_allow单一来源），异步SELL不再被旧AND条件拦截；tests/test_fast_architecture_repairs.py、test_fast_decision.py。
- [TASK-20261013-13](../tasks/TASK-20261013-13-隔离持仓行情故障与风险退出.md)：单持仓行情失效隔离（pipeline.py、guard/guard.py、portfolio/ledger.py），缺行情标的跳过、不阻断其他标的风险退出；test_fast_architecture_repairs.py、test_t0_runtime_repairs.py、test_t0_risk_guard.py。
- [TASK-20261013-14](../tasks/TASK-20261013-14-统一账户写入进程互斥.md)：新增execution/writer_lock.py跨进程单写租约（按库+账户，持锁者心跳、崩溃过期释放；路径解析与Database共用storage/sqlite_url.py），pipeline构造时取得、runner对WriterLockHeld输出JSON失败；运行类命令finally释放，serve取得后持至进程退出（写端点需token）；tests/test_writer_lock.py含真实子进程竞争（Windows与Linux容器均已实测）。
- [TASK-20261013-15](../tasks/TASK-20261013-15-完善交易日历覆盖与更新门禁.md)：market/calendar.py覆盖范围与越界/非交易日语义，auto_paper覆盖不足时停模拟并示"日历覆盖不足"；新增market/calendar_update.py与scripts/update_calendar.py离线合并官方日历（并集、默认拒绝缩小、写.meta.json旁证）；test_market.py、test_auto_paper.py、test_fast_startup.py。
- [TASK-20261013-17](../tasks/TASK-20261013-17-建立可验证数据库升级与恢复流程.md)：storage/db.py顺序迁移migrate、SchemaVersionError拒绝更高版本、backup_to/verify_database；迁移2补ix_intents_created/ix_calls_created/ix_ctx_asof/ix_plan_hist_acct_avail；test_storage.py、test_recovery.py。
- [TASK-20261013-16](../tasks/TASK-20261013-16-限制健康与日报历史查询范围.md)：健康与日报历史读取窗口化（execution/paper_run.py、reports/daily.py，复用迁移2索引，账户事件保持全量）；test_daily_report.py、test_auto_paper.py。

全量回归481项通过（`python -m pytest tests --ignore=tests/test_multi_asset.py`；该文件因环境缺pandas持续排除）。真实盘中/容器生效与长期库收益未验，见各Task未验项。

上一增量：[TASK-20261013-10](../tasks/TASK-20261013-10-保障后续交易日模拟交易闭环.md)。`pipeline.py`：`close_model_worker`排空终态分类与`model_drain`审计（仅worker未排空/不可分类/审计失败锁定，见[ARCH-015](../architecture/15-model-drain-terminal-states.md)）；测试`tests/test_paper_run_report.py`。

更早增量：[TASK-20261009-10](../tasks/TASK-20261009-10-修复快速决策架构问题.md)。`pipeline.py`：模型 worker、结果再校验、原子期限/恢复和诊断；`portfolio/ledger.py`：账户视图加锁；`models/{daily_budget,deepseek}.py`：已结算/待确认费用；`evaluation/fast_comparison.py`：profile、完成时间和配置回放。回归：`tests/test_fast_architecture_repairs.py`。

2026-10-09：[ARCH-006](../architecture/06-jev-fast-decision.md) 新 profile 的离线工程已实施（02、04～07 completed），266项测试通过；03真实认证/费用、08真实对照校准、09真实启动仍未完成。旧默认链保留，新profile须显式选择。见 [验证报告](../research/20261009-fast-decision-verification.md)。

## 新链文件（2026-10-09已存在）

| 文件 | 职责 |
|---|---|
| src/trademind/domain/strategy.py | 计划、可计算条件、事实与动作契约 |
| src/trademind/agents/strategy_planner.py | flash计划/校验/版本化缓存 |
| src/trademind/context/fast_state.py | 可见原始事实、合法动作与账户版本 |
| src/trademind/agents/fast_decider.py | 官方JEV原子题组合、低可信WAIT |
| src/trademind/agents/fast_pipeline.py | 单模型槽、刷新与意图规范 |
| src/trademind/models/jev.py、daily_budget.py | 官方协议/usage及统一计数/费率预约 |
| src/trademind/pipeline.py、runner.py | 显式profile/Guard/OMS/最大持有退出/CLI |
| src/trademind/evaluation/fast_comparison.py | 无网络归档意图对照与隔离账户 |
| scripts/check_fast_startup.py | 无凭据、有界免费行情启动预检 |

无包布局重构或新数据库表；既有coalesce/audit使用版本化payload。Provider实际目录为models/。

## 多市场入口

- market/multi_asset.py：发现、快照/分钟观察、资格与资金筛选、超时边界。
- config.py：MarketAccessConfig；config/market-access.example.toml与market-rules.example.json：模板。
- runner.py：market-data/market-bars，无模型入口。
- market/profiles.py：拒绝新品种误套A股执行规则。
- api/app.py、api/static/index.html：报告API和观察表。
- tests/test_multi_asset.py：24项新增测试（含参数化），全套227项通过。

逻辑边界见[系统概览](system-overview.md#多市场观察与购买条件筛选)。

## 程序入口与核心模块

- CLI 入口：`src/trademind/runner.py`（status / demo-decision / poll-fills / replay-demo / serve / probe-llm / recover）
- 全链路编排：`src/trademind/pipeline.py`（Pipeline.run_decision / poll_fills）
- API 入口：`src/trademind/api/app.py`（FastAPI；工作台 `src/trademind/api/static/index.html`；管理员设置 `/api/admin/settings*`——控制令牌门禁、密钥仅掩码回传）
- ASGI：`uvicorn trademind.api.app:app`

## 当前导航

| 路径 | 职责 |
|---|---|
| src/trademind/domain/ | 领域契约（enums/base/instruments/evidence/context/research/judgments/intents/orders/outcome/memory/modelcall） |
| src/trademind/config.py, errors.py | 配置加载（TOML+env+管理页覆盖 `data/runtime/admin_settings.json`、模式隔离、设置白名单/掩码）与错误分类 |
| src/trademind/market/ | base（Bar 等记录）、calendar（覆盖/越界门禁）、calendar_update（官方日历合并）、profiles、quality、router、multi_asset、adapters/{fixture,live,sina_etf} |
| src/trademind/storage/ | schema.sql、db.py（Database/Repository/AuditSink、migrate/backup_to/verify_database、逐账户事件重放对账）、sqlite_url.py（sqlite URL→路径单一语义，Database与writer_lock共用） |
| src/trademind/context/ | builder.py、features.py |
| src/trademind/orchestrator/ | scheduler.py（有界队列、排队与执行合并、持仓优先） |
| src/trademind/models/ | base/deepseek(多协议LLM provider)/protocols(四协议线格式归一)/jev/stub/provider_factory、daily_budget |
| src/trademind/agents/ | prompts.py、research.py、trader.py、risk.py、fast_prompts/fast_decider/strategy_planner |
| src/trademind/guard/ | guard.py（九步检查+原子预留） |
| src/trademind/portfolio/ | ledger.py（单写者账本） |
| src/trademind/execution/ | oms.py、paper.py、paper_run.py、auto_paper.py、multi_asset_paper.py、writer_lock.py（跨进程单写租约） |
| src/trademind/memory/ | memory.py（MemoryStore/OutcomeService/Reflector） |

记忆可见性时点查询（2026-10-19，Task18-03/Issue18-01）：`storage/db.py::memories_visible` 对TEXT混合偏移`available_at`只做加宽2天的SQL粗预筛，精确可见性在Python按解析的带时区datetime瞬间比较（与`MemoryRecord.visible_at`同语义），存量混合偏移行免迁移正确查询，naive as_of拒绝；回归`tests/test_memory_visibility.py`（7项，含Issue18-01两行反例、跨偏移同瞬间等价、微秒/跨日/+14时区边界、混合偏移排序、撤销/valid_until/标的过滤）。

反思成熟与生成时点门禁（2026-10-19，Task18-05/Issue18-02）：`memory/memory.py::Reflector` 构造新增可注入aware时钟（默认utcnow）；`reflect`在模型调用/落库前拒绝未final与未到available_at的Outcome（提前平仓final不重查horizon_end），LLM返回后新经验`available_at=max(outcome.available_at, completed_at)`不回填历史；回归`tests/test_reflection_boundaries.py`（5项：两拒绝/晚生成/正常控制/提前平仓）。

结果标签成熟时点与终点取值（2026-10-19，Task19-02/Issue19-01）：`memory/memory.py::OutcomeService` register固定上海时区horizon（终点交易日07:00+08，同瞬间跨时区等价，版本lbl-2026-10-19）；mature到期+终点bar可见双门禁（可注入时钟），仅取horizon日收盘、缺失不用次日代替，legacy行成熟保留原版本；候选发现逐行`horizon_end<=now`且终点日`bar_close_time`(15:00+08)`<=now`（不早于真实收盘、收盘后/次日可发现，Task19-08复验后终版）；mature日线查询传`as_of=now`（Task19-10，可见性过滤先于revision归并）；回归`tests/test_outcome_maturity.py`（9项，合成日历+实际FixtureAdapter/Router+可增补修订Adapter）。

日线修订时点可见（2026-10-19，Task19-04/Issue19-02）：`market/router.py::get_daily_bars` 新增`as_of`参数，归并前过滤`available_at<=as_of`可见集合并按available_at升序去重（quality.py语义未改），未来修订不再挤掉历史已知行；`context/builder.py`两处日线调用传入as_of；回归`tests/test_daily_revision_visibility.py`（4项：组合反例/逆序重复/路由契约/持仓估值，真实Router+ContextBuilder+可增补合成adapter）。
| src/trademind/evaluation/ | replay.py、calibration.py |
| src/trademind/api/ | app.py、static/index.html |
| scripts/generate_fixtures.py | 确定性 fixture 生成 |
| data/fixtures/ | calendar/daily/news/fundamentals |

## 测试、配置、数据 / Schema / Migration

- 测试：`tests/`（contracts/storage/market/ledger/context/providers/guard_oms_pipeline/evaluation/api/recovery 及 T0 系列，2026-10-14 起全量 563 项无 --ignore（pandas 已装，test_multi_asset 纳入））；`tests/conftest.py` 提供合法对象工厂。
- 配置：`config/trademind.example.toml`（复制为 trademind.toml，已 gitignore）。
- Schema：`src/trademind/storage/schema.sql`（幂等迁移 + schema_migrations）。
- 锁文件：`requirements.txt`（固定环境依赖 + 本地 -e .；不得写回旧远程 editable）。

## 需求与规划入口

- docs/requirements.md · docs/architecture/01-ai-trading-system.md · docs/tasks/index.md
- docs/research/20261007-a-share-free-data.md · docs/research/20261007-tech-stack-verification.md

## 独立验收导航

- docs/research/20261007-code-audit.md：首版验收结论与修复映射。
- docs/research/20261007-audit-repro.py：离线缺陷回归入口，使用 pytest 临时数据库，无外部 Provider 请求。

- `tests/test_audit_regressions.py` / `tests/audit_helpers.py`：40 项缺陷回归与真实 Guard 授权工厂。
- `scripts/verify_install.py`：临时干净环境安装、测试、wheel 资源与仓库外 CLI 校验。

## 2026-10-08 非JEV修复入口

- src/trademind/models/daily_budget.py：持久共享每日请求计数与DeepSeek费用上界预留。
- src/trademind/evaluation/prompt_paper.py：复用Pipeline/Guard/OMS的两个隔离历史模拟账户及归档。
- src/trademind/pipeline.py：T0分钟、确定性风险退出、冷却、意图审计；原V1分支保留。
- tests/test_t0_runtime_repairs.py：18项真实组合回归，涵盖数据/退出/重启/预算/CLI/隔离评测。

其余修改文件与行为见[复验报告](../research/20261008-non-jev-fix-verification.md)，JEV官方Provider未改。

## 2026-10-08 T0已登记全池更新

runner.cmd_t0_data：无模型/凭据免费数据检查；runner._t0_instruments：池/T0规则验证；router._minute_cache：30秒实时观测缓存；tests/test_t0_market_pool.py：6项池与数据入口回归。关联[ARCH-004](../architecture/04-t0-market-pool.md)。

2026-10-17：ledger.py T0跨日单次解锁、paper.py 撮合质量门禁、fast_pipeline.py 含费数量钳制、oms.py `expire_stale_orders`（[ARCH-021](../architecture/21-t0-intraday-order-lifecycle.md)）；测试：test_ledger.py（T0跨日5项）、test_t0_oms_execution.py（撮合质量2+生命周期6）、test_fast_decision.py（含费数量3）。

2026-10-17：oms.py生命周期操作与guard.py恢复/同标的扫描账户隔离、ledger.apply_fill外账户防御（17-08）；测试：test_t0_oms_execution.py多账户隔离2项。

2026-10-17：oms._poll_fill成交费用预填与权威回填（17-09，费用唯一入口ledger.compute_fees）；测试：test_t0_oms_execution.py费用一致性2项。
