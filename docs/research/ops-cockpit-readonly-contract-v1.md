# Unified Ops Cockpit 只读契约 v1（2026-10-30 冻结）

范围：TASK-20261030-04 的文档交付；TASK-05 消费 SAMPLE，并经用户 2026-10-30 追加授权接入同源/配置化白名单 GET（失败按窗独立回退 SAMPLE）；跨项目正式接线、轮询退避与成本验证仍归 TASK-06。代码依据：TradeMind `api/workbench_routes.py` / `read_models.py`，trader `apps/trader/web.py` / `quant/dashboard/service.py`。冻结的是展示信封与候选 GET 映射，不保证源字段已齐全、HTTP 可达或查询成本已验。

## node / edge / event 公共字段矩阵

以下字段三类记录都必须有；未知用 null，不能省略后假定实时。版本不兼容按 missing 展示，禁止补造关联。

| 字段 | 类型 / 语义 | node / edge / event 规则 |
|---|---|---|
| project | trader 或 TradeMind | 不跨项目合并 |
| host | 字符串或 null | SAMPLE 为 Wang-SAMPLE；实际主机只能来自已核实身份，不从 URL 推断 |
| account | 字符串或 null | SAMPLE 专用别名；真实值服务端固定，股票与ETF分开 |
| run | 字符串或 null | 没有 run ID 保持 null；run-ledger 不等于 paper-runs |
| source | 固定源标识 | sample:* 或白名单 GET；禁止用户输入 URL、文件路径、SQL |
| schema_version | ops.v1 | 展示信封版本，不冒充后端原生版本 |
| data_as_of | ISO8601 带时区或 null | 原始事实时间；不能替换成请求时间 |
| observed_at | ISO8601 带时区 | 适配器观察时间；SAMPLE 使用固定样例时点 |
| data_status | ok / empty / missing / stale / error / unknown / loading | SAMPLE 另有 sample=true，绝不被 ok 隐藏 |
| evidence_ref | 稳定脱敏引用或 null | 仅展示文本，不执行链接；无 trace 不构造因果 |

node 另含 id、label；edge 另含 id、from、to、relation=config_dependency（静态配置依赖）或 verified_trace（需原始关联证据）；event 另含 id、node_id、label。node/edge/event ID 在 project/host/account/run 内唯一。跨窗不画边。装饰粒子只标“示意”，不表示实时流量、成功执行或事件发生。

## 窗格归属（冻结）

| 范围 | 实施 Owner / 事实 Owner | 交付位置 |
|---|---|---|
| 唯一 Vue 壳、导航、分屏、动效开关、键盘证据 | Claude / Wang（TASK-05；接管 Codex 原型）；保留26-14既有验收范围 | frontend/src/pages/OpsStreamPage.vue、components/OpsStreamPane.vue、demo/opsStreamSample.ts、adapters/opsReadonly.ts；router.ts、layouts/WorkbenchLayout.vue仅加入口及SAMPLE页状态隔离 |
| 左 trader：刷新→质量→因子→计划→风险→OMS→paper；account/run-ledger | TASK-345 合同交接；源事实归 trader query service / 原263、289、291、293、308 Owner | 本节与下表；346仅交左窗样例/验收，不建第二 UI |
| 右 TradeMind：行情→StrategyPlan→JEV→Guard→OMS→paper；account/activity | TASK-04合同、TASK-05样例；源事实归原只读服务及交易生产者 | 同一个 Vue 壳，独立身份和时点 |
| 两套适配器、网络鉴权、固定源身份核验 | TASK-06（未实施、Owner待认领） | 不新增代理、collector、数据库或交易权限 |

## GET 候选白名单 / 左窗交接 TASK-345

下表每项只允许 GET。HEAD 仅用于路由可达性，trader HEAD 静态200不能证明 GET成功、worker存活或交易就绪。**本轮不调用任何端点**。股票选固定 `/api/stock/`，ETF选固定 `/api/`，不混账户；主机/账户/run 未验证则 null 并禁用真实模式。

| 源 / Owner | 窗格映射 | 时间 / 单位 | 窗口与成本 / 接线处置 |
|---|---|---|---|
| trader `/healthz` / web.py | web可读性，非readiness | 无数据时点；无单位 | 单条、静态响应；不代表DB/worker |
| trader `/api/status`、`/api/stock/status` / get_system_status | 心跳、worker、readiness分列 | 原心跳时间；秒仅在原字段明确时 | 单快照；DB查询成本未知，06实测前不轮询 |
| trader `/api/data-verification`、`/api/stock/data-verification` / get_data_verification | 数据质量节点 | 原报告时点；原口径 | 原源返回集合；无已验证限窗则禁用高频 |
| trader `/api/plan`、`/api/stock/plan`、对应next-session / get_latest_plan、get_next_session_readiness | 计划/就绪；因子缺证据标missing | 原计划/评估时点；权重保留源单位 | 最新快照；不触发生成计划，成本待测 |
| trader `/api/account`、`/api/stock/account` / get_account_summary | 账户玻璃舱 | CNY Decimal字符串，估值时点未知保留null | 单固定账户；账户身份核实后才接 |
| trader 对应risk-rejections / get_risk_rejections | 风险拒绝摘要；非可编辑风控 | 源事件时间；次数 | 后端限条/最大历史窗未验，先禁用高频 |
| trader 对应orders、trades、reconciliation / 同名get_* | OMS/paper事实只读 | 源订单/成交时间；CNY/数量依原字段 | 不能用客户端截断冒充后端限量，成本待测 |
| trader `/api/run-ledger`、`/api/stock/run-ledger` / get_run_ledger | 运行履历与事件证据 | 原run时间/ID；无单位 | 不等于TradeMind paper-runs；后端上限未验 |
| TradeMind `/api/workbench/runtime` / WorkbenchReadStore | worker/observer/预算分别呈现 | 原源时点；预留≠结算 | 服务端固定账户；成本待06实测 |
| TradeMind `/api/workbench/overview`、positions、strategies / WorkbenchReadStore | 账户、持仓、计划 | CNY Decimal字符串；各源时点 | 快照；不当免费/收费provider入口 |
| TradeMind `/api/workbench/activity` / WorkbenchReadStore | 有界活动证据 | 原事件时点；无单位 | 使用现有服务端分页/限条；实际最大值按源码，06核对后接线 |

trader事实Owner不变；本轮只做源码合同审阅，263/289等任务无需为本文档冻结先行完成，但修改共享入口前仍须交接。未知费用、刷新周期、精确返回字段缺口、源最大窗口均显式保留 unknown，06未测前不得解除接线门禁。日志摘要HTTP未证实，显示missing并禁用；不读取原始日志、实际库或私密配置。LLM调用统计不并入TradeMind预算。

## 研究页提及但未入 v1 白名单的 GET（2026-10-30 Claude 补录，保持冻结）

[研究页](RESEARCH-20261030-cool-dataflow-frontend.md)事实表列有 `performance`、`performance.csv`、`backup-status`、`llm-calls` 四个trader只读GET，本文档上方白名单表未收录，显式记录为**未入v1白名单**：字段/单位/成本/分页未经源码审阅冻结，06接线门禁不解除前不得调用。绩效展示若未来接入，只作trader窗内只读证据，不与TradeMind账本/预算合并；llm-calls仅trader内部参考；backup-status属运维细节，非流水线展示必需。此节只补研究页与本契约的对齐缺口，不改变已冻结矩阵。

## 状态 / 脱敏 / 成本边界

### trader 已核实的原字段映射（源码，不代表在线验证）

- status：`current_time`是服务观察时间，不能当worker数据时间；`worker.healthy`与`worker.details`只映射worker证据；`database.connected`仅DB连通；`calendar.market_session`仅交易时段；`data.last_modified`是文件修改时间。禁止向UI透传`data.path`或未脱敏details/error。readiness另读next-session，不把healthy提升为可交易。
- account：仅`ok=true`后，`account_id`映射固定账户身份、`updated_at`映射账本更新时点；`currency/cash/total_equity/total_fees`保留字符串和源单位；`valuation_status=pending`时收益unknown。**源`ok=false`分支仍返回cash等`0.00`占位，适配器必须整体归error/missing并将金额置null，不能显示成真实零现金。** `positions_count`含历史零仓位，当前持仓使用`open_positions_count`；`external_flows_status=unsupported`时不宣称收益已扣净外部流。
- plan：`has_plan=false`映射empty；有计划则`plan_id`仅作计划证据，不能当run；`generated_at`是计划生成时点，`decision_date`不是精确时间戳；`lifecycle.state/execution_status`与生成时`actionable`分开。`data_status/dataset_version`保留源原文，不自动等同ops.v1状态。`cash_weight`为比例，不能转成现金值。
- run-ledger：`get_run_ledger()`调用`_run_ledger_snapshot(include_history=True)`；窗口/历史上限与条目字段待06定向验证，未验证不映射为事件脉冲。映射缺口保留missing，禁止新造run或trace。get_latest_plan扫描配置报告目录，get_account_summary加载账本并聚合成交，故不能把它们当低成本常量GET。

empty 仅表示成功读取后的真实空；missing 为源/字段不存在；error 为请求失败；stale 为已知过期；unknown 为无法确定；0只表示源中明确的数值零。每窗独立失败与标时，不因另一窗成功而变健康。SAMPLE无账户金额和收益汇总，不混真实数据。未来适配器仅允许明确白名单字段、脱敏摘要和稳定证据ID，不展示Token、连接串、个人信息或原始日志。

设计容量：每窗最多50节点/100边/500事件；本地样例远低于上限。真实后端限条/限时、轮询退避与成本验证归06，未测数字不得写成测量结果。禁止 `/api/control/*`、任何POST研究jobs、模型/行情调用、下单/撤单/解锁/现金/风控写入。
