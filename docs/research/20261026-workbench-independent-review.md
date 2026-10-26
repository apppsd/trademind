# 工作台V2独立复核（2026-10-26，Codex）

关联：[Task26-15](../tasks/TASK-20261026-15-独立复核工作台V2交付.md)。结论：**未通过完整验收**。保留已完成的前端工程/主题与部分只读实现，不能接受“12Task+7Issue+ARCH全部完成”。本轮不修改实现代码，不启停服务、不发POST/真实模型/行情请求；原Owner修复后独立复验。

## 1. 已独立执行的验证

- Python：`.venv\Scripts\python.exe -m pytest -o addopts='' -q --basetemp data/runtime/task26-15-pytest -o cache_dir=data/runtime/task26-15-cache`：**726 passed，65.13秒**，既有Starlette弃用警告。
- 前端：便携Node路径下`npm test`两次均**29 passed / 1 failed**，失败为router.spec.ts第一项5000ms超时（第一次与Python并发，第二次无Python全量并发仍失败）。不能复算“30全绿”；未改超时或忽略失败。`npm run typecheck`exit0。
- HTTP只GET当前8321：`/workbench/`200；`/api/workbench/overview`200，但返回`paper-001`/现金1000000；`/api/observer-status`非200。这证明当前serve可提供页面，不证明supervisor实际observer托管通过，也不能把默认百万账户当本地千元实验。
- 已保存live截图共**10张**，不是报告写的11张。独立查看总览和行情页：总览显示1000000与缺曲线，行情页明确显示“服务响应契约异常（信封缺失）”。这不是通过的有数据曲线截图；不将其记成已跑完七页视觉/交互验收。
- 隔离反例：`data/runtime/task26-15/probe.py`（Python/临时SQLite/TestClient/Mock）和`frontend-probe.cjs`（实际TS编译执行、Mock fetch、无网络）；仅本任务临时数据，不接触真实交易账户。

## 2. P1：影响核心交付的确认缺陷

| 问题 | 独立结果 | 根因与原Issue/Task |
|---|---|---|
| observer CLI未分发 | `runner.main(['observer'])`返回1，Mock cmd_observer从未调用 | parser与函数已增加，`runner._dispatch`缺observer分支。原supervisor虽launch带observer参数，真实进程会退出。[Issue26-08](../issues/ISSUE-20261026-08-模拟交易时工作台服务无法持续在线.md)，Task26-07/14 |
| 显式账户配置未绑定路由 | create_observer_app(cfg_A=local-1k/1000)，路由仍load_config()选择cfg_B/default-other/1000000 | factory传cfg只影响状态/静态目录；`_configured_store→_load_cfg`全局无参数load_config，忽略app cfg/CLI配置；原tests用monkeypatch遮住了问题。[Issue26-06](../issues/ISSUE-20261026-06-现有工作台GET未与交易初始化及行情请求解耦.md)，26-05/07 |
| 实际存量分钟字段不匹配 | 按生产Context的`aux.minute_bars[].time`存一根可见分钟，collect得到0 | builder实际存time，collector只读bar_time；有存量也被当无行情。[Issue26-05](../issues/ISSUE-20261026-05-工作台缺少行情与账户曲线展示链路.md)，26-06/09 |
| 曲线信封与前端不兼容 | 真实bars_response的generated_at不存在，实际isEnvelope返回false | charts/series/events没采用共同完整信封，client要求generated_at。交付截图中的信封缺失由代码路径支持，不是可忽略空态。[Issue26-05](../issues/ISSUE-20261026-05-工作台缺少行情与账户曲线展示链路.md)，26-05/06/09/10/13 |
| 曲线和静态日报跨账户 | 配置local-1k却收到了other-account的Context bar；GET静态`/daily-reports/default-other/...json`200 | Context查询只按instrument，不按portfolio.account_id；observer挂载全部账户日报父目录，绕过固定账户API。[Issue26-06](../issues/ISSUE-20261026-06-现有工作台GET未与交易初始化及行情请求解耦.md)，26-05/06/07 |
| 资金/费用口径错接 | 日报settled_estimate=0.197，API返回null；前日现金1000/当日买入100.10，现金曲线返回-100.10而非899.90 | API误读settled_cny/pending_cny；今日现金从0开始，未重建期初；前端类型reserved与实际reserved_cash也不一致。[Issue26-05/09](../issues/ISSUE-20261026-09-工作台信息层级缺少持仓策略与交易详情.md)，26-05/06/08/10 |
| null在资产图变0 | 编译执行AssetsPage实际num(null)，结果0 | Number(null)导致未知权益被画为0；相同写法存在多图表。[Issue26-05](../issues/ISSUE-20261026-05-工作台缺少行情与账户曲线展示链路.md)，26-09/10 |

这些都是离线/显示链路实证，不代表发生真实损失；也不能因Python全绿而忽略。

## 3. P2：必须补的记录、刷新、状态和产品要求

- **分页与详情**：decision按rowid倒序，却用随机decision_id<cursor继续；插入a/z/m后第一页m、第二页a，漏z。Fill的通用_item先选order_id，导致fill.id是订单ID，成交详情按Fill ID查会失败、同单多Fill还可能重复key。归Issue26-09/Task26-05/11。
- **取消/世代守卫无效**：请求A未完成时cancel()+load()复用同一个Promise，fetcher调用仍1次，resource保留A。页面回调多处忽略传入signal；Market虽用ID守卫避免直接画错A，但B不会立即发起新请求，可持续空态。effectScope.stop后signal.aborted=false；卸载未取消在途请求。归Issue26-07/Task26-13/09。
- **完整响应超时未覆盖**：headers已到、body悬挂时timeout被finally提前清掉；5ms超时后35ms仍未settle、signal未abort。归Issue26-07。
- **展示状态错误推断**：任意只读接口成功就置apiOnline并显示“真实行情·模拟成交”，未验证数据来源/真实模型/stub；陈旧时间主要靠失败标记，状态读取mtime不等于心跳/PID实际健康。归Issue26-10/Task26-12/13。
- **中文与能力**：Strategy页直接显示instrument_id和条件JSON，未统一中文名/条件中文字段；目录所有白名单均tradable=True（含T+1上证50），WatchCard只比余额与一手本金，忽略200/300限额、费用和100储备。归Issue26-04/09，不能把显示“可交易”当正确资金可达诊断。
- **策略原要求缺失**：条件没有当时实际值、满足/不满足/未知状态；planStatus只比ISO字符串valid_until，未处理可用/替换/失效，且跨偏移字串比较不等于瞬间比较。简单列三组条件不等于完成Task26-11。
- **曲线完整性**：前端折线直接使用category/close连续连接，不按gap_notes断线；量源为share却标题写“手”。events先拿全历史最近200再筛日期，可能遗漏选定历史范围。归Issue26-05/Task26-06/09。
- **observer收养**：检测到状态文件live PID只return，并未登记进self.observer；后续state observer_pid=None、stop_observer不管理该进程。只测launch命令和Mock子进程不能证明真实收养/退出成立。归Issue26-08。

## 4. 延期与完成范围

- 多标的比较、图片导出和新增CSV属于次级/可选增强，可以记录延期，不凭它们否定已完成核心组件。
- **回到最新**、可靠切标的/取消、条件当时值/状态、正确关联记录/逐节点阅读路径属于原首期要求；只在实施末尾写“延期”没有完成原验收，也没有用户确认缩减范围。Task26-09/11不能继续以全部completed收口。
- 日期说明有错误：文档多处写2026-10-26“周六/休市”，但该日为周一且项目`calendar_xshg.csv`列为交易日。未验可如实写未验，不能用错误时段解释。这里只核本地日历，不据此声明外部市场实际运行已验。
- Task正文仍保留“Owner待认领/pending/未实施”、后来又写completed；ARCH与roadmap既写implemented又说仅规划，文档同步尚未收敛。顶层应由本轮复验限定历史，不继续传播完成声明。

## 5. 返修顺序与复验门槛

1. **26-05**先绑定显式cfg与安全读取（无凭据回退/固定账户），统一信封/字段、日报路径隔离与金额语义，补真实生产者→消费者契约测试。
2. **26-06**修实际分钟time适配、账户/时点筛选、图表信封、现金期初、费用字段、历史events范围；用现有真实Context形状做golden样本，不只手写假API字段。
3. **26-07**补CLI分发、真实进程入口/收养/停止/端口验证，先隔离临时端口/账户验证，不碰当前8321服务；不得用旧serve HTTP200替代observer运行证据。
4. **26-13**修切换请求世代/取消与组件卸载、完整body超时、模式/陈旧源；页面必须传signal，补挂载页面级乱序/切换测试。
5. **26-04/08/09/10/11/12**依上述契约补中文/能力/资金可达、图表null/缺口/单位、条件状态/详情/分页、模式状态及首期交互。
6. **26-14**重跑前后端门禁；front测试不绿查原因，不先改大超时掩盖；截图须包含真实有数据曲线与交互结果、真实observer而非仅serve；静态包/干净wheel/回退独立验证。修复前不将ARCH重新标implemented。

本轮只完成独立审查/返修交接。复用原Task与7个Issue，保留Claude Code Owner，未覆盖未提交实现代码；具体根因/关闭条件在对应Issue维护，Task26-15保存验证入口与状态同步。
