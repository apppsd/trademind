## 2026-11-11 重启 150 监督器加载新代码

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-07](TASK-20261111-07-重启150监督器加载新代码.md) | completed / Cursor Grok | TASK-20261111-06 | 收盘后排空 pid 6004 并启动 pid 8504；工作台与账户接口正常；不是盘中成交验收 |

## 2026-11-11 在 150 拉取已推送代码

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-06](TASK-20261111-06-在150拉取已推送代码.md) | completed / Cursor Grok | 无 | 150 磁盘快进到 `502cc9d`；监督器 pid 6004 未重启，新代码尚未进入进程 |

## 2026-11-11 拆开日报、决策与风控长函数

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-05](TASK-20261111-05-拆开日报决策与风控长函数.md) | completed / Cursor Grok | 无 | 仅工程：`generate_report`、`_run_decision`、`guard.check` 按原行为拆开；相关测试通过；未部署 |

## 2026-11-11 拆开盘中健康读取

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-04](TASK-20261111-04-拆开盘中健康读取.md) | completed / Cursor Grok | 无 | 仅工程：`read_intraday_health` 拆到 `execution/intraday_health.py`，原导入名和返回字段不变；94 项相关测试通过；未部署 |

## 2026-11-11 运行中心未成交原因

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-03](TASK-20261111-03-审阅并接通运行中心未成交原因.md) | completed / Cursor Grok | 无 | 仅工程：工作台运行状态改读 `read_intraday_health`，运行中心展示已记录的未成交原因和逐条条件；不改下单，未部署 |

## 2026-11-11 功能地图

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-02](TASK-20261111-02-梳理功能地图.md) | completed / Cursor Grok | 无 | 仅文档：当前功能对照到代码路径，区分 150 主链、本机未加载、未接线部分；未改交易代码 |

## 2026-11-11 未使用导入与死函数清理

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261111-01](TASK-20261111-01-清理未使用导入与死函数.md) | completed / Cursor Grok | 无 | 仅离线工程：删除 AST 确认未引用的导入，以及目录模块两个未调用私有函数；主基线 910 项通过；不改交易行为，未部署 |

## 2026-11-10 150 重启后服务恢复

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261110-01](TASK-20261110-01-恢复150重启后服务.md) | completed / Codex | 无 | 150代理与监督器恢复，工作台HTTP200、账户正确、只读体检退出0；运行接线，不同步代码 |

## 2026-11-08 撮合与执行旁路 P0 快修（Phase 0 Batch B）

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261108-002](TASK-20261108-002-撮合与执行旁路P0快修.md) | engineering-delivered / Claude Code | 无 | B04 日线漏传 as_of（pipeline 参考价+paper 日线撮合）、B07 共享 tick 格点量化（新增 execution/price_quant.py，两撮合器统一）、B06 期货开空/平空语义显式拒绝（拒绝零账本残留）、B05 execute_order 离线实验门禁+order_id 幂等+仅 NEW 可执行；新增 tests/test_paper_execution_p0.py（16 项）；主基线 897 项全绿；Codex 二轮 REWORK（2 MAJOR+3 MINOR）已修，卡内 R1 返工记录含隔离差异与复现命令；工程交付，未 commit/push/碰 150 |
| [TASK-20261108-003](TASK-20261108-003-行情有效性与估值质量快修.md) | engineering-delivered / Claude Code | 无 | B01 行情年龄按旧 as_of 复核：JEV 决策期限改 min(180s,配置 quote_max_age_seconds)（fast_decider/fast_pipeline），执行前按当前时钟复核参考价并以 `reference_price_recheck` 审计行保留决策快照（pipeline.py）；B02 其他持仓估值统一"质量合格+可见+新鲜"入口，质量拒绝窗口不进权益、标 valuation_incomplete、Guard 拒新风险（builder.py，replay 无豁免）；新增 tests/test_quote_valuation_p0.py（13 项）；Codex 一轮复审 R1 返工（1 MAJOR+3 MINOR）已修：审计决策快照冻结自模型消费上下文（异步=模型轮 source）、复核拒绝携带被拒报价非 null、补保护退出豁免/replay 质量门/同报价纯时钟老化 6 项测试；主基线 910 项全绿（收集原文+EXIT 为证）；适配 002 卡 B04 未绑定调用测试（断言语义不变）；工程交付，未 commit/push/碰 150 |

协议照 trader 侧 383：六项 finding 均先实读代码证实属实后才修（证据 file:line 入卡）。B03/B12 归蓝图手术批未碰；主链行为除六项修复外零变化（002 撮合与执行、003 行情与估值）；ruff/format 仓库无 dev 配置且工具未安装，如实记卡（002/003 两卡同）。

## 2026-11-03 收盘后冷却与分钟行情复核

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261103-001](TASK-20261103-001-收盘后核验冷却修复与分钟行情可观测性.md) | engineering-delivered / Claude Bot | TASK-20261030-02、TASK-20261030-03 | 逐文件版本矩阵；冷却复用30-02无需重写；分钟数据门禁诊断（data_window/data_reject/audit 轮次）+health四桶+日报双分母；Codex FAIL 后返工 R1–R4（前置分母账户隔离、NotFresh 子因贯通公共链、t279u 窗口/条件证据补完、有效行情门槛）；round3 FAIL 仅 R2 后收口：NotFresh/数据拒绝 reason 封闭白名单（raise点去动态化、public/health/日报全链 classify、合成正文探针回归），离线277项通过；2026-11-11 只读核验确认分类已在150运行并写入 health/control_audit，不必再同步11-03；正式验收未做，11-08 P0仍未加载 |

关联[Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md)与[Issue27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)。2026-11-03 实施当日未连 150；2026-11-11 只读核验确认分钟拒绝分类已在 150 运行中的 dirty 代码上生效（当日 `data_reject` 已按 NotFresh / WAIT_DATA_QUALITY 与 `missing_fields` 落盘），不必再同步 11-03。这不构成正式验收，也不表示 11-08 P0 或仍标明未加载的 30-02/30-03 已在 150 上。资金/风险/FeeModel未改、不造单、未commit/push；其余代码同步和重启仅限收盘后另行授权，盘中不重启，无GitHub release路径。

## 2026-10-30 双系统独立导航与视觉对等（仅规划）

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261030-07](TASK-20261030-07-双系统独立导航与常驻总览壳.md) | draft / 待认领 | TASK-20261030-04 | 默认双总览、50/50常驻、左右独立导航与历史/深链 |
| [TASK-20261030-08](TASK-20261030-08-trader左窗密集样例子页.md) | draft / 待认领 | TASK-20261030-07 | trader密集Vue SAMPLE五子页；原型只读参考，不做iframe |
| [TASK-20261030-09](TASK-20261030-09-TradeMind右窗复用页面与子路由.md) | draft / 待认领 | TASK-20261030-07 | 复用TM七页为右窗子路由，驾驶舱内SAMPLE注入与链接隔离 |
| [TASK-20261030-10](TASK-20261030-10-双窗视觉对等与联合验收.md) | draft / 待认领 | TASK-20261030-08、TASK-20261030-09 | 双侧密度/美观对等、浏览器与工程联合验收 |

关联[Issue30-03](../issues/ISSUE-20261030-03-双系统大屏导航与内容失衡.md)，Claude认领入口与顺序见[实施简报](../research/BRIEF-20261030-dual-ops-cockpit-claude.md)：07→08→09→10。Codex本轮只规划；共享文件先向05 Owner交接，保留现有diff；不依赖06真实接线，不改05/06状态、trader或交易代码。ARCH-027仍implementing。

## 2026-10-30 统一运维驾驶舱草案（单壳双窗，仅文档）

| Task | 状态 / Owner | 依赖 | 交付范围 |
|---|---|---|---|
| [TASK-20261030-04](TASK-20261030-04-流水线前端盘点与只读契约草案.md) | completed / Claude | 无 | ops.v1只读字段、GET候选与双窗归属文档已冻结（Claude接管并补研究页白名单缺口）；未运行接线 |
| [TASK-20261030-05](TASK-20261030-05-流水线交互视觉原型与性能门禁.md) | in_progress / Claude | TASK-20261030-04 | SAMPLE双窗原型+只读白名单GET适配（失败按窗回退样例）已实现；浏览器视觉/性能门禁待验 |
| [TASK-20261030-06](TASK-20261030-06-跨项目只读适配与验收方案.md) | draft / 待认领 | TASK-20261030-05 | 两系统只读适配接入同一壳及验收；待授权 |

[统一驾驶舱决策](../research/RESEARCH-20261030-cool-dataflow-frontend.md)：单壳同时展示trader与TradeMind，不是两个独立网站；默认扩展TradeMind Vue工作台。trader345/346、Issue156已存在，本次修订因写权限仅限TradeMind，以[定向补丁](../research/trader-cool-dataflow-frontend-docs.patch)交接，已由父会话应用。保留ARCH-027 implementing与trader ARCH-030 proposed；编码等待后续确认，不改代码/资金/风险，不SSH/部署/重启/commit/push。

## 2026-10-30 多日零成交 RCA 与后续工程

| Task | 状态 / Owner | 依赖 | 范围 |
|---|---|---|---|
| [TASK-20261030-01](TASK-20261030-01-零成交归因证据与原因遥测.md) | completed / Codex | 无 | 文档归因证据与标准原因直方图口径；仅工程文档交付 |
| [TASK-20261030-02](TASK-20261030-02-重设计预算内全局模型冷却.md) | completed / Codex Bot / Wang desktop | TASK-20261030-01 | Design A调用路径预约、纯WAIT不占窗口；仅离线工程 |
| [TASK-20261030-03](TASK-20261030-03-纸面账户最小手可达性与观察池分离.md) | completed / Codex Bot / Wang desktop | TASK-20261030-01 | 复用诊断与纸面新仓筛选；保留观察池/已有退出，不改资金风险 |

关联 [Issue30-01](../issues/ISSUE-20261030-01-纸面长期零成交冷却与最小手买不起.md)。Task30-02/03 串行协调共享代码文件；不覆盖旧 Task16-04/12-06 的历史验收，不授权连接150、重启、推送或部署。

## 2026-10-29 文档治理改造（审阅八项，执行中）

| Task | 状态 / Owner | 内容 |
|---|---|---|
| [TASK-20261029-01](TASK-20261029-01-文档治理改造批次.md) | in_progress / Claude Code | 八项处置：1～7 已完成（2026-10-29 `ffde753`：参数分层、模板分级、索引归档等）；第 8 项部分（最重组已收敛，余 7 组三文件重复待核对） |

## 2026-10-28 文档同步与资金调整

| Task | 状态 / Owner | 范围 |
|---|---|---|
| [TASK-20261028-02](TASK-20261028-02-同步股票扩展当前事实与验收文档.md) | completed / Codex | 股票扩展代码入口、任务、架构与未验边界同步；仅文档 |

股票批次按 Task27-04 补足验证 → Task27-06 检查运行接线并验收继续；Task27-05 的 completed 仅限离线工程。最新边界统一见 [current-status](../project/current-status.md)。

## 2026-10-28 影子盘资金上调（用户授权，已完成）

| Task | 状态 / Owner | 内容 |
|---|---|---|
| [TASK-20261028-01](TASK-20261028-01-影子盘资金上调至5000.md) | completed / Claude Code | 1000→5000 完成（10-29 执行）：入金 4000 账本留痕、监督重启 pid 1048796、overview 现金 5000 验证 ✓ |

## 2026-10-27 A股广域观察与决策扩展批次（Codex 拟案，Claude Code 立项）

用户目标："先扩大观察池，再让合格股票进入模拟决策"——覆盖更多 A 股且不把全市场逐只送模型。共同边界：股票独立账户示例参数见 ARCH-028；ETF 后续资金调整另归 Task28-01，不混用两账户参数；不自动部署 150；候选增多不作收益改善主张；三 ETF 链路行为不变；现有股票分钟数据未验证，追加代码本身不完成这些 Task。**上游阻断**：[Issue26-27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)（JEV 全景就绪门禁）未定位前，Task06 盘中验收无法完成。夜间 MCP 委托工具不可用期间，经用户指示可由会话执行者认领。

| Task | 状态 / Owner | 依赖 | 工作与验收重点 |
|---|---|---|---|
| [TASK-20261027-02](TASK-20261027-02-规划A股广域观察与决策扩展.md) | completed / Claude Code | 无 | 规划：范围/数据源/筛选规则/调用上限/独立账户 → [ARCH-028](../architecture/28-stock-wide-observation-bounded-decision.md)（implementing，规划任务已完成）；三 ETF 链路不变 |
| [TASK-20261027-03](TASK-20261027-03-建立股票发现与只读候选池.md) | completed / Claude Code | 02 | 观察池落地：stock_discovery+akshare 适配+扫描脚本；本机初次扫描记录保留；后续样本口径见 Task04；正式重复运行归 150 |
| [TASK-20261027-04](TASK-20261027-04-验证股票行情与可交易性.md) | in_progress / Claude Code | 03 | 新浪股票分钟源已验证（60 只收盘线 0 失败、单位=股）；affordable 10/60、费用占比中位 2.87%；10-28 已补盘中时点样本；余完整时段质量证据与官方规则 source_url |
| [TASK-20261027-05](TASK-20261027-05-接入有界候选决策与隔离模拟盘.md) | completed / Claude Code | 04 | 离线全链落地：StockFastPipeline（T+1 板块 profile）+ 确定性选择器 + `paper-stock-1` 独立库建账 + stock_chain 配置开关（默认关）；600000.SH BUY 100 股 Guard ALLOW 集成通过；创业板 Guard 注入已补；选择器运行接线、预算合计与跨日退出仍待验 |
| [TASK-20261027-06](TASK-20261027-06-盘中验收与对照.md) | pending / 待认领 | 05 | 盘中验收与对照；无自然机会如实记未验，不制造成交 |
| [TASK-20261027-07](TASK-20261027-07-部署股票观察批次到150并验证.md) | completed / Claude Code | 05 | 150 拉取 1e47d39、pytest 849 门禁、监督切换 391268、身份/版本/halted/overview 验证；股票链开关未启用 |

## 2026-10-27 审查修复批次

| Task | 状态 / Owner | 范围 |
|---|---|---|
| [TASK-20261027-01](TASK-20261027-01-修复夜间二轮审查缺陷.md) | completed / Claude Code | 修复 Codex 审查的四处夜间二轮缺陷（[Issue26-27-01](../issues/ISSUE-20261027-01-夜间二轮四处运维缺陷.md) resolved）：备份原子化+重试、代理脱敏、告警后置+上限+终态、看门狗去重 |

## 2026-10-26 夜间自主批次（Claude Code，用户授权自实施+自评审）

目标：交易系统达到可持续日常使用状态并在 150 就绪次日交易。用户授权：自发布 Task/Issue→自实施→自评审→评审通过后 push GitHub 并在 150 切换启动；验收门禁=150 就绪可正常进行 10-27 交易。

| Task | 状态 / Owner | 范围 |
|---|---|---|
| [16-08 二轮](TASK-20261016-08-修复快速链买入数量的费用预留.md) | completed / Claude Code | 已有仓位增仓累计目标修复（Issue16-05 resolved）；全量783通过 |
| [TASK-20261026-22](TASK-20261026-22-夜间二轮运维韧性代理预检自动备份晨间体检.md) | completed / Claude Code | 代理存活预检+当日自动备份+晨间体检脚本；部署待二轮切换验证 |
| [TASK-20261026-23](TASK-20261026-23-夜间二轮无人值守告警可选webhook通知.md) | completed / Claude Code | 可选webhook告警（默认关闭）+看门狗脚本；钉钉真实投递待用户配置 |
| [TASK-20261026-24](TASK-20261026-24-夜间二轮开源借鉴扫描.md) | completed / Claude Code | 开源借鉴扫描：采纳4项/暂缓2项（待五日样本）/不采纳3项；笔记见 research |
| [TASK-20261026-21](TASK-20261026-21-夜间批次自评审16-08修复对抗性复查.md) | completed / Claude Code | 16-08 diff 对抗性复查与边界补测：放行部署；明建议 Codex 复核 |
| [TASK-20261026-20](TASK-20261026-20-夜间批次部署推送GitHub并在150执行安全切换.md) | completed / Claude Code | push→150 拉取（1f4cd06）→pytest 门禁→切换完成：supervisor 311984、账户 paper-t0-local-1k、就绪 10-27 交易 |
| [TASK-20261026-18](TASK-20261026-18-核对150主机运行实例并准备安全切换.md) | completed / Claude Code | 只读核对+授权切换均完成；第4项验收由 26-20 执行通过 |

## 早期活动任务（V1 时代，仍 in_progress/pending）

| Task | 名称 | Owner | 状态 | 说明 |
|---|---|---|---|---|
| [TASK-20261007-23](TASK-20261007-23-完成模拟观察与首版交付.md) | V1 模拟观察与首版交付 | 待认领 | in_progress | 真实模型/行情与前瞻观察待验收，依赖 22、30～37 |
| [TASK-20261007-55](TASK-20261007-55-单模型影子盘独立验收.md) | 完整双模型模拟盘启动验收 | Codex | in_progress | 已认领有界启动检查；真实能力门禁未过不启动持续观察 |
| [TASK-20261007-56](TASK-20261007-56-单模型五日观察.md) | 五交易日影子观察与复盘 | 待认领 | pending | 依赖 TASK-20261009-09；与 fast 链五日观察口径见 ARCH-006 |

TASK-44/47/48 为早期规划条目，其最小需求已并入完成项，不再单列；TASK-24～27 为 M5/M6 可选扩展（pending），不阻塞当前实验。

## 历史归档

2026-10-26 及更早批次（稳定化排查、工作台 V2 历史、10-25/15/14/13 修复与验收、V1 队列、旧 T0 工程队列等）已迁至 [archive.md](archive.md)——历史快照，状态以各 Task 文件为准。

2026-10-30 认领时：TASK-20261030-02 / TASK-20261030-03 已由 Codex Bot / Wang desktop 认领，in_progress；用户授权先02后03，仅本地工程实施与离线测试。

2026-10-30 收口：30-02先95项通过再实施30-03，最终六套件148项通过；两任务completed仅指工程交付，150加载、真实paper和费用对账未验。详见两Task与ARCH-029。
