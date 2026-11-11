# 当前状态（唯一当前事实入口）

> 2026-11-11（Asia/Shanghai）150 只读核验（非验收、未改运行、未同步代码）：监督器仍在跑（pid 6004，`code_version 62b5be1` 加其自身 dirty 文件），账户 `paper-t0-local-1k`，配置 `D:\trademind\config\t0-local.local.toml`，库 `D:\trademind\data\runtime\local-1k\trademind.db`，`halted=false`，`calendar_covered=true`，`session_open=true`。TASK-20261103-001 的分钟拒绝分类已在 150 上并被运行中进程写入 `last_blocker_class`、`data_window`、`missing_fields` 与连续 1m 根数，不必再同步 11-03。11-08 P0 与付费备源仍未加载、本次不动。

> 当日时间线：09:30:01 起免费源 `sina_etf` 按约 5 分钟做启动门禁拉取（`started_at` 09:30 / 09:35 / 09:40 / 09:45）。约 09:44 健康为「行情不足」，`last_blocker_class=data_reject`，信息为启动门禁未通过、五分钟后重新检查，当时模型调用 0、持仓/委托 0；工作台 `/workbench/` 与 `/api/workbench/overview` 当时 HTTP 200，但 overview `as_of` 曾为 `2026-11-06T00:00:00+00:00`（约 5 天前），`data_status` 仍为 ok。09:50:48 进入连续决策，09:50:52 起出现当日首次模型调用（先探针后策略）。账户层最先放行的是 513050.SH / 518880.SH（09:51:00 首条 `t0_chain`：`data_age_seconds≈117.3`，门禁 180 秒，`continuous_1m_bars=19`）。513100.SH 仍拒绝至 10:47:19：09:50:54–10:31:54 共 395 次 `missing visible minute bars`（`reason_class=NotFresh`，`missing_fields.bars=no visible minute bars at as_of`），10:32:00–10:47:19 共 168 次 `incomplete minute features`（`WAIT_DATA_QUALITY`，连续 1m 根数从 1 增至 15，缺 `ret_15m` 所需 16 根）。10:47:25 起 513100 进入 `model_pending`。另有 513050 在 09:56:59 一次缺线、10:09:02 一次 `async model result expired or account changed`。控制审计当日 `data_reject` 共 565 条；路由目标全程 `minute_bars_1m->sina_etf`，当日审计未见 akshare。约 10:12 的账户健康已是放行后状态（当时已有策略 4 / 探针 2 / JEV 1），并不表示 513100 当时已够窗。11:21:49 最近一次策略成功；11:23:28 健康为「有效策略观望」，`last_blocker_class=global_cooldown`，当日请求 23（策略 15、探针 2、JEV 6），费用约 0.186 CNY，持仓/委托仍为 0，当时标的 518880.SH、连续 1m 112 根、数据年龄 82.7 秒（门禁 180 秒）。11:29:30 的 `intraday-health.json` 仍为 `global_cooldown`，最近标的已是 513100.SH（连续 1m 58 根，年龄 85.3 秒，`last_missing_fields` 空）。

> 2026-11-10 重启后恢复：150 既有代理与 `D:/trademind` 监督器已启动，重连 SSH 核验心跳新鲜、`halted=false`、日历已覆盖、休市等待；工作台与账户接口 HTTP 200。仍使用远程既有 HEAD `62b5be1` 加 dirty 文件，没有同步本地代码。操作与验证见 [Task11-10-01](../tasks/TASK-20261110-01-恢复150重启后服务.md)；本次不代表盘中模型或成交验收。恢复时有意未创建开机自启或计划任务，是否配置自启仍待用户决定。

> 2026-10-30 零成交 RCA：用户提供的多日证据已按资金档位整理至 [Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md)。当前主要记录原因是全账户冷却，旧档另有最小手限额障碍；近期 JEV 已参与，不能只归因全景门禁或市场差。后续见 [Task30-01](../tasks/TASK-20261030-01-零成交归因证据与原因遥测.md) 及其关联任务；本轮未连接150，未改运行或参数，Issue27-02不代关闭。

更新：2026-11-11。本文件只写当前状态——目标、能力、运行版本、限制、下一步入口。**历史过程与逐日记录不在本文件**：见 git 历史（本文件 2026-10-28 及之前的按日期版本）、[任务索引](../tasks/index.md)、[问题索引](../issues/index.md)、docs/research/。当前事实与 [system-overview](system-overview.md)（逻辑架构）、[code-map](code-map.md)（文件入口）、[flows](flows.md)（执行顺序）、[setup](setup.md)（环境）、[operations](operations.md)（操作）按职责分工，互不复制。

## 当前运行（150 主机，本机只开发不运行）

- 代码版本：150 正在运行 `code_version 62b5be1`（2026-10-28）并带有其自身 dirty 文件；本机 Git HEAD 为 `ffde753`（2026-10-29）并另有大量未提交、未部署的本地工程。两边均 dirty、未同步，不能把本机工作树或 `origin/main` 当作 150 已加载版本。
- 监督器：`scripts/run_local.py`，账户 `paper-t0-local-1k`，独立库 `D:\trademind\data\runtime\local-1k\trademind.db`，配置 `D:\trademind\config\t0-local.local.toml`（该文件在 150，本机仅有 example）。查状态必须带绝对路径与 `--config`，省略配置或从错误工作目录执行会落到 `paper-001` / `data/runtime/trademind.db` 并误报 `running=false`，详见 [operations](operations.md)。截至 11-11 盘中核验：`halted=false`，脱终端存活，健康已从早盘「行情不足」转为「有效策略观望」。
- **影子盘资金：5000 元档**（2026-10-29 生效，[Task28-01](../tasks/TASK-20261028-01-影子盘资金上调至5000.md)）——初始 5000 / 单证券 1000 / 总持仓 1500 / 最低现金 500（原冻结表 20%/30%/10% 比例语义不变）。账本 deposit 4000 留痕，重放对账守恒。**版本化新 run：五日观察口径自 2026-10-29 重新起算**。
- 模型费用红线：5 CNY/日、100 请求/日（资金调整不改变调用预算）。

## ETF 影子实验（当前主链，fast profile）

- 运行面：开盘自动预检（双模型真实探针）→ 盘中 paper → 收盘排空与日报；策略 900 秒刷新、300 秒全局评估冷却、预约制预算、Guard/OMS 硬门禁。
- 三只ETF观察池保留。已存历史价格在5000档无仓无冻结假设下均可达；当前报价、冻结、持仓变化后需逐快照重算，不能保证永久可达。证据见[Task30-03](../tasks/TASK-20261030-03-纸面账户最小手可达性与观察池分离.md)。
- 近期用户提供证据已有JEV参与，不能泛称全天决策为0或归因为策略保守。实施前全账户纯WAIT占窗机制已确认并有本地修复；该修复尚未加载到 150，真实效果待按冻结版本观察。[Issue27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)的分钟/failed根因及真实闭环仍独立开放。
- 历史成交与等待统计、逐日核查证据：见对应 Task 与 research，不在本文件维护。

## 本地工程与 150 加载边界（未加载不等于真实验收）

- [Task30-02](../tasks/TASK-20261030-02-重设计预算内全局模型冷却.md) / [Task30-03](../tasks/TASK-20261030-03-纸面账户最小手可达性与观察池分离.md)（离线工程 completed；行为见 [ARCH-029](../architecture/29-model-charge-window-paper-candidates.md)）：ETF 冷却只在模型调用路径预约，纯 WAIT 不占窗；`paper-t0-local-1k` 新仓复用可达性筛选，观察池与已有退出原路。未提交、未加载 150；运行效果、真实 paper、费用对账未验证，[Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md) 保持 open。
- [TASK-20261103-001](../tasks/TASK-20261103-001-收盘后核验冷却修复与分钟行情可观测性.md)（engineering-delivered）：分钟数据门禁逐轮审计（`decision/data_reject` 含 NotFresh/WAIT_DATA_QUALITY 细分、`decision/model_pending`）、上下文 `data_window`、health `last_blocker_class` 与冷却剩余秒数、日报已存意图/前置轮次双分母。2026-11-11 只读核验确认该分类已在 150 运行中的 dirty 代码上生效（当日 `control_audit` 已写出 NotFresh / WAIT_DATA_QUALITY 与 `missing_fields`，health 含年龄、门禁与连续 1m 根数），不必再同步 11-03。这只说明诊断可用，不代表正式验收或根因已关闭。冷却仍为 30-02 Design A/P2 语义（300 秒全局窗口未改）。`no actionable strategy conditions` 的逐条件真值遥测未实施。
- [TASK-20261108-002](../tasks/TASK-20261108-002-撮合与执行旁路P0快修.md) / [TASK-20261108-003](../tasks/TASK-20261108-003-行情有效性与估值质量快修.md)（engineering-delivered；部署与真实验收前保持此状态）：日线参考价/撮合补 `as_of`、共享 tick 格点量化、期货开空/平空显式拒绝、`execute_order` 离线实验门禁；以及行情按当前时钟复核、其他持仓估值走质量合格+可见+新鲜门。未 commit/push、未加载 150，不得视为已接受或已在真实 paper 生效。
- 双系统驾驶舱：本机工作树前端已将 `/#/cockpit` 作为默认入口（左右独立导航；旧 `/#/ops-stream` 等路径重定向到该壳）。[Task30-05](../tasks/TASK-20261030-05-流水线交互视觉原型与性能门禁.md) 仍 in_progress（浏览器视觉/性能门禁未验）；[Task30-07](../tasks/TASK-20261030-07-双系统独立导航与常驻总览壳.md)～[Task30-10](../tasks/TASK-20261030-10-双窗视觉对等与联合验收.md) 仍为 draft，[ARCH-027](../architecture/27-chinese-large-screen-workbench.md) 保持 implementing。左窗 trader 默认样例，真实接线归 [Task30-06](../tasks/TASK-20261030-06-跨项目只读适配与验收方案.md)（draft）。未部署到 150，不得将本机代码存在视为已接受。只读白名单 GET 与失败回退样例的工程边界见 Task30-05。

## 股票广域观察扩展（[ARCH-028](../architecture/28-stock-wide-observation-bounded-decision.md) implementing）

- **离线已交付**（Task27-02/03/05，completed 仅指离线工程）：观察池（4403 只候选，程序化剔除含逐标的淘汰原因）、新浪股票分钟源验证（单位=股；收盘 60 只 0 失败；盘中延迟 ≤1 分钟时点样本）、可交易性档案（affordable ≈17%、往返费用占比中位 2.87%）、`StockFastPipeline`（沪深主板+创业板，T+1；600000.SH 与 300750.SZ 全链 BUY Guard ALLOW 集成）、确定性选择器（持仓优先，≤1 候选/轮）、`paper-stock-1` 独立库幂等建账工具、`t0.stock_chain` 开关（默认关）。
- **运行接线未完成**：选择器生产调用点、候选/选择结果落盘、持仓并入选择器输入、股票分钟源运行路由、跨日退出（期限强平遇 T+1 的顺延策略）、两账户预算合计约束。
- **未验收**：完整交易时段连续质量（Task27-04 余项）、真实盘中决策（Task27-06）、沪市交易规则官方原文 source_url。
- **当前未启用**：`stock_chain=false`、`paper-stock-1` 未建账——启用为显式步骤，未经用户指示不执行。此项为后续可选轨道，不是当前绑定门禁。

## 中文大屏工作台 V2

代码返修完成（[Task26-16](../tasks/TASK-20261026-16-修复工作台V2独立复核缺陷.md)）；实际页面/交互/回退验收未完（26-04、26-08~12、26-14 in_progress，Owner Codex；[ARCH-027](../architecture/27-chinese-large-screen-workbench.md) implementing）。150 当前仍服务既有静态包，不等于本机驾驶舱工作树。

## 机制与兼容（细节见对应文档，本文件不复制）

- V1 股票 paper/replay 兼容保留（ARCH-001 implementing、TASK-23 前瞻观察）；多市场扩展 ARCH-011 implemented；旧默认链（flash 候选→JEV 审核→flash 汇总）保留对照。
- 安全红线（永久）：不接实盘、不做空、不加杠杆、不静默换型号/stub/单模型开仓、禁止未来数据、账本一致性与 Guard/OMS 授权不变、未经用户口令不部署 150、候选增多不等于收益改善。
- 文档治理（[Task29-01](../tasks/TASK-20261029-01-文档治理改造批次.md)）：第 1～7 项已完成（2026-10-29 提交 `ffde753`：夜间规则适用范围、current-status 重写、索引去重与校验器、交付层级、参数与永久规则分离、任务模板分级、索引归档分离）；第 8 项部分完成（最重组已收敛，余 7 组三文件重复待核对）。任务保持 in_progress，不宣称整批关闭。

## 下一步入口

- **不要再同步 11-03**：分钟拒绝分类已在 150 上使用。盘中不重启监督器，不换付费源，不擅自加载 11-08 P0。
- **仍待用户批准的下一步**：收盘后可继续只读观察免费 `sina_etf` 的开盘质量（尤其 513100.SH 在 09:30–10:31 无可见 1m 线、10:32–10:47 根数从 1 增到 15 才放行）。若要冻结本机其他未部署修订（11-08 P0，以及仍标明未加载 150 的 30-02/30-03 等）再同步到 150，须收盘后单独授权，不在盘中进行。版本钉扎观察仍针对 [Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md) 与 [Issue27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)。在完成这次观察之前，不以股票 [Task27-06](../tasks/TASK-20261027-06-盘中验收与对照.md) 为当前主路径。
- 股票 [Task27-04](../tasks/TASK-20261027-04-验证股票行情与可交易性.md) 收口余项与 Task27-06、启用 `stock_chain` 仍为后续可选轨道；当前 `stock_chain=false`、`paper-stock-1` 未建账，未经用户指示不启用。
- 2026-11-10 恢复时有意未创建开机自启或计划任务；是否配置自启仍待用户决定。本文只记录该缺口，不新增自启。
- Issue27-02 与 Issue30-01 保持 open；11-03 诊断已加载、11-11 盘中时间线与其余离线工程都不代关闭。
- [任务索引](../tasks/index.md) · [问题索引](../issues/index.md) · [逻辑架构](system-overview.md) · [Roadmap](../roadmap.md)
