# 系统概览

自动运行边界（ARCH-013）：一个auto容器内supervisor按上海日历顺序启动serve或既有有界paper，保持一个账户writer。连续时段paper，等待/休市/失败退避serve，盘中UI暂停；状态心跳只写runtime文件，不产生交易授权。模型探针/费用/质量/硬风控仍归原paper，未知worker结果锁定自动重试。排空终态分类（ARCH-015）：收盘排空对已完成的模型结果写`model_drain`审计终态（取消未启动/模型异常/过期废弃/有效不执行）后安全恢复，仅worker未排空、结果不可分类或审计写入失败保持锁定；正常收盘不再误锁次日，监督器保护策略不变。与workbench部署模式互斥；原完整闭环验收不变。

2026-10-14边界补充（同日复核更正serve表述）：账户写租约（[ARCH-017](../architecture/17-writer-lease.md)，更正记录见其复核更正节）——所有写入口构造Pipeline时先取得库+账户级跨进程租约（内核持进程死亡自动释放，无陈旧锁/任意删锁），第二写者立即失败零副作用；锁键与Database共用`storage/sqlite_url.py`路径语义（相对/绝对URL指向同一实际库得到同一租约）。serve经init_app构造Pipeline取得租约并持至进程退出（其kill_switch/decide/poll_fills写端点需token，[ARCH-017](../architecture/17-writer-lease.md)矩阵）；runner运行类命令与评测脚本在进程结束前显式释放；只读日报/查询不取得租约、他进程持锁时照常生成。Windows真实子进程已验；Linux容器验证见current-status与Task-14。估值隔离（[ARCH-016](../architecture/16-position-valuation-isolation.md)）——逐持仓独立估值，单持仓行情失效记入诊断不阻断其他标的风险退出；估值不完整时Guard否决新开买入，不以残缺估值落日初基线。交易日历（[ARCH-018](../architecture/18-calendar-ownership.md)）——覆盖建模为已确认段+未确认空档（段间隙>14天为未知，不是休市），覆盖外/跨空档显式拒绝、覆盖内非交易日按休市，时段判断只在覆盖内进行；auto覆盖不足停止模拟与付费请求，日历更新仅经离线合并接口（官方输入按其区间完整陈述，重叠已确认段整段替换、区间外段保留历史，meta记录csv_sha256供load()一致性比对）。持久化升级（[ARCH-019](../architecture/19-db-upgrade-backup.md)）——`Database()`打开先以只读immutable预检Schema版本再执行任何写入/PRAGMA（更高版本拒绝、文件字节不变）；打开按`schema_migrations`顺序迁移（当前v1→v2附加性）；备份`backup_to`、只读校验`verify_database`含逐账户事件重放资金对账（现金/冻结/活动预留/持仓/版本），账目不平即ok=False。决策条件语义单一来源`context/fast_state.conditions_allow`（BUY全部入场/SELL任一退出/空集不授权），同步构造与异步复核共用；健康与日报历史读取按上海日窗口走索引，账户事件保持全量重放。真实盘中/容器生效均未验。

新增日报边界（ARCH-012 implemented）：主CNY SQLite账户的事件/成交/预算/策略/意图→只读事务快照→中文Markdown/HTML及原始JSON。工作台生命周期每60秒生成今日/昨日，paper结束触发；报告线程只写报告、不执行模型/行情或账户操作。有效策略history追加存储，strategy-4中文说明及planner语言校验已实施，DeepSeek统一中文指令，JEV协议保持；旧缓存按版本失效但保留冷却。日终报不代表交易闭环验收。[报告](../research/20261012-daily-report-verification.md)。

提示评测新增只读离线边界：固定合成案例/版本标签＋保存模型响应及程序观测→生产契约/行为检查→分项JSON/Markdown报告；不初始化provider、不联网、不接订单或账户写入。strategy-3追加WAIT/有仓退出配对示例，JEV仍fast-2。真实档案自动适配/晋级/回滚增强尚未实现。详见[本轮证据](../research/20261011-prompt-eval-verification.md)。

快速链提示已版本化为strategy-3/fast-2（此后已升级为strategy-4，见上方日报边界）；策略Schema从实际契约派生，程序独占身份/时间字段，旧缓存按提示版本失效且仍受冷却。JEV独立题目的入场判断只约束BUY，正常风险反方意见不直接等于计划失效；程序仍按原阈值/Guard/OMS裁定。证据与真实限制见[提示验证](../research/20261011-prompt-verification.md)。

2026-10-10新增已实现运行边界：paper免费门禁→同进程探针→既有异步worker/主线程Guard→禁止新预算预约/排空→白名单原子报告。预算按预约日归档/结算；collector共同真实采样→独立旧新500账户→前瞻档案→禁网对照，缺档不通过。文件与验证见[收尾报告](../research/20261010-engineering-completion.md)，ARCH-009仍implementing，真实条件尚未验收。

Task18安全工程：runner免费门禁先于收费双探针，daily_budget只读检查两次预算；Pipeline先保留程序风险退出，再以WAIT_DATA_QUALITY拒绝缺指标模型worker。新profile的ready不代表交易闭环，详见[Task18事实与限制](../research/20261009-task18-verification.md)。

Task16分钟特征已修复：连续1m窗口遇缺口/午休/隔夜重置，当天已观察量额VWAP，分钟版本minute-feat-2026-10-09；实现context/features.py与builder.py，308离线测试通过。真实盘中质量未验收，详见[验证报告](../research/20261009-task16-verification.md)。

容器显式网络入口已实施：`scripts/run_container.py check` 或 `paper --steps N`，宿主venv运行；读取既有宿主代理，仅传子进程，不修改compose/.env网络。paper先停workbench并最终恢复，默认不收费探针。工作台本身不继承此参数。操作/验证与限制见[Task15报告](../research/20261009-task15-verification.md)。

## 显式ETF分钟源（2026-10-09）

[ARCH-008](../architecture/08-etf-minute-source.md) implemented：market/adapters/sina_etf.py及DatasetRouter显式sina_etf，核验ETF限定、1m、8秒、量额单位/字段校验、未闭合分钟过滤、fetch可见时点。container示例及container/fast local已选择该源，不调用股票SDK回退。三ETF数据能读取，夜间正确stale；源完整性/盘中新鲜度待验收。更新Key后JEV经现有代理三题型HTTP200，直连451/正常启动网络条件与盘中质量仍待验收。完整证据与下一步统一见[运行连接报告](../research/20261009-runtime-connectivity.md)。

## 免费分钟质量路由（2026-10-14）

[ARCH-020](../architecture/20-free-minute-source-routing.md) implemented（2026-10-15两轮复核缺口当日全部修复，缓存命中统一检查证券级_divergence，[Issue15-01](../issues/ISSUE-20261015-01-跨源观察一致性与时间预算门禁不完整.md) resolved、Task14-04 completed；全量577+通过，2026-10-16已有真实策略与JEV成功样本，下午续跑仍受探针额度阻断（见当前状态））：ETF协议/单位自检、基础质量回退、源级缓存、单源与观察整轮预算、退避/计数、首次观察与as_of接受策略、跨源差异持续拒绝（含缓存路径）均有实现和回归。执行侧同受质量门禁：PaperBroker撮合拒绝不合格窗口（Task16-07），T0日内订单生命周期见[ARCH-021](../architecture/21-t0-intraday-order-lifecycle.md)。公开example展示主备，本地local未切换；真实样本及盘中双源质量待Task09-16。

[ARCH-023](../architecture/23-minute-request-inflight-capacity.md) implemented（2026-10-17工程口径）：router拥有进程内分钟请求槽位与worker列表，默认全路由4、每源1；主源/观察在请求计数前原子申请，无队列busy不消耗计数。caller超时只结束等待，worker实际返回/异常后才释放；日切不清槽，迟到结果不能写缓存、选源、health或divergence。`close(timeout_seconds)`拒绝新分钟SDK请求并有界join，不强制取消。原质量路由、账户/Guard/OMS所有权不变；真实SDK和容器资源效果未验。

本地部署增加Rancher Desktop容器入口：[ARCH-007](../architecture/07-container-runtime.md)。Linux Python3.12运行既有CLI/API/SQLite，独立容器账户与host runtime挂载；根目录.env和容器配置只读挂载，工作台端口仅host localhost暴露。模型/Guard/OMS边界不变，不自动启动交易。

新链运行边界补充见 [TASK-20261009-10](../tasks/TASK-20261009-10-修复快速决策架构问题.md)：CLI 模型在单工作线程计算，主线程独占执行并重检当前条件/账户/期限；成交及持仓期限原子提交，缺失期限可重建。直接 Pipeline 调用保留同步接口。原默认链不变。

2026-10-09：[ARCH-006](../architecture/06-jev-fast-decision.md) 新 profile 的离线工程已实施（02、04～07 completed）；03官方认证与参考费率其后已完成（Issue-20261007-12/20261008-02 resolved），08真实对照校准、09真实启动仍待真实条件。旧默认链保留，新profile须显式选择。见 [验证报告](../research/20261009-fast-decision-verification.md)。

## 显式新 profile 的当前工程架构

jev_fast_decision：原始可见分钟及规则/账户 → 程序止损/时间/尾盘退出优先 → 缓存StrategyPlan（必要时deepseek-flash三维分析） → FastDecisionState → 官方JEV独立Choice/Noul题 → 程序规范动作/数量 → 现有Guard/OMS → paper账本/审计。正常决策后不再固定调用flash汇总。旧jev_deepseek仍为默认及对照，V1保留。

计划/候选槽/预算/持有deadline归coalesce_state，订单及资金仍归OMS/ledger，审计绑定计划/行情/账户版本。模型没有订单权限；真实probe未通过、未知价格、过期/账户变化/缺题均不新增风险。shadow经Guard拒绝任何执行。数据仍是分钟粒度，实际模型时延未测。

## 多市场观察、资格门禁与隔离模拟执行（ARCH-011）

`market/multi_asset.py` 负责 ETF、LOF、可转债、港股、境内期货批量发现和按需分钟观察；`market/rules.py` 与 `market/access.py` 负责官方核验规则库与账户细分权限门禁（支持港股通名单验证、期货特殊/金融权限门禁、可转债强赎及停牌拦截）。

执行层面通过 `market/profiles.py` 与 `execution/multi_asset_paper.py` 落地隔离模拟执行：
- 可转债：10 张申报单位、T+0 交易、20% 涨跌幅限制、强赎/停牌拦截。
- LOF：QDII 境外/大宗 LOF 允许 T+0 日内回转；境内股票 LOF 为 T+1（当日卖出被拦截）。
- 港股：严格隔离港币账本 `MultiAssetLedger`（零串币，CNY 与 HKD 互不污染），支持每手股数与印花税/经手费计算。
- 期货：保证金交易模式（按 `price * qty * multiplier * margin_rate` 占用与释放保证金，计算多空平仓盈亏）。
- 候选筛选：`market/selector.py` (`MultiAssetCandidateSelector`) 与 CLI `market-select` 按流动性排序并受 `top_k_per_market` 与 `max_total_candidates` 硬顶截断，零模型调用。

详见 [ARCH-011](../architecture/11-multi-asset-execution.md)，[ISSUE-20261008-05](../issues/ISSUE-20261008-05.md) 已解决关闭。

## T0当前实现补充（2026-10-08）

T0启用时，Pipeline使用分钟Context、分钟持仓估值和Guard参考价；持仓先由代码检查止损/尾盘退出，经原Guard/OMS执行，之后才进入双模型候选/审核/最终意图。最终意图直接构造PortfolioIntent，代码计算合法数量和硬性否决；不再接旧股票版的额外风险/组合模型调用。原V1路径保留。

SQLite仍是账户和审计的存储；审批日计数持久化，连续亏损从实际成交事件的完整平仓重建；模型请求共享交易日持久计数，DeepSeek费用按配置价格与token上界预留。配置未知价格拒绝收费请求；JEV费用解析已完成（[Issue-20261008-02](../issues/ISSUE-20261008-02.md) resolved，配置估值为参考费率而非账单）。CLI各入口显式共享TOML，撮合只看当前可见分钟。

[ARCH-003](../architecture/03-t0-runtime-safety.md)记录边界与历史；[复验报告](../research/20261008-non-jev-fix-verification.md)维护证据及费用口径。完整JEV双模型/盘中真实能力仍未验收。


## 当前系统目标与边界

以 AI 为决策核心的 A 股股票交易系统：AI 产生投资意图，程序负责事实计算、硬性风控与模拟执行。当前为单用户本地单进程架构，paper/shadow/replay 三种模式，无 Live 通道（配置层拒绝）。

## 当前核心组件与关系（已实现，对应 ARCH-001 模块表）

```
market(适配器/路由/日历/质量) → context(时点快照+特征)
  ├→ agents.research(5分析师→Bull/Bear→Thesis)
  └→ models.jev(JEVGate 批量判断)
Thesis + Judgments + Portfolio → agents.trader(TradeIntent)
  → agents.risk(APPROVE/REVISE/REJECT) → agents.portfolio(PortfolioIntent)
  → guard(九步检查+原子预留) → execution.OMS → paper broker → portfolio.Ledger
  → memory(Outcome/Reflection) → 后续 context
orchestrator: Pipeline 入口有界队列、触发合并、持仓优先；models: 调用预算；storage: SQLite+审计；evaluation: 回放/校准/成本
```

单写者账本（Ledger）、单进程 orchestrator、Provider 接口隔离（DeepSeek / JEV-HTTP / 显式 stub）。

## 当前数据所有权与外部系统

| 数据 | 所有者模块 | 存储 |
|---|---|---|
| evidence/context/theses/judgments | market / context / models | SQLite 表 + JSON payload |
| intents/risk_reviews/guard_results | agents / guard | 同上 |
| orders/fills/reservations/account_* | execution / portfolio | 唯一键约束 |
| outcomes/memories/reflections | memory | available_at 索引 |
| model_calls/control_audit | models / control | 脱敏元数据 |

外部系统：DeepSeek API（OpenAI 兼容，密钥未配置时 stub）；JEV（TypeSafe官方adapter已实现，三题型认证HTTP200与费用解析通过，见[Issue-20261007-12](../issues/ISSUE-20261007-12.md) resolved；容器直连451与盘中新鲜度仍受限）；akshare/baostock/tushare（SDK 已装，接口未实测）；数据 fixtures 为离线主源。公开来源核验见 [新核验记录](../research/20261007-t0-shadow-verification.md)，用户保存Key不等于真实能力已验证。

## 当前接口、约束与运行边界

- 消息契约：schema_version/id/trace_id/created_at；金额数量十进制字符串；概率 [0,1] 且带语义；缺失=null+missing_reason；NaN/未知动作/naive 时间拒绝。
- Guard 检查顺序与原子预留见 [flows](flows.md)；所有模式（含 paper）必经 Guard。
- 回放仅可见 available_at <= as_of；记忆检索先时点过滤。
- 管理端控制操作需 token；未配置则禁用。
- 管理员运行时设置（ARCH-024 implemented）：`/api/admin/settings*` 同受控制令牌门禁；密钥/服务地址白名单存于 gitignored 的 `data/runtime/admin_settings.json`，仅在加载凭据路径应用（replay/shadow 隔离不受影响），优先级系统环境 > 管理页覆盖 > `.env` > TOML；保存后重启生效，接口仅回传掩码值，模型型号与管理令牌不可页面修改。
- LLM 多协议接入（ARCH-025 implemented）：flash 策略 LLM（`llm.*`）支持 `openai_chat`（默认，DeepSeek 官方）/ `anthropic_messages` / `openai_responses` / `gemini_generate_content` 四种线协议，`models/protocols.py` 归一请求构造、响应解析、usage 与失败分类（诊断键不扩白名单）；经 `[llm].protocol`、env `TRADEMIND_LLM_PROTOCOL` 或管理页 `llm_protocol`（枚举校验）选择；base_url 缺省按协议取官方端点、自定义中转 URL 原样保留；审计 provider 枚举不变、线协议记入 `model_params`。JEV TypeSafe 官方协议与 T0 型号锁定（deepseek-flash）不变；四协议均为离线 mock 验证，真实调用未验。

## 规划入口

[ARCH-002](../architecture/02-t0-etf-shadow.md) 已 superseded：T0 ETF最小模拟盘工程已实施（见其替代关系与 ARCH-006），后续真实能力目标由 [ARCH-006](../architecture/06-jev-fast-decision.md) 承接（真实闭环未验收）。这里不把规划模块写成已实现。

[ARCH-001](../architecture/01-ai-trading-system.md) 状态 implementing（V1 链路已落地，真实能力验证后转 implemented）。

## 相关记录

[ARCH-000 初始化基线](../architecture/00-initial-architecture.md) · [技术栈核验](../research/20261007-tech-stack-verification.md) · [A 股数据研究](../research/20261007-a-share-free-data.md)

## 独立验收边界（2026-10-07）

离线工程复验通过，证据见 [复验报告](../research/20261007-code-audit.md#修复复验2026-10-07)。

- cash 包含冻结部分，equity=cash+market_value。Guard 的 ALLOW、资金冻结和审计原子提交；OMS 使用同一持久授权 ID，并重检意图哈希、数量、限价、账户、模式、版本及 TTL。
- 成交、账户、预留、订单与审计在同一事务提交；Ledger.atomic 同时回滚内存，日切与快照恢复基于事件。UNKNOWN 阻止新风险。
- 在线 paper 使用墙钟，shadow 禁止订单与冻结；ReplayRunner 深拷贝、清凭据、固定 fixture/stub 与隔离新库，按可见时点顺序运行。
- JEV 自定 HTTP 协议携带完整可见状态、Thesis 和版本化题目；官方TypeSafe协议适配已由TASK-41完成并通过三题型认证（Issue-20261007-12 resolved），真实盘中能力仍未验收。

## 2026-10-08 T0已登记全池更新

单标的运行已扩展为配置池默认逐只轮询；独立t0-data只创建行情Router。运行先观测后取as_of，30秒实时分钟缓存复用已观测数据，fixture不缓存。资金/账本/模型额度仍是一个账户共享。架构见[ARCH-004](../architecture/04-t0-market-pool.md)。

费用责任（17-09）：模拟费用唯一计算入口为ledger.compute_fees；Fill.fee=账本实收（OMS持久化前预填、以apply_fill返回为权威）；日报/对照以account_events为事实源；历史fills行fee=0为旧占位，权威费用看事件note。

记忆时点隔离（2026-10-19工程修复）：[Issue18-01](../issues/ISSUE-20261018-01-记忆可见性按时区字符串比较导致未来信息纳入.md)曾证实混合时区available_at被按ISO字符串比较，未来记忆可被纳入、历史记忆可被遗漏；Task18-03已修复——SQL仅做加宽2天的粗预筛，可见性判定统一为解析出的带时区datetime瞬间比较（与MemoryRecord.visible_at同语义），存量混合偏移行无需迁移即被正确查询，naive as_of显式拒绝。回归见tests/test_memory_visibility.py；真实生产库与快速链受影响面仍未观察到。

反思模块（2026-10-19工程修复）：[Issue18-02](../issues/ISSUE-20261018-02-反思入口缺少成熟与生成可见时点门禁.md)曾记录成熟/可用门禁缺失及新经验回填历史；Task18-05已修复——`Reflector.reflect`在模型调用与落库前拒绝未final/未到available_at结果（提前平仓final保留），新经验visible时间不早于生成完成与标签可用时刻。src仍无反思业务自动调用方，上述memory→后续context流程不表示已完成自动反思闭环。

结果标签服务（2026-10-19工程修复）：[Issue19-01](../issues/ISSUE-20261019-01-结果标签成熟时间与终点行情选择不一致.md)曾记录登记时区差8小时、提前mature写final及误取终点次日收盘；Task19-02已修复——horizon固定上海时区（终点交易日07:00+08，同瞬间跨时区等价），mature要求到期且终点bar可见，final只用horizon日收盘；候选发现不早于终点日收盘15:00+08、收盘后/次日仍可发现（Task19-08复验后终版）；新登记版本lbl-2026-10-19，legacy行保留原版本。成熟服务仍无自动业务调用，结果反馈闭环未接入。

日线修订时点可见（2026-10-19工程修复）：[Issue19-02](../issues/ISSUE-20261019-02-未来日线修订提前挤掉历史可见版本.md)曾证实先归并后过滤可丢历史已知版本；Task19-04已修复——`get_daily_bars(as_of=)`在revision归并前过滤可见集合，未来修订不再挤掉历史已知行；`as_of=None`保持实时默认，分钟as_of门禁不变。Task19-10补齐遗漏调用方：`OutcomeService.mature`日线查询传`as_of=now`，结果成熟视角不受未来修订影响。真实多版本源与部署效果未验。

2026-10-19独立续验边界：[Task19-06](../tasks/TASK-20261019-06-独立复验四项时点修复.md)通过记忆跨时区、反思与日线修订原反例；结果标签发布/终点取值通过但盘前候选条件未完成，Issue19-01 open/Task19-02 in_progress。当前没有自动成熟业务调用或生产提前final证据。
