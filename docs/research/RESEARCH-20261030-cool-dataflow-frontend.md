---
type: research
title: 统一运维驾驶舱 Unified Ops Cockpit 规划
status: draft
created: 2026-10-30
updated: 2026-10-30T15:51:49+08:00
related_tasks: [TASK-20261030-04, TASK-20261030-05, TASK-20261030-06]
---

# 统一运维驾驶舱 Unified Ops Cockpit（事实 / 决策 / 规划）

## 2026-10-30 用户反馈后的规划修订（未实施）

当前双窗只存在于运行流，顶层导航与业务内容仍偏TradeMind，源码证据见[Issue30-03](../issues/ISSUE-20261030-03-双系统大屏导航与内容失衡.md)。新规划以默认双总览、左右常驻独立导航、双侧内容与视觉对等为首发门槛；左侧选择密集Vue SAMPLE卡片，trader原型只读参考，右侧复用七页并隔离样例资源/窗内链接。详细合同与07→08→09→10顺序唯一见[Claude简报](BRIEF-20261030-dual-ops-cockpit-claude.md)。本段替代下文旧规划中“运行流作为默认入口”的建议，不改写其历史Checkpoint。05仍in_progress、06仍draft、ARCH-027仍implementing；Issue30-02的双源真实接线边界不变。本轮Codex只写文档，无实现或运行操作。

**2026-10-30 用户架构决定（via Trade Ops）：一个统一运维驾驶舱 Unified Ops Cockpit，单一壳同时展示左侧 trader / 右侧 TradeMind，支持分屏，共用酷炫管线/粒子视觉语言。禁止拆成两个独立网站。** 本轮只更新规划；编码等待后续确认。Wang仅文档工作，无服务启动、HTTP探测、SSH或150现场确认；新增能力未实现。
关联[Issue30-02](../issues/ISSUE-20261030-02-跨项目流水线观测缺少统一只读语义.md)、[Task04](../tasks/TASK-20261030-04-流水线前端盘点与只读契约草案.md)；trader基线[ARCH-030](../../../trader/docs/architecture/30-frontend-experience-read-model.md)（proposed）、[原型说明](../../../trader/prototype/frontend-20261026/README.md)。trader卡 ISSUE-20261030-156、TASK-20261030-345/346 及索引已存在于 `F:/Project/trader/docs`；本次统一驾驶舱修订因沙箱仅允许TradeMind写入，以现有[定向补丁](trader-cool-dataflow-frontend-docs.patch)交接，已由父会话应用进 trader 目标树。补丁取代旧提案快照，仅修改上述三卡与两个索引对应行；索引内容按UTF-8 Python定向处理，未整体重排。

## 事实：已打开的UI/API入口

以下路径以实际打开/定向读取的文件为依据；端口是代码默认或文档值，**不表示当前监听、可从Wang访问或已加载本地修改**。

| 项目 / 路径（本机绝对路径） | 页面、入口与端口依据 | 读写边界 |
|---|---|---|
| TradeMind `F:/Project/TradeMind/frontend/package.json`、`frontend/src/router.ts` | Vue3/vue-router/ECharts已安装；Overview、Market、Assets、Strategy、Activity、Reports、RunCenter七页；hash路由 | 呈现层；本轮不改依赖。Vite配置未固定开发端口，不能当部署端口 |
| `F:/Project/TradeMind/frontend/vite.config.ts`、`src/trademind/api/observer_app.py` | 构建到`src/trademind/api/static/workbench`，FastAPI同源`/workbench/`；runner的observer子命令；8321来自observer说明及operations | observer请求只读业务库；自身生命周期写observer-state心跳，不能称整个进程零文件写入 |
| `F:/Project/TradeMind/src/trademind/api/workbench_routes.py`、`read_models.py` | GET `/api/workbench/{overview,instruments,positions,strategies,activity,runtime,alerts}`，activity详情、instruments/{id}/bars与events、account/series、reports/{day}；GET `/api/observer-status` | 只读快照，账户由服务端配置固定，不可直接让浏览器任意切账户；SQLite mode=ro/query_only |
| `F:/Project/TradeMind/src/trademind/api/app.py` | 旧根页面、`/reports/daily`、`/api/status,account,orders,decisions,audit,trace/{trace_id}`等 | 旧GET可能依赖get_pipeline初始化，**不纳入高频只读白名单**。POST `/api/control/kill_switch,decide/{instrument_id},poll_fills`会改变业务，不接入新视图；observer未挂旧控制端点 |
| trader `F:/Project/trader/apps/trader/web.py`、`quant/dashboard/service.py` | `python -m apps.trader.web`；默认127.0.0.1:8080；`/`、`/overview`、`/stock`、`/research`、`/etf-research`。daily-shadow-operations记载8080 ETF / 8081个股研究 | GET/HEAD监控；股票与研究服务可选，缺服务503。8081是文档部署入口，非web.py第二个硬编码默认值 |
| 同上web.py | GET `/healthz`；`/api/{status,run-ledger,performance,performance.csv,backup-status,data-verification,risk-rejections,llm-calls,account,plan,next-session,orders,trades,reconciliation}`；股票对应`/api/stock/*` | 可复用只读查询；每项成本/分页/健康语义仍需契约审查，不高频全扫历史 |
| `F:/Project/trader/apps/trader/stock_research_web.py`、`etf_research_web.py` | 个股研究catalog、monthly、paper-trial、etf-trial、ai-cards、jobs；ETF研究`/api/etf-research*` | 已存研究GET与监控分开；POST `/api/research/jobs`及ETF研究jobs可创建隔离回测，**不是只读，不纳入本方案** |
| `F:/Project/trader/quant/dashboard/{overview,stock_research,etf_research}.html`、`docker/dashboard/Dockerfile` | 原生HTML/JS模板；Docker入口仍是apps.trader.web | Docker是兼容部署材料；当前文档为150本地uv+便携PostgreSQL，不能据Docker目录推断现运行容器 |
| `F:/Project/trader/prototype/frontend-20261026/README.md`、`docs/tasks/TASK-20261026-289-frontend-shell-assets-routing.md` | 原型五页、无构建、设计样例；可选8099静态预览；289优先原生ES模块，待依赖/Owner交接 | 原型不接API；ARCH-030拟议`/ui`、`/api/ui/v1`尚不能当已实现端点 |

已打开架构：`F:/Project/TradeMind/docs/architecture/27-chinese-large-screen-workbench.md`=implementing，视觉/回退仍未验；
`F:/Project/trader/docs/architecture/30-frontend-experience-read-model.md`=proposed，只有部分独立模块交付。两者都不能以本轮规划代验收。

## 事实：可接入的只读源

`<TM>`=`data/runtime/local-1k`；`<TR>`=trader项目根。Wang路径是**开发树候选位置**，未打开实际账本/日志/私密配置、不证明存在或新鲜。150路径来自setup/current-status及源码相对路径拼接，未远程确认。浏览器优先HTTP，不能直接读取D盘/F盘，更不能下发SQL或任意文件路径。

| 数据 | Wang候选 / 150文档位置 | 推荐入口、局限 |
|---|---|---|
| TradeMind监督/盘中心跳/observer | `F:/Project/TradeMind/<TM>/{auto-paper-state,intraday-health,observer-state}.json`；150 `D:/trademind/<TM>/`同名文件 | `/api/workbench/runtime`、observer-status；分别显示web可读/worker存活/交易就绪，不用动画合并为healthy |
| TradeMind持仓、账本事件、计划、活动 | 两根下`<TM>/trademind.db`；实际配置身份待核实 | positions、overview、activity、strategies、account/series。仅后端固定账户只读连接，保留快照时间/币种/Decimal，不读原库到浏览器 |
| TradeMind有界paper结果与日报 | 两根下`<TM>/paper-runs/`、`<TM>/daily-reports/<account>/<day>.json/.md/.html` | 已有reports/{day}可读日报；paper-runs未发现独立列表HTTP，本轮仅记录文件源，不能杜撰API |
| TradeMind日志摘要与费用 | 两根下`<TM>/auto-paper.log`；日报、intraday-health的budget_meta | runtime现有model_budget_today来自日报settled/pending，不保证实时完整预算余额；日志摘要HTTP/限量tail未确认。缺日报为unknown，预留不是结算账单 |
| trader ETF/个股心跳 | Wang `F:/Project/trader/var/{shadow,stock}/worker_health.json`；150 `D:/trader_wang/var/{shadow,stock}/worker_health.json` | status、stock/status、healthz；源依据web.py默认值。worker身份/心跳与readiness分别显示 |
| trader账户、持仓、订单、成交、计划、绩效 | 后端PostgreSQL；150 setup记载127.0.0.1:55432，数据目录`D:/trader_wang/var/pgdata`；Wang无运行主库假设 | account/orders/trades/plan/performance/reconciliation；浏览器不连数据库，不读取连接凭据。字段、账户及归属复用现有query service |
| trader运行履历、研究与数据质量 | run-ledger、next-session、data-verification、llm-calls、risk-rejections；文档报告目录`<TR>/var/shadow/reports/` | run-ledger不等同TradeMind paper-runs；LLM调用统计不等于共同预算。在线模型额度接口未知；已存研究jobs/ai-cards只读可挂接 |
| trader日志 | runbook记载`<TR>/var/logs/stock-worker-managed-carrier.log`；其他日志确切名称/保留策略未知 | 无统一日志摘要HTTP证据；仅计划未来后端固定白名单、有界摘要、脱敏，不扫描整个磁盘 |

路径来源另已打开：`F:/Project/TradeMind/docs/project/{setup,operations,current-status}.md`、`scripts/run_local.py`；
`F:/Project/trader/docs/project/{setup,daily-shadow-operations,stock-paper-runbook,index}.md`。README中仍有历史资金描述，以current-status为本地事实入口；本轮不改资金也不替其他Task修文档。

## 假设：尚需验证

- 现有快照能支持分钟级运维浏览，但没有完整跨进程event bus/统一trace证明；不能把相邻时间事件画成因果链。
- Wang到150的HTTP可达性、实际bind/鉴权/加载版本未知；127.0.0.1始终指各自机器，不能把150 loopback当Wang本机服务。
- 两项目刷新成本、最大事件量、GPU/屏幕能力与跨项目账户映射未知；共享控制台先只是只读观测台，不拥有现金/风险/交易状态。
- 用户已明确单壳双窗方向；乱码不再作为“单项目还是双项目”的开放问题，也不扩展编码或部署授权。

## 架构决策：单壳双窗与责任边界（规划，未实现）

默认推荐：**扩展 TradeMind 现有 Vue 工作台作为唯一 cockpit 壳**。复用既有 Vue/TypeScript/ECharts、导航和只读展示组件，减少第二套构建与交互维护；这只是承载推荐，具体文件和实施 Owner 在04冻结，编码须后续确认。

| 责任 | 规划归属 |
|---|---|
| 单一入口、split布局、公共tokens、粒子/管线、证据抽屉、故障隔离 | TradeMind cockpit 壳 Owner（待认领，与ARCH-027原Owner交接） |
| 左窗 trader | trader345提供字段映射、源身份、单位/时间/分页/成本边界；346交接左窗样例、交互及验收，不另做产品UI |
| 右窗 TradeMind | 04冻结行情→StrategyPlan→JEV→Guard→OMS→paper、runtime/activity/account等只读映射；保留原系统状态所有权 |
| 双源只读接线 | 06负责两个适配器到同一壳；后续必要的固定上游GET/HEAD同源代理须独立审查鉴权、超时和白名单，不开放任意URL |

左窗显示trader数据刷新/质量/因子/计划/风险/OMS/paper及账户和run-ledger；右窗显示TradeMind行情/计划/JEV/Guard/OMS/paper及账户和activity。两窗同时可见、分别标项目/主机/账户/源时点；共享时间刷选器可选，只有时间覆盖一致时联动，否则分别显示时间窗。账本、预算、查询事实源不合并；相近时间不建立跨项目因果。

v1排除的替代方案：双独立站点（违背用户决定）；新建薄壳仅代理/读取两系统（增加独立构建和运维Owner，当前不优于复用工作台，只有后续明确变更决定才重评）；iframe拼接既有两站（难以统一状态、证据和交互）；中央交易/数据写入后台（超出只读边界）。原站维持现状不等于另交付第二产品UI。不迁移trader技术栈，不修改其289原生模块合同。

本研究决策段为跨项目规划唯一详细决策记录；不新建TradeMind ARCH-030，避免与trader编号混淆。ARCH-027保持implementing，trader ARCH-030保持proposed；实施前由相关Owner补相应接口/安全/部署架构记录，不标implemented。

## 统一壳视觉与交互规划

**Vue+ECharts先行；Canvas粒子作为可关闭呈现层；D3、Three.js按证据增量引入。** ECharts支持Canvas/SVG，官方认为大量元素/视觉效果更适合Canvas，但需实际设备测量。[ECharts官方说明](https://echarts.apache.org/handbook/en/best-practices/canvas-vs-svg/)
固定分层流水线用ECharts graph/lines或轻量Canvas即可；若需可拖拽自由图再评估D3-force，其模拟会修改节点输入，应使用展示副本。[D3官方文档](https://d3js.org/d3-force/simulation)
Three.js只候选用于可选“空间管廊”，不得遮住中文告警；按需渲染、限制分辨率/粒子数、无WebGL时回退2D。[Three.js官方按需渲染](https://threejs.org/manual/pages/rendering-on-demand.html)、[响应式渲染](https://threejs.org/manual/pages/responsive.html)
不引入第二前端栈或复制trader页面。统一Vue壳读取两套适配后的快照；trader现有页面与289合同保持独立所有权。是否需要固定上游同源代理取决于后续网络/鉴权核实，SSE和新端口不作为默认依赖。

| 页面 / 层 | 视觉和交互草案 | 数据含义 |
|---|---|---|
| 运行流 Ops Stream（入口） | 深色网格、分泳道光轨、时间刷选、暂停/追随；点击光点打开证据抽屉；按项目/主机/账户筛选 | TradeMind“行情→计划→JEV→Guard→OMS→paper”；trader“数据刷新→质量→因子→策略/计划→风险→OMS→paper”。静态拓扑与真实事件分别标注，缺事件不补造流量 |
| 系统图 System Graph | 主机/进程/源/存储分层，缩放、节点聚焦，上下游高亮；显示年龄/延迟/阻塞原因 | 首期配置定义依赖，不宣称自动发现；布局坐标不决定业务依赖。未完成的股票链灰色标规划 |
| 账户玻璃舱 Account Glass | 权益/现金/持仓/费用叠层，时间窗与原始证据联动；每项目账户隔离 | 只读既有account/series/positions/performance，入金≠收益，unknown≠0，不合并两个项目账本 |
| 日志爬行器 Log Crawler | 横向时间轨、结构化事件卡，关键词/级别过滤，暂停跟随、展开邻近证据 | “爬行器”是日志浏览视觉，不是真爬虫；先activity/run-ledger，原始日志待白名单摘要接口。截断/轮转/断档明确提示 |

全页固定来源、数据时点、观察时点、账户与质量。装饰粒子标“示意动效”；有事件才标事件脉冲，停更/断连停止事件脉冲。键盘列表与抽屉提供等价信息；prefers-reduced-motion/手动关闭动效、隐藏页暂停、切账号取消旧请求。草案性能门禁：每源不重叠轮询，先5–15秒可配置退避；单屏≤50节点/100边/500可见事件，超出聚合/分页；约30fps动效、无动效可用。以上为待测目标而非测量结果。查询限时/上限后端执行，前端不可控制文件路径。

## 分期、Owner和未决

04冻结统一读契约和pane ownership → 05统一split壳的样例交互/酷炫流动/低动效原型 → 06两系统只读适配接入同一壳并分别验收。三卡均draft，本轮只更新规划；trader345提供字段映射，346提供左窗样例/Owner交接与验收，不建设第二产品UI。263/289/291/293/308原Owner范围仍保留。

待确认：目标屏幕/显卡和窄屏双窗可读布局；哪些脱敏日志可展示；是否已有统一trace与离线样本；后续只读上游可达性/鉴权；壳与适配Owner的确切文件交接。单一双窗方向已决定，不再待选。
明确非目标：不建设两个独立网站；无实盘下单按钮/撤单/解锁/调现金风控/触发模型/研究作业；本轮不编辑交易/runtime代码、frontend/package.json、src/apps/quant，不部署150、不SSH、不重启、不commit/push。

## 2026-10-30 授权后的实施Checkpoint（替代上文仅规划状态）

用户已明确授权本地04/05与trader345文档合同。04完成[ops.v1字段/Owner/GET冻结](ops-cockpit-readonly-contract-v1.md)；05已有纯SAMPLE本地双窗原型，实际浏览器/性能门禁未验，状态in_progress，详见[05](../tasks/TASK-20261030-05-流水线交互视觉原型与性能门禁.md)。06保持draft，ARCH-027仍implementing。trader TASK345映射源码审阅及[新定向补丁](trader-345-readonly-contract.patch)已准备，当前沙箱仅TradeMind可写，补丁未应用，不能声称345源卡已更新；原trader-cool-dataflow补丁仅历史对照，不再次应用。无真实HTTP接线、150、生产重启、commit/push或交易写入。

## 2026-10-30 Claude 落地Checkpoint（Trade Ops 追加授权）

用户追加授权05接入现有只读API（不再是永久SAMPLE-only）。Claude接管04/05/345 Owner并落地：05新增[只读适配层](../../frontend/src/adapters/opsReadonly.ts)——右窗接同源 `/api/workbench/*` 白名单GET，左窗仅当配置固定 `ops-trader-base` 时尝试trader三端点（默认空→保持SAMPLE），失败按窗独立回退样例并明确LIVE/SAMPLE标注；trader account ok=false零占位丢弃、host不核实置null、run-ledger不映射事件脉冲。345补丁经反向校验确认已完整进trader目标树，Owner移交Claude。04契约补"未入v1白名单"节（performance/backup-status/llm-calls等未审阅不接线）。前端77项测试与本地构建通过；浏览器视觉/性能门禁、trader在线验证、轮询成本仍归06/后续验收。无150、无commit/push、无交易写入。
