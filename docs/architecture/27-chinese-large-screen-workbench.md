---
id: ARCH-027
type: architecture-change
title: 中文大屏工作台与常驻只读观察服务
status: implementing
created: 2026-10-26
updated: 2026-10-26
related_tasks: [TASK-20261026-02, TASK-20261026-03, TASK-20261026-04, TASK-20261026-05, TASK-20261026-06, TASK-20261026-07, TASK-20261026-08, TASK-20261026-09, TASK-20261026-10, TASK-20261026-11, TASK-20261026-12, TASK-20261026-13, TASK-20261026-14]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-027 中文大屏工作台与常驻只读观察服务

## 2026-10-26 返修设计与证据（最新）

[Task26-16](../tasks/TASK-20261026-16-修复工作台V2独立复核缺陷.md)已修复工程边界：应用作用域显式cfg；账户过滤的只读Context/日报；完整响应与金融口径；observer配置身份摘要+启动实例ID的可管理收养/停止；Windows优先退出实际服务、仅自有Popen树可强制清理；原始快照年龄与请求世代隔离。新增execution/observer_identity.py只计算非凭据展示配置摘要，market/profiles仅增加安装资源路径回退。没有新增交易授权、第二writer或真实模型调用。

普通/收养observer与同账户受控paper Pipeline并存及只读约束已验证，详见[返修报告](../research/20261026-workbench-repair-verification.md)。完整视觉、回退及现有运行切换未验，继续implementing；下方Task26-15缺陷说明为修复前历史，首次implemented声明仍无效。

## 2026-10-26 独立复核退回implementing

[Task26-15](../tasks/TASK-20261026-15-独立复核工作台V2交付.md)与[独立报告](../research/20261026-workbench-independent-review.md)：前端工程/主题和部分只读代码已存在，但完整架构未通过验收。observer CLI未分发、显式cfg未绑定API、真实Context字段与曲线信封不匹配、账户/日报隔离与财务语义有反例。Python726通过不能替代这些原始边界。Task26-04～14退回in_progress、Issue26-04～10重开，保留原Owner与历史；26-03工程骨架保留completed。

本记录是在同次实施中纠正错误的implemented声明，不是新增已实现后的架构变化，也不重写交易ARCH历史。最终实施结果为首次自验历史；最新真实状态为implementing。真实observer与有数据图表/资金口径/隔离/请求生命周期修复独立通过后，才可再次评估implemented。

## 状态

implementing（2026-10-26）。账户绑定/隔离、曲线/财务契约、observer入口与请求生命周期的代码返修及隔离验证见Task26-16；实际页面视觉、目标运行实例切换及回退仍待26-14。下方首次自验记录为历史，不代表当前完整验收。不改变交易决策/资金/风控授权。


## 背景与目标

用户确认深色科技驾驶舱、电脑大屏为主，中文名称优先并查看具体曲线。当前单HTML页面无图表/分层导航，GET依赖交易Pipeline，监督器paper/serve切换导致交易期间网页不可持续访问。详见[需求](../requirements-workbench-v2.md)与[Task26-02](../tasks/TASK-20261026-02-规划中文大屏交易工作台重构.md)。

目标：建立组件化展示层、纯只读快照/曲线契约、交易期间持续可读的观察进程，同时保留现有paper writer独占和旧操作入口。

## 非目标

实盘、手动买卖、新风险决策源、改模型/费率/预算/冷却、云平台/公网、多租户权限平台、后台新采集器、WebSocket/SSE首期强制化、读服务写账户或迁移。动态重生成历史模型判断不属于展示。

## 变更前（代码事实）

- FastAPI `api/app.py`提供旧API/HTML；`get_pipeline→init_app→Pipeline→ensure_account`承担交易初始化。
- 当前账户GET逐标的reference_price，其他读接口有不带account_id的表查询；不能作为新大屏高频只读层。
- `auto_paper.py`一个child在serve/paper之间切换；Windows本地入口由ARCH-026覆盖。
- 浏览器单文件内联逻辑，5秒轮询；中文名仅ETF映射、订单/意图部分展示，无曲线API。

## 方案与技术选择

### 1. 展示工程

- 新增`frontend/`：Vue 3 + TypeScript + Vite，ECharts按需引入，CSS tokens与公共组件；npm锁定依赖，执行时核验Node/包支持范围，不升级Python依赖。
- 生产`vite build`静态资源由FastAPI同源服务；Node只用于构建/开发，不增加生产Node进程或Vite preview常驻服务。
- 产物建议`api/static/workbench/`，保留原`static/index.html`。hash导航避免覆盖API/日报URL；构建清单/递归package_data和wheel验收必须落实。
- 无CDN、外部字体/统计请求依赖；token只内存；金额Decimal字符串由后端确定性计算，图表浮点只作展示。

### 2. 只读观察API

- 建议`api/read_models.py`（快照/分页服务）、`api/workbench_routes.py`（路由）、`api/workbench_charts.py`（可见曲线重建）。名称目录由独立展示服务提供，不修改交易身份。
- SQLite `mode=ro` + `query_only`，短一致性读事务；不使用会迁移/创建账户的Database初始化路径。不把immutable用于活跃WAL库读取，以免忽略当前WAL；具体连接策略由测试验证。
- 服务端固定配置账户。订单/Fill/意图/Context/策略/费用均按账户关系过滤，不接受前端随意切账号；多账户无归属的旧记录注明不可归因，不错算为本账户。
- 读服务读取已有库、日报/健康文件；缺表/未知Schema/缺库安全失败，绝不修复或写入。JSON状态文件原子读取/部分缺失时保留最后成功快照及年龄。
- 新路由`/api/workbench/`与旧API分开，字段/状态和能力见需求主文档；不依赖旧`get_pipeline`。接口不能以UI轮询次数驱动免费行情或收费模型调用。

### 3. 可见曲线与派生数据

- 首期从已存Context分钟事实/账本事件/日报重建有界图表视图；按可见时点先过滤再选择修订；数据缺口/局部覆盖显式返回。
- 股票/ETF价格曲线与账户权益不是同一个数据流。权益用真实资金事件和当时可见估值；入金不算收益，模型预留不当账单。
- 读服务可做有界内存缓存（按库/账户/版本/范围/as_of键、字节/条数上限），过期后重读本地；缓存不能跨账户或误用未来数据。
- 首期不新增持久数据库/采集调度。若性能证据要求索引/物化视图或新持久档案，必须在本记录写明写入Owner、迁移、容量/保留、版本和失败恢复，再实施；不能由GET默默写主库。

### 4. 常驻观察服务与交易进程分离

- 新观察app/factory仅挂只读路由和新前端，不构造Pipeline/LLM/JEV，不拥有账户writer租约。
- 新factory不复用旧`daily_report_lifespan`或其后台报告生成循环；日报读取既有生产者已生成产物。只读连接必要的SQLite WAL读锁/共享内存元信息不等同业务写入，但不得以此绕过业务库/账户/报告的零写入要求。
- 本地supervisor有两个独立职责：常驻observer子进程、按交易时段运行的paper writer子进程；端口8321仅observer持有，paper不再通过serve抢该端口。PID/状态应分别登记，不能沿用单child假装两个角色。
- observer崩溃只影响可视化，不能自动停止/重启正常交易worker；交易异常/锁定在网页明确显示，网页能打开不能把halted改false。停止observer和停止整个自动模拟有不同明确定义。
- 保留旧`serve`/旧单页兼容路径与后端控制鉴权；旧交易型serve仍受单writer互斥，不能在paper运行时为了回退而并行启动。
- 新observer第一版声明`controls_available=false`；高级面板显示只读并解释原操作流程。现有控制能力若未来需要接入新服务，另设计授权/幂等/结果未知的控制通道，不在本次为了按钮能点增加IPC或第二Pipeline。
- 真实启停/部署按已有授权及原Owner协调，不因文档创建自动进行。Windows后台启动隐藏窗口，遵守项目现有PID校验/排空/恢复行为。

## 核心设计决策与取舍

1. 大屏炫酷与信息简洁通过视觉层次/渐进详情实现，不减少必要风险/数据年龄信息。
2. 中文名是展示别名，代码是内部键，避免改动交易授权和历史身份。
3. 新只读层先于页面高频刷新与常驻服务，避免把前端重构变成额外行情/模型负载。
4. 首期轮询+取消/退避即可，不额外引入消息总线/WebSocket进程；连续网页来自服务生命周期，而不是动画假实时。
5. 初期新服务只读，保留旧授权操作流程；可查看优先于增加控制权限。
6. 图表库只负责渲染，不计算权威盈亏/模型胜率。缺数据时空态优于漂亮假曲线。

## 模块、接口、数据与所有权影响

| 模块 | 规划范围 | 所有权/约束 |
|---|---|---|
| frontend | 六页+公共请求/图表/状态组件 | 26-03骨架，页面与26-13按目录认领 |
| 名称目录 | 现有白名单/观察元数据的展示DTO | 26-04，不改instrument_id或资格 |
| 只读API | 新路径/快照/账户隔离/分页 | 26-05，旧app注册入口串行协调 |
| 曲线 | bars/events/account-series | 26-06，不从GET采集/持久化行情 |
| 生命周期 | observer/paper双职责PID与端口 | 26-07，须与22-01/13-10/16-02协调 |
| 打包交付 | 前端静态包/manifest/旧入口回滚 | 26-14，无未经授权部署 |

交易事实所有权仍归原行情/模型/Guard/OMS/账本，前端不写交易数据库、不修复账户、不把UI状态反写为交易授权。读服务的缓存/格式化属于展示派生，不可作为交易数据源。

## 部署与运行影响

新增Node构建前置和静态构建资源；生产仍本机FastAPI同源、localhost默认，无新公网服务。新增独立观察进程；账户writer数量仍为1。Docker入口保留兼容，首期本地Windows优先，容器未验证不得声称也已适配。

## 测试范围与通过门槛

- 组件/类型/金额语义、中文身份、请求取消/乱序/退避、图表时点/事件分类、空态/错误/陈旧/未知、键盘/减少动效。
- 只读零副作用：禁provider/ensure_account/迁移/writer租约、库不存在、WAL活跃、混合账户、未来修订、价格缺口、有界查询。
- 运行隔离：observer/paper同时存活、网页持续可读、无第二writer、重启观察不影响交易、原排空/停止/异常恢复不退化。
- 构建打包/干净wheel可读静态资源、资源路径/旧API/报告兼容、受控回退。三种大屏截图与真实性能证据。

## 实施中的设计变化
实施与规划的一致，以下为实际选定/调整项：
- Node 便携版 v22.23.4 落地 D:/tools/nodejs（免管理员、不改系统 PATH，见 setup.md）；Vite 7 + Vue 3.5 + TS 5.9 +
  ECharts 6（按需注册 Canvas 渲染），hash 路由。规划表中「六页」实际为 7 页（需求§7.1–7.7：总览/行情/资产/策略/
  交易记录/日报/运行中心），运行中心按需求§7.7 独立成页。
- 静态包目录 src/trademind/api/static/workbench/，app.py 挂载 StaticFiles(html=True)，/workbench 重定向路由必须
  定义在 mount 之前（否则被 mount 遮蔽 404——实施中发现并修复）。
- 只读连接：WorkbenchReadStore 在构造时 Path.resolve() 相对 sqlite URL 再 as_uri（实施中发现相对配置路径
  as_uri 抛 ValueError 导致 series 500——修复）；mode=ro + PRAGMA query_only 双重只读。
- pyproject package-data 补入 api/static/workbench/*.html 与 assets/*（否则 wheel 漏装 V2 产物）。
- observer 生命周期落地为 observer-state.json（pid/running/started_at/heartbeat 5s），Supervisor 跨重启收养活实例。
- 前端请求层按 26-13 落地为 client/workbenchApi/useResource/usePolling/usePreferences 五件套，页面禁止自行 fetch；
  轮询频率按资源分配（5–60s），隐藏暂停、退避封顶 30s。


## 首次实施结果与验证（历史，已由 Task26-15/16 修订）
首次自验曾报告26-14集成门槛通过；独立复核发现缺陷，后续代码返修已验证，当前完整验收仍未完成：
- 构建：npm run build 成功，产物入 src/trademind/api/static/workbench/（主包 104 kB/gzip 41 kB，ECharts 分包
  591 kB/gzip 201 kB，7 页面懒加载 chunk）；干净 wheel 含 V2 产物（package-data 已补）。
- 测试：全量 pytest 725 passed（test_auto_paper 21 / test_local_lifecycle 32 / test_observer_app 6 为本批新增/重构）；
  前端 vitest 30 passed；vue-tsc --noEmit 0 错。
- 真实 serve HTTP 冒烟（runner serve，端口 8321）：/workbench/、静态 assets、overview/instruments/runtime/alerts/
  account/series/bars/reports/activity/strategies 全 200；期间发现并修复两处真实缺陷（html=True 404、相对路径
  as_uri 500，见「实施中的设计变化」）。
- 视觉证据：11 张真实页面截图（7页@1920x1080 + 总览 1440x900/2560x1440/@1.25 缩放）
  docs/research/20261026-workbench-screens/live-*.png。
- 只读零副作用：test_observer_app.py 断言 GET 不创建/迁移数据库；observer_app 源码无 pipeline/ensure_account 依赖。
- 回滚：legacy serve 首页与 /api/* 未改动，旧 index.html 保留；删除静态目录即回退页面，只读API无副作用。
未验（不属于工程门槛）：真实盘中 supervisor 托管 observer、真实行情五日观察、盈利能力。


## 当前事实同步
本批已同步：docs/project/system-overview.md（observer 双进程事实与只读API层）、code-map.md（新模块与前端树）、
operations.md（observer 启停/端口 8321/陈旧与部分数据语义）、setup.md（Node 便携版与前端构建）、README.md（工作台V2
入口）、current-status.md（交付与未验边界）、roadmap.md（阶段注记）。后续变化按 Task 契约继续同步。


## 关联内容

需求：`docs/requirements-workbench-v2.md`；实施入口：Task26-02；Issue26-04～10；既有ARCH-006/017/026和本地运行Owner仍有效。
