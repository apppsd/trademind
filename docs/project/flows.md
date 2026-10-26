# 运行流程

自动模拟：run_auto入口验证代理/无手动paper→停止旧workbench→auto容器supervisor→上海交易日历检查（日期在日历覆盖范围内才继续：覆盖内非交易日按休市，覆盖外/临近结尾显示"日历覆盖不足"并停止模拟与付费请求等待人工更新日历，[ARCH-018](../architecture/18-calendar-ownership.md)）→等待serve，或先停serve后启动有界paper（原免费门禁→双探针→分钟决策）→保存报告并排空worker（对已有结果写model_drain审计终态：取消未启动/模型异常/过期废弃/有效不执行均安全恢复，仅worker未排空或不可分类锁定，见[ARCH-015](../architecture/15-model-drain-terminal-states.md)）→正常稍后续跑/错误退避/午休恢复serve；结果未知则锁定自动重试。每5秒更新状态心跳，容器健康不代表模型或交易验收。无新增账户写权/订单授权。本地Windows入口（[ARCH-026](../architecture/26-local-windows-runtime.md)）：`run_local.py start`校验重复心跳/端口/代理后脱终端启动同一supervisor→相同serve/paper周期；`stop`写`auto-paper.stop`标志→supervisor轮询置stopping并**拒绝一切新writer**→排空子进程退出（paper超时才TerminateProcess并halt）→按`result_policy`分类paper最终排空报告（缺失/损坏/未知/失败原子持久化halt，合法终态不误暂停，不清除既有halt）→`shutdown()`收尾原子持久化终态（halted/原因），强停/停止异常即时持久化halted，重启保持人工核对暂停，持久化失败显式报错；状态/日志/锁/标志均在配置的运行根目录。

每日归档：strategy-4中文说明校验通过的有效StrategyPlan→脱敏追加strategy_plan_history；工作台周期/paper结束/手动CLI→只读SQLite一致快照→按上海日事件重建资金/剔除入出金→已实现收益/必要市场估值→账户预算已结算/未知预留→中文展示层/历史原文哈希译文→日报JSON/MD/HTML单文件原子替换。无估值不补造收益，无模型请求/账户写权，原协议及审计文本保留。快照读取按上海日窗口约束（模型调用/意图/计划±2天、context 7天回看，走20261013-17迁移2索引），账户事件保持全量重放重建期初账本（无边界快照，属必要历史，不丢）；持仓无当期可用行情时price_time仅解析7天内快照，更旧为null，金额/口径字段不受影响。

盘中健康读取：auto每5秒/paper结束/日报内嵌调用`read_intraday_health`，model_calls与strategy_plan_history按上海日±2天窗口走索引读取、Python精确日过滤，intents当日最近意图为窗口内有界候选挑选；昨日及更早的失败/计划不进入当日连续失败数或最后成功时间，当日行不丢；行数上界由测试固定（[ISSUE-20261013-08](../issues/ISSUE-20261013-08-高频健康与日报查询扫描全部历史记录.md) resolved）。

估值与风险退出隔离：每轮决策逐持仓独立取参考价，单持仓行情失效记入`diagnostics.valuation_failures`并跳过，不阻断其他持仓的确定性风险退出；任何持仓估值不完整时Guard以`incomplete_account_valuation`否决新开买入，日初基线不以残缺估值落定，失效标的不交易不补价（[ARCH-016](../architecture/16-position-valuation-isolation.md)；[ISSUE-20261013-05](../issues/ISSUE-20261013-05-单持仓行情失效阻断其他标的风险退出.md) resolved）。

新增离线提示评测：验证案例Schema/标签→核对数据集/输入指纹→读取保存的九字段草稿/四题answers→分别统计模型建议、程序观测、费用/耗时→原子报告。缺响应/未知费用不填零；A/B须同输入/配置/模型。实际Guard/OMS不在这个工具内执行，账户保持只读；证据见[报告](../research/20261011-prompt-eval-verification.md)。

快速提示流程：可见分钟/账户+实际草稿Schema/条件/费率/硬约束→flash九字段草稿→程序校验并绑定strategy-3→JEV独立四题→原概率/合法动作门禁→fast-2意图→Guard/OMS。交易条件语义单一来源`context/fast_state.conditions_allow`：BUY须全部入场条件满足、SELL任一退出条件满足、空条件集合永不授权交易；同步动作构造与异步结果复核共用该判定（TASK-20261013-12）。旧策略缓存版本不同先失效，再按现有冷却刷新；不补订单、不强迫买卖。详见[提示验证](../research/20261011-prompt-verification.md)。

2026-10-10：paper退出先关闭新预约→等worker/丢弃未消费动作→原子写调用/订单/成交/账户报告；容器超时定向stop→成功才恢复workbench。采集在真实时段先取共同免费行情→质量检查→显式模型探针→独立账户运行两链→原子档案→禁网对照；空档/缺档不成功。预算迟到usage回到预约日。详见[收尾报告](../research/20261010-engineering-completion.md)。

Task18安全工程：runner免费门禁先于收费双探针，daily_budget只读检查两次预算；Pipeline先保留程序风险退出，再以WAIT_DATA_QUALITY拒绝缺指标模型worker。新profile的ready不代表交易闭环，详见[Task18事实与限制](../research/20261009-task18-verification.md)。

Task16分钟特征已修复：连续1m窗口遇缺口/午休/隔夜重置，当天已观察量额VWAP，分钟版本minute-feat-2026-10-09；实现context/features.py与builder.py，308离线测试通过。真实盘中质量未验收，详见[验证报告](../research/20261009-task16-verification.md)。

容器显式网络入口已实施：`scripts/run_container.py check` 或 `paper --steps N`，宿主venv运行；读取既有宿主代理，仅传子进程，不修改compose/.env网络。paper先停workbench并最终恢复，默认不收费探针。工作台本身不继承此参数。操作/验证与限制见[Task15报告](../research/20261009-task15-verification.md)。

## 显式ETF分钟源（2026-10-09）

[ARCH-008](../architecture/08-etf-minute-source.md) implemented：market/adapters/sina_etf.py及DatasetRouter显式sina_etf，核验ETF限定、1m、8秒、量额单位/字段校验、未闭合分钟过滤、fetch可见时点。container示例及container/fast local已选择该源，不调用股票SDK回退。三ETF数据能读取，夜间正确stale；源完整性/盘中新鲜度待验收。更新Key后JEV经现有代理三题型HTTP200，直连451/正常启动网络条件与盘中质量仍待验收。完整证据与下一步统一见[运行连接报告](../research/20261009-runtime-connectivity.md)。

## 免费分钟质量路由（2026-10-14）

[ARCH-020](../architecture/20-free-minute-source-routing.md) implemented：候选取数→可见性/质量/新鲜度→可选交叉观察，观察共用整轮预算；差异写入证券级`_divergence`，后续pref/再次请求及缓存命中统一受持续拒绝约束，合格一致观察才解除；不缓存争议窗口、不平均或拼接。本地local未切换，真实盘中质量归Task09-16。

[ARCH-023](../architecture/23-minute-request-inflight-capacity.md) implemented：主源/观察→原子申请全路由4/每源1的无队列槽位→既有每日/观察计数与有界等待→原质量路由；容量满→busy诊断（零SDK、零请求/观察计数）→有容量的健康备用或既有拒绝路径。caller超时→保留槽位；worker实际返回/异常→仅释放槽位，迟到数据不改缓存/选源/health/divergence。close→拒绝新分钟SDK请求→在真实时限内join→仍有活跃明确False，不强制取消。

容器运行：Rancher Desktop引擎 → 构建无凭据镜像 → 只读挂载.env/容器配置与独立runtime → workbench本机页面；check为独立有界免费预检，paper为人工有界运行。一次只运行一个持有同一数据库的应用进程：写者构造Pipeline时先取库+账户级跨进程写租约（`execution/writer_lock.py`，与Database共用`storage/sqlite_url.py`路径语义，内核持进程死亡自动释放），第二写者立即非零失败零副作用；serve取得租约后持至进程退出（工作台写端点需token），runner运行类命令与评测脚本进程结束前显式释放（[ARCH-017](../architecture/17-writer-lease.md)含入口矩阵）。见 [容器操作](operations.md#rancher-desktop容器入口)。

新 profile CLI：当前行情/账户 → 确定性退出检查 → 单模型 worker（flash计划/JEV） → 下一轮取结果 → 最短期限、当前条件与账户版本重检 → Guard/OMS → 成交/期限/记忆原子事务。主循环5秒一轮，行情源仍可能阻塞；全局预算与300秒模型节流保留。同步接口与旧链兼容，详见 [TASK-20261009-10](../tasks/TASK-20261009-10-修复快速决策架构问题.md)。

2026-10-09：[ARCH-006](../architecture/06-jev-fast-decision.md) 新 profile 的离线工程已实施（02、04～07 completed），266项测试通过；03真实认证/费用、08真实对照校准、09真实启动仍未完成。旧默认链保留，新profile须显式选择。见 [验证报告](../research/20261009-fast-decision-verification.md)。

## 新 profile 实际数据流

行情轻量门禁/程序退出 → 全池持久化300秒模型槽 → 有效flash策略（900秒刷新，1200秒TTL） → 原始事实与账户 → JEV独立问题 → WAIT或合法四动作 → 提交前版本/时点检查 → Guard确定数量 → OMS下一可见分钟模拟成交。

低可信WAIT保存刷新请求，至少300秒后才能再次分析；JEV/flash失败不强行重试或切stub。首次BUY成交建立最大持有deadline；程序时间/止损/尾盘退出仍经Guard/OMS。需要真实probe，离线测试provider不构成认证。

## 多市场观察流程

market-data读取无凭据配置→五市场批量行情→规则/权限/资金筛选→本地观察报告→工作台只读展示。market-bars保留原始单位/available_at，两入口不写订单、不调用模型。见[系统概览](system-overview.md#多市场观察与购买条件筛选)。

## 决策主链（每次 run_decision，已实现）

1. 有界调度入队（持仓优先；排队/执行重复合并；过期丢弃），随后检查 kill switch 与账户时点，恢复当日日损基准。
2. ContextBuilder 构建时点快照（只含 available_at<=as_of 的 bar/新闻/财报修订/记忆+组合版本；hash 冻结）。记忆查询（2026-10-19，Task18-03）：`memories_visible` 对TEXT混合偏移available_at仅做加宽2天的SQL粗预筛，可见性判定在进入相似度/截断前按解析的带时区datetime瞬间精确比较，与`MemoryRecord.visible_at`同语义（[Issue18-01](../issues/ISSUE-20261018-01-记忆可见性按时区字符串比较导致未来信息纳入.md)）。日线修订（Task19-04）：`get_daily_bars(as_of=)`先过滤可见集合再做每日期最高revision归并，未来修订不挤掉历史已知行；`as_of=None`为实时默认。
3. 有效 Thesis 复用（快路径）或 ResearchPipeline 慢路径（5 分析师→Bull/Bear→Research Manager→Thesis，带失效条件与有效期）。
4. JEVGate 批量判断（开仓/持仓问题集；过期 Thesis 拒绝）。
5. Trader 输出 TradeIntent（方向矛盾自动降级 HOLD）。
6. AIRiskReviewer（APPROVE/REVISE/REJECT）→ PortfolioAI（REVISE 生成新意图版本）。
7. Guard 九步检查 + 原子资金预留（ALLOW 携带合法手数）。
8. OMS 原子校验同一持久授权 → 幂等提交。成交轮询只用后续交易日已可见 bar；成交、账本、订单、预留与审计同事务提交，部分成交缩减预留、终态释放。shadow 在 Guard 拒绝执行。
9. 决策登记 Outcome（horizon 到期后成熟）→ Reflector 生成反思与记忆（available_at 控制回放可见性；2026-10-19 Task18-05 门禁：未final/未到available_at在模型调用与落库前拒绝，新经验可见时间不早于生成完成与标签可用时刻。src仍无自动业务调用方）。登记/成熟边界（Task19-02/19-10）：horizon固定上海时区终点交易日07:00+08，候选发现不早于终点日收盘15:00+08且收盘后/次日仍可发现，mature要求到期且终点日bar可见（日线查询传as_of=now，未来修订不影响当前视角），final仅用horizon日收盘，缺失不用次日代替。

## 回放评估流程

`replay-demo`：隔离新内存 SQLite → replay 模式（深拷贝、清凭据、fixture/stub）→ 逐日：日切 T+1 rollover → 到时成交 → 抽样决策 → 记录净值曲线 → 汇总收益/回撤/胜率/盈亏比/Sharpe(IID 声明)/VETO/HOLD/错误/stub 计数。消融对照：full / no_jev / no_memory。

## 恢复流程

启动后 `runner recover`：SUBMITTING 订单→UNKNOWN（不自动重提交）→ 显式 reconcile → Ledger.rebuild_from_events 重放校验 → 与 account_state 比对一致后恢复。

## 独立验收边界（2026-10-07）

离线工程复验已通过，详见 [复验报告](../research/20261007-code-audit.md#修复复验2026-10-07)。在线提交按当前墙钟重检 TTL；回放按虚拟时钟，含未来账户事件的历史决策被拒绝，应新建 ReplayRunner。

## 2026-10-08 T0运行流程

显式TOML→当前时间轮询已有订单→分钟时点/质量检查与持仓估值→代码止损/尾盘退出（如触发，经Guard/OMS并结束本轮）→候选冷却检查→flash候选/JEV审核/flash最终意图→意图和meta审计→PortfolioIntent→Guard合法数量/原子审批→OMS→后续可见分钟撮合→账户事件与重建计数。

缺失/过期/未来时间/异常OHLC不调用模型；minute_only撮合缺分钟时不回退日线。DeepSeek每个请求与格式重试先做持久额度和费用上界预留；未知价格/不足额度不发HTTP。JEV链路本次保留未修状态。Prompt对照使用录制分钟数据和独立账户重走此流程，不复制原账户持仓。证据和限制见[复验报告](../research/20261008-non-jev-fix-verification.md)。

## 2026-10-08 T0已登记全池更新

最新T0全池：每轮依次抓取当前标的分钟→取抓取后as_of→用短缓存的已观测分钟进入原决策链；一只错误不跳过其他标的。t0-data只输出免费行情状态，不经过模型/账户链。实际连接限制见[报告](../research/20261008-t0-pool-verification.md)。

OMS生命周期与Guard恢复/同标的扫描均已账户化（17-08）：外账户订单取消/撮合/对账/恢复一律拒绝且零副作用，Ledger拒收外账户fill；管理级全库查询仅保留在storage层。

成交费用流（17-09）：撮合校验→Fill.fee预填（ledger费率）→持久化→apply_fill实扣（同输入同值）→返回Fill/落库行/事件note/现金差单一事实。


## 工作台V2只读流程（代码返修后，运行切换待验）

显式cfg→app.state→账户只读Store→已有Context/账户/日报/记录→统一信封→前端资源世代与陈旧判断→图表/中文条件/时间线。无提供者调用、迁移或writer租约。交易仍独立由paper→Guard/OMS→账本写入；observer启动/收养按非凭据身份+实例ID管理，退出不解锁halted。真实并存和只读验证见[返修报告](../research/20261026-workbench-repair-verification.md)；真实盘中与实际浏览器未验。
