---
type: research
title: 双系统运维驾驶舱Claude实施简报
status: draft
created: 2026-10-30
updated: 2026-10-30
related_tasks: [TASK-20261030-07, TASK-20261030-08, TASK-20261030-09, TASK-20261030-10]
related_issues: [ISSUE-20261030-03]
related_architecture: [ARCH-027]
---

# 双系统运维驾驶舱Claude实施简报

## 目标与交付边界

把“TradeMind单系统页面加一个双窗运行流”改为真正双系统驾驶舱：**进入即整体总览，左trader与右TradeMind同时展示；两窗各有导航和内容；细节和美观程度对等。** 继续使用TradeMind Vue3 + Vite + ECharts，不新建网站/技术栈。

本简报供后续 `claude -p` 会话认领，不是本轮已经执行Claude的记录。Codex本轮只规划。首发是本地SAMPLE/readonly工程，默认固定样例、零业务网络请求。既有05只读适配保留，06继续负责未来真实接线；不把现有API可用当成双系统真实验收。

事实和行号唯一见[Issue30-03](../issues/ISSUE-20261030-03-双系统大屏导航与内容失衡.md)；[Issue30-02](../issues/ISSUE-20261030-02-跨项目流水线观测缺少统一只读语义.md)仍open；05 in_progress、06 draft、ARCH-027 implementing均不因本规划改变。

## 首发主方案与取舍

左侧选**密集Vue SAMPLE卡片 + 五个子页**。只读参考 `F:/Project/trader/prototype/frontend-20261026/{index,account,explorer,instrument,states,components}.html`、README与样式；复用TM现有MetricCard、ChartPanel、状态组件与ECharts，不复制第二套站点，也不修改trader。

不采用iframe首发：原型虽有完整静态SVG与明细，但也有自己的站点导航、研究/系统占位及仅演示的按钮；半屏适配、iframe内外导航/历史同步、静态资源挂载和安全隔离会增加工作。统一Vue窗内路由能直接满足独立导航、深链与视觉对等，整体交付更稳定。此次只实现这一主方案，不能同时铺iframe备用路线。

右侧直接复用七个现有页面的呈现逻辑，添加驾驶舱内SAMPLE资源注入与窗内跳转适配。不能重做七页，也不能仅嵌进去后继承原自动GET/轮询/真实日报外链。

## 信息架构与导航合同

- 全局：品牌“交易系统大屏 / trader · TradeMind”、上海时钟、整体只读模式/按窗状态摘要、回到双总览。移除驾驶舱内全局TM专属导航，不能用它替代右nav。
- 桌面：常驻50/50，左trader / 右TradeMind，统一窗框与独立滚动。两侧标题/导航/来源区等高；宽度不足时卡片按窗宽重排。窄屏上下两窗保留，不切成只看一个系统的全屏tab。
- 左nav：总览、账户、标的、探索、状态；可附加运行流。
- 右nav：总览、行情、资产、策略、交易记录、日报、运行中心；可附加运行流。
- 推荐地址：`/#/cockpit?trader=overview&tm=overview`；左页值 `overview/account/instrument/explorer/states/stream`，右页值 `overview/market/assets/strategy/activity/reports/run/stream`。字段白名单校验，非法值只回退对应窗总览。
- `/`重定向双总览；旧 `/overview`、`/market`、`/assets`、`/strategy`、`/activity`、`/reports`、`/run`分别映射右页，左默认总览；旧 `/ops-stream`映射双侧stream，保留旧书签但不再是默认首页。
- query按窗命名，例如 `traderInstrument`、`traderFilter`、`tmInstrument`、`tmDay`、`tmActivity`；切页保留另一窗全部query与内存状态。用户切tab push历史，筛选可replace；前进/后退及刷新恢复双方选择。回双总览是明确重置双方的唯一全局操作。
- 两窗宿主保持稳定，只替换被操作窗的子页，不以整个query作根key；未操作窗不重挂、不清滚动/筛选。各窗页内链接都更新本窗目标；不得跳回全屏TM。07 Owner维护唯一导航helper，08/09调用。
- 模式：双壳显式SAMPLE，与旧全局apiOnline隔离；来源、账户、数据时点、观察时点按窗显示。右源将来成功也不把左窗标LIVE。

## 内容矩阵：两侧必须都有业务细节

| 窗 / 页 | 首发必须有的内容与交互 |
|---|---|
| trader 总览 | ≥4指标（权益/现金/持仓数/样例收益等）、权益趋势、为何未交易、候选与最终计划摘要、最近运行履历；标明确定性策略身份 |
| trader 账户 | 样例非零持仓、已清仓历史、费用拆分、委托/成交日期、账户口径说明；点击持仓到左标的页 |
| trader 标的 | 中文名+代码、大价格图/成交量、MA20与样例成交/计划标记、时间窗与解释区；无曲线变体明确提示 |
| trader 探索 | 候选观察与最终模拟计划分区，至少6条固定候选、筛选与标的入口，非调仓日样例；不能把候选当订单 |
| trader 状态 | 运行/质量摘要及loading/empty/missing/stale/error/unknown变体，来源/时点/恢复说明；不是研究/系统“以后再做”占位 |
| TM 总览 | 复用Overview：指标、账户/标的曲线、未买原因、近期事件；默认样例有完整内容 |
| TM 行情 / 资产 | 复用Market/Assets：样例列表、标的图表与筛选；持仓/权益与费用细节；宽表在块内滚动 |
| TM 策略 / 交易记录 | 复用Strategy/Activity：StrategyPlan/JEV/Guard语义、计划/决策/订单/成交明细与详情；窗内互链 |
| TM 日报 / 运行中心 | 复用Reports/RunCenter：日期切换、本地样例报告/下载、只读监督/健康/就绪/费用摘要；不提供控制入口 |
| 双侧可选运行流 | 复用OpsStreamPane，左右各自身份/拓扑，示意粒子不冒充真实事件；不是业务总览替代品 |

样例金额/曲线/事件来自固定脱敏fixture，明确SAMPLE、固定日期、币种/单位；不用真实账户文件或API灌样例。未知显示“—”；成功空、错误、缺失、陈旧分别处理；样例红涨绿跌同时带符号/文字，不只靠颜色。

## 文件清单与所有权

以下是后续Claude允许认领的拟议文件，**本轮没有编辑实现文件**。先检查git diff，与05及既有页面Owner交接；保留原有未提交修改，不能整体覆盖。

| Task | 可触及文件 / 合同 |
|---|---|
| [07 双壳](../tasks/TASK-20261030-07-双系统独立导航与常驻总览壳.md) | `frontend/src/{App.vue,router.ts,layouts/WorkbenchLayout.vue}`；新增 `layouts/DualOpsLayout.vue`、`components/ops/OpsPaneFrame.vue`、`composables/useCockpitNavigation.ts`；router/repairs测试和新增dualOps测试。共享壳仅07 Owner串行修改 |
| [08 左内容](../tasks/TASK-20261030-08-trader左窗密集样例子页.md) | 新增 `components/ops/TraderPane.vue`、`pages/ops/trader/`五页、`demo/traderCockpitSample.ts`、traderCockpit测试；原型只读参考 |
| [09 右复用](../tasks/TASK-20261030-09-TradeMind右窗复用页面与子路由.md) | 新增 `components/ops/TradeMindPane.vue`、`demo/tradeMindCockpitSample.ts`及窗内资源provider；七个现有Page、`composables/useWorkbench.ts`、`useResource.ts`、`services/workbenchApi.ts`仅做最小可选注入/禁用样例轮询/链接适配；tradeMindPane测试 |
| [10 联合验收](../tasks/TASK-20261030-10-双窗视觉对等与联合验收.md) | 已交接双壳/窗内页面和 `frontend/src/styles/`相关tokens；dualOpsAcceptance测试；docs/research验收报告与截图；逐文件文档同步 |

09资源注入要覆盖七页的直接useResource、目录/图表/详情取数和useWorkbenchResource，而非只包一处fetch。驾驶舱内默认选择fixture provider且不启动业务轮询；禁止全局覆盖window.fetch。原只读API路径默认语义保持，05适配器不扩白名单。Reports真实Markdown链接须换成本地样例操作或明确禁用，避免样例页仍请求后台。运行流若复用OpsStreamPage，应拆出纯呈现，不能挂载后自动调用reload。

实施后的 `npm run build` 按现有Vite配置输出 `src/trademind/api/static/workbench`；只允许正常构建产物，不手改后端Python或宣称部署。文档同步按07～10各卡契约，ARCH-027仍implementing；本轮按用户限制只写Task/Issue/Research/brief及索引、current-status短提示，不改Architecture正文。

## 禁止事项

- 不改 `src/trademind/`业务代码、scripts、运行配置、现金/风险/预算参数；不改任何trader实现文件。
- 无实盘/模拟下单、撤单、解锁halted、控制POST、模型调用、研究作业或第二writer；不读取密钥、Cookie、账户私密配置或真实日志灌样例。
- 不SSH、不碰150、不部署、不重启、不commit/push/merge/rebase；不新增依赖或技术栈。
- 不用自动API失败回退冒充纯SAMPLE；不以测试通过或截图替代06双源接线/真实交易验收；不关闭Issue30-02。
- 不覆盖05/其他任务改动，不把07～10创建为draft误写为已实现；本次不启动Claude或服务。

## 推荐认领顺序与验收

**07 → 08 → 09 → 10**，优先补强左trader，再复用右页；08/09虽同依赖07，本轮建议同一个Claude串行实施，减少共享文件冲突。每卡认领填写Owner/确切范围/分支Worktree，完成Checkpoint后再继续。04已completed，05仍有独立待验项；新SAMPLE批次不依赖06真实接线。

- [ ] 根入口双总览50/50同屏；全局TM导航迁入右窗，左有五页导航。
- [ ] 左五页/右七页内容矩阵全部完成；每侧≥4总览指标、趋势图、明细/事件和原因/状态区，双方首屏均有可见信息与图表。
- [ ] 切左/右、页内链接、前后退、深链刷新不卸载或重置另一侧；旧书签进入双壳，报表链接不逃逸。
- [ ] SAMPLE默认初次/刷新/切页/停留/日报操作零业务请求；未知不补零、样例不混真实账户，单窗错误隔离。
- [ ] TASK-10成对视觉清单全部勾选并有同屏证据，涵盖1440×900、1920×1080、390窄屏、200%缩放、键盘/低动效/图表resize/状态异常。
- [ ] 工程typecheck/test/build与文档校验通过；真实浏览器未测就明确未测，不宣称视觉/性能通过。Issue30-03仅在其关闭标准满足后关闭。

## 本地预览URL说明（待Claude实现后核验）

- 开发候选 `http://127.0.0.1:5173/#/cockpit?trader=overview&tm=overview`；深链示例 `http://127.0.0.1:5173/#/cockpit?trader=account&tm=strategy`。
- 构建挂载候选 `http://127.0.0.1:8321/workbench/#/cockpit?trader=overview&tm=overview`。端口沿用既有文档，不表示服务正在监听或加载新产物。
- Claude实施验收可按既有说明仅启动临时本地Vite：frontend目录 `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`；端口占用先核对，不杀现有服务。验证后停止自己启动的临时进程；不启动/重启后端。
- 当前已有地址仍是 `/#/ops-stream`；新cockpit路由尚未实现，不能把上述候选URL当已交付预览。Vue SAMPLE方案无iframe静态副本、无8099额外服务要求。

## 规划交付验证

2026-10-30 Codex：读取当前Git状态、入口/Task/Issue/研究/ARCH-027、相关Vue路由与资源代码、trader静态原型；没有active Handoff目录。既有工作树大量未提交改动已保留，05/06未改。

- 文档检查基线：`.pytest-tmp2\task30\python\cpython-3.12.13-windows-x86_64-none\python.exe -X utf8 -B scripts\check_docs.py`，exit 0，0错误/36既有提醒。常规 `.venv\Scripts\python`指向的解释器不存在，使用已有便携Python，无安装/升级。
- 交付后同命令校验：exit 0，0错误/36既有提醒，没有新增链接/索引/依赖错误或提醒；`git diff --check -- docs` exit 0。本轮所有写入均为本文、Issue30-03、Task07～10、两份索引、主研究页与current-status短提示；新增实现diff为零，原有实现改动保留。
- 未运行前端测试/build、未启动浏览器或HTTP；本轮没有实现代码，视觉/性能/网络验收全部留给Claude。07～10保持draft，Issue30-03 open。
