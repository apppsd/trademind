# 运行操作手册

## 150主机夜间切换与勘误（2026-10-26，Task26-20）

千元实验链已切换为 run_local 监督（supervisor 311984，账户 paper-t0-local-1k，代码 1f4cd06）。勘误与要点：①**150 宿主代理实际端口为 127.0.0.1:7897**（Clash Verge mixed port；本节上方"D:\trademind 副本实测代理 127.0.0.1:7890"为旧值）——启动 run_local 前必须在 shell 显式 `set HTTPS_PROXY=http://127.0.0.1:7897`（NO_PROXY 含 localhost,127.0.0.1,.deepseek.com,.sinajs.cn,.sina.com.cn），否则 JEV 直连 451；实测经代理 404（可达）、直连 451。②新监督链的 serve 为 observer_app（ARCH-027），旧 `/api/status` 已 404；运行身份验证改用 `GET /api/workbench/overview`（信封 `account_alias`）与 `scripts/run_local.py status`。③run_local 脱终端持久：SSH 启动后断开会话监督器继续运行（心跳实测脱离 SSH 存活）。④switch 前单 writer 检查点：确认无存活进程访问 local-1k 库、8321 端口先停旧 serve。切换细节与次日就绪检查单见 [Task26-20](../tasks/TASK-20261026-20-夜间批次部署推送GitHub并在150执行安全切换.md)。

## 夜间二轮运维韧性（2026-10-26，Task26-22/23/24）

- **代理存活预检**：`run_local.py start` 现在会对 HTTPS_PROXY 做 TCP 连通预检（3 秒），代理不可达直接拒绝启动并给中文指引——不会再带死代理启动到开盘才暴露。
- **当日自动备份**：start 时若无当日备份，自动把账本一致快照写入 `data\backups\<账户>\<日期>\`（幂等；失败只警告不阻断启动）。
- **晨间体检**：`.venv\Scripts\python.exe scripts\check_morning_readiness.py`——8 项只读检查（代理/监督器/halted/日历/密钥文件存在性/账户身份/备份/磁盘），退出码 0 就绪、1 有阻断、2 需注意。开盘前一键运行。
- **看门狗**：`scripts\watchdog_heartbeat.py`——监督器心跳>120 秒陈旧时写 `watchdog-alert.json` 并（如配置）发 webhook。监督器自身死亡无法自报，建议注册 Windows 计划任务（用户决定）：`schtasks /create /tn "TradeMind watchdog" /tr "D:\trademind\.venv\Scripts\python.exe D:\trademind\scripts\watchdog_heartbeat.py" /sc minute /mo 15`。
- **可选 webhook 告警**：为监督器 shell 设置 `TRADEMIND_NOTIFY_WEBHOOK=<钉钉自定义机器人或任意JSON POST地址>` 后重启生效；halted/日历覆盖不足各告警一次，失败绝不影响交易。默认关闭。

## 容器自动模拟监控

Windows宿主 `.venv/Scripts/python.exe scripts/run_auto.py start` 启用；`status`查看、`stop`停止。源码更新后构建镜像再`start --recreate`（优先休市更新，等待子进程退出）。入口验证既有本地无认证HTTP代理，只传容器环境，不打印或写.env；Rancher与代理应保持运行。自动入口拒绝活跃手动paper并停旧workbench，手动paper入口检测auto活跃也拒绝；不要自行并行同账户writer。

按上海交易日历09:30～11:30、13:00～14:57连续时段监控，不含收盘集合竞价。每5秒检查，paper单次最多720轮/3600秒且截止当前时段结束；正常结束15秒后继续，行情/模型门禁失败5分钟后再检查。每轮同进程双模型验证计费，仍受既有每日请求/费用/探针限制，不改费率、阈值或余额。无交易依据时观望。

supervisor顺序运行serve/paper：休市、午休、退避时工作台及日报可用；盘中paper运行时工作台暂不可访问，日报在本轮结束生成。运行状态`data/runtime/container/auto-paper-state.json`每5秒更新；等待时/api/status的automatic和页面“自动监控”显示中文说明。未知报告、worker未排空、不可分类结果或超时会锁定halted，不自动重试；已完成且可分类的模型结果（取消未启动/异常/过期/有效不执行）写`model_drain`审计终态后不再锁定。先核对paper报告shutdown.drain_terminal_state、账户/订单和进程结束，停止auto后才能人工处理halted状态，不能用健康检查解除资金风控。

restart unless-stopped使Rancher恢复后可恢复已启用服务；手动stop后要start。电脑关机/睡眠或Rancher停止期间不能监控。**重建auto容器的工作目录shell必须导出真实代理变量**（2026-10-16实证：从无代理变量的shell `up -d` 会把空值透传进容器，JEV直连451探针失败、会话秒退并空耗探针额度）；本机可用值为 `HTTP_PROXY=http://host.docker.internal:7897`（宿主代理仅监听127.0.0.1，容器内须用 host.docker.internal；192.168.127.1 网关不通），NO_PROXY 建议含 `.deepseek.com,.sinajs.cn,.sina.com.cn`（国内端点直连更快）。日历当前覆盖至2026-12-31，下一年须更新有效日历，未列日期拒绝开市。更新官方日历用离线合并命令（不联网）：`.venv\Scripts\python scripts/update_calendar.py --source-csv <官方CSV> --source <来源名> --version <版本标识>`，并集写入 data/fixtures/calendar_xshg.csv 并生成 `.meta.json` 旁证（来源/版本/核对时间/覆盖范围）；默认拒绝缩小覆盖，确需缩小加 `--allow-shrink`。超出覆盖范围或覆盖临近结尾（≤20个交易日）时健康状态显示“日历覆盖不足”，auto supervisor 停止模拟与付费请求，等待人工更新日历；覆盖范围内非交易日按休市处理，不会截断为边界日期。stop不会另外启动workbench；需手动模式时确认auto及其子进程结束再启workbench。

<a id="local-windows-auto"></a>

## 本地 Windows 自动模拟监控（2026-10-22，ARCH-026）

无 Docker 的本地入口，与容器入口二选一（同端口 8321，不可同跑）。启动前：`config/t0-local.local.toml`（由 example 复制，绝对 sqlite 路径、账户 `paper-t0-local-1k`、1000 CNY）、根目录 `.env` 含真实双模型 Key、shell 已导出宿主代理（`HTTPS_PROXY` 必需，JEV 直连 451；NO_PROXY 含 `localhost,127.0.0.1`）。D:\trademind 副本实测代理 `http://127.0.0.1:7890`。

```bat
.venv\Scripts\python scripts\run_local.py start     :: 脱终端启动 supervisor
.venv\Scripts\python scripts\run_local.py status    :: 心跳/phase/预算/代码版本/日志位置
.venv\Scripts\python scripts\run_local.py stop      :: 写停止标志，排空最长150s
```

监督行为与容器 auto 相同（日历时段、单 writer、预算、halted 语义）。差异：状态/日志在配置的运行根目录（如 `data/runtime/local-1k/`：auto-paper-state.json、auto-paper.log、auto-paper.pid、auto-paper.stop）；重复 start 有存活心跳即拒绝；端口被占或无代理拒绝启动；**停止期间监督器不启动任何新 writer**（serve/paper 均先排空）；停止超时仅在进程映像确认 python 后强制结束该 pid，监督器自身与 run_local 双层原子持久化 halted 及原因，重启保持人工核对暂停（人工核对账本、确认后编辑 state 文件清除 halted 才能再启动，不自动解锁）；监督器收尾持久化失败会显式报错退出，此时重启可能丢失暂停门禁，须人工核对账本后再启动。**外部硬终止后重启的恢复语义**：恢复分类只在人工 `start` 重启时执行，**进程死亡后不会自行恢复**；start 会核验上一轮状态（phase=paper 且未终态时），按 `result_policy` 分类旧排空报告并对存活旧子进程探活——缺失/损坏/未知/失败原子保留 halted 待人工核对（编辑 state 文件清除后才恢复），报告合法完成或 serve 阶段被杀则正常恢复；不存在"任意被杀直接 start 即安全恢复"的无条件保证，halted 出现时先核对账本与报告。改代码后先 stop → 核对 → 再 start（venv 可编辑安装，无需重装依赖）。电脑关机/睡眠期间不运行。真实双模型运行验收见 [TASK-20261022-01](../tasks/TASK-20261022-01-本地千元双模型自动模拟交易落地.md)、停止边界修复见 [TASK-20261022-03](../tasks/TASK-20261022-03-修复本地停止误启动与强停锁定丢失.md) 与 [Issue22-02](../issues/ISSUE-20261022-02-异常终止重启未核验上一轮paper结果.md)。

## 工作台V2与观察服务（2026-10-26，ARCH-027 implementing）

入口为`/workbench/`；新`/api/workbench/*`只读，不产生交易授权或行情/模型请求；旧控制端点保持原令牌边界，observer无控制能力。新代码的observer与paper为独立进程，paper仍独占账户writer。独立observer命令为`.venv/Scripts/python.exe -m trademind.runner --config <显式配置> observer`；切换前必须按原流程核对并停止端口占用，不能与当前8321服务抢端口。本轮没有对当前进程执行此操作。

监督器启动时识别账户/存储/地址/展示约束摘要与新鲜心跳，再收养同一配置的observer；状态含实际服务PID与每次启动实例ID。停止通过实例匹配的observer-stop.json请求，避免把磁盘PID重用进程当作服务；Windows自有Popen启动器必要时按其进程树清理。状态心跳每秒写入，30秒以上或PID不存在则不算活跃；崩溃重生退避60秒，不自动解除halted，不停止正常paper。

财务与图表只读已保存产物：金额字符串，未知为null，权益估值缺失不显示0；现金包含冻结，冻结不重复加减；现金序列有历史期初，模型费用另计。曲线按真实字段/可见修订/账户筛选，有缺口则断线，成交量为份；仅Fill称成交。生成前最近留存Context只能作为条件阅读证据，不能冒充确证模型输入。资金可达仅为冻结限额诊断，不是下单授权。

`cd frontend`后`npm run build`重建静态包；Node只用于构建。Python没有热重载：当前8321后端未重启，不会自动应用返修。正式切换、真实大屏截图/缩放/事件/回退仍归Task26-14；删除静态目录不能替代完整回退验收。隔离验证结果见[返修报告](../research/20261026-workbench-repair-verification.md)。

## 每日交易日报

自动模式使用方式见下方“容器自动模拟监控”；等待时工作台生成日报，盘中paper结束时生成日报。

页面/Markdown使用中文动作、状态、条件和理由；Schema键、交易枚举及标的/模型ID按协议保留。新strategy-4要求趋势/风险/反证/原因中文，纯英文计划校验拒绝，旧缓存按版本失效但不绕过冷却。DeepSeek各请求追加中文说明要求；JEV仍返回结构化枚举。历史英文策略不重写证据，可按原文SHA256在数据库旁daily-reports/translations.zh.json存人工中文译文；今日四段旧策略已补译，无模型翻译费用。未翻译旧文字显示中文待译提示。语言校验不保证决策正确或盈利。

工作台首页新增日报链接：http://127.0.0.1:8321/reports/daily；历史页/reports/daily/YYYY-MM-DD，JSON /api/reports/daily/YYYY-MM-DD。文件在对应数据库旁daily-reports/<account>/<date>.json/.md/.html；当前容器宿主目录data/runtime/container/daily-reports/paper-t0-container。

运行中的工作台每60秒只读生成今日/昨日，paper结束后生成当日；不调用模型/行情，不写账户。程序关闭期间不会自动运行，重启会继续更新。盘中标暂报，收盘后标日终快照；迟到费用后可重新生成，不是不可变账单。健康与日报历史读取自2026-10-14起按上海日窗口走索引（详见[flows](flows.md)），32k行合成库实测快照0.8ms/健康0.9ms/日报生成2.3ms；若运行中仍变慢，优先排查磁盘/锁竞争，而非全表扫描（[ISSUE-20261013-08](../issues/ISSUE-20261013-08-高频健康与日报查询扫描全部历史记录.md) resolved，真实长期库收益待五交易日观察）。

容器手动补历史（不启动交易）：`docker exec trademind-workbench-1 python -m trademind.runner --config config/t0-container.local.toml daily-report --date 2026-10-12`。宿主配置的storage.url须指向正确宿主数据库，容器配置里的相对路径不可直接当宿主container目录使用。

开始/交易后现金与持仓总资产分开；净入出金不计盈利、冻结已包含现金。总盈亏=期末总资产−期初总资产−净资金流，成交手续费已包含；缺期初持仓估值或有效期末价显示未确认，不以成本价冒充市价。已实现收益单列，可能包含以前持仓收益。

模型日预算费用与未知预留分别列出，扣模型估值后的经济结果不修改账本现金；费用按配置估算，不代表供应商账单。多账户库旧调用表无account_id时明细标库级；预算/审计数量不一致保留提醒。

完整有效策略历史只从新版接入后追加保存；以前缓存被覆盖的策略不能恢复。自动生成失败只记录报告错误，不改变原paper状态。[验证](../research/20261012-daily-report-verification.md)。

## 离线提示评测

只读固定案例与保存响应，不加载.env、不请求模型、不写paper账户。根目录用现有venv运行：

```text
.venv\Scripts\python.exe scripts/evaluate_prompt_cases.py --cases tests/fixtures/prompt_eval/cases.json --responses <保存响应.json> --output data/runtime/prompt-eval/report.json
```

增加`--baseline-responses <基线响应.json>`可同输入A/B，模型/配置也必须一致。退出0=pass/compared，1=失败或无效输入，2=缺档/观测不全；详细JSON与同名.md各自原子写入。没有有效响应时不重新调用模型补齐。

保存响应格式：schema_version=1、dataset_hash为evaluation.prompt_eval.fingerprint完整案例文件的值、source为synthetic或recorded；prompt_versions/model_versions各含strategy和fast，另含config_version及records。每条record有case_id、input_hash（该例input的fingerprint）、status(ok/error)、output、latency_ms、cost、usage、program。strategy的output是九字段草稿；fast的output是归一化四题answers：action含choice/confidence，其余各含noul。不要直接传未经适配的HTTP原始包。

cost含currency=CNY、kind=estimated/settled/pending及amount；usage含非负整数input_tokens/output_tokens；program含admitted_action和非负整数orders_created。缺费用/usage/耗时/程序观测明确incomplete，既有错误仍fail；已知费用小计不等于完整账单。程序观测须来自真实运行记录，评测器不核验其真实性，也不重放OMS。

一例一保存响应；若有重试，多次尝试分别评测/在原调用审计汇总，不能混成单次响应费用。报告只输出白名单统计/安全标识，不导出响应原文。合成案例和合成响应pass只证明检查器行为，不能证明真实模型或交易收益。

检查命令：`.venv\Scripts\python.exe -m pytest -q tests/test_prompt_eval.py tests/test_prompt_eval_cases.py tests/test_fast_prompt_contract.py`。真实前瞻档案仍由原Task17采集，07适配/审计尚未完成。

## 离线可达性与成本压力诊断（2026-10-25，Task25-04/05）

两个零调用、零联网、不读.env、不写生产数据的只读评估CLI（uv 环境）：

```text
.venv\Scripts\python.exe scripts/check_t0_tradability.py --snapshot <快照.json> [--max-price-age 300] [--output-json 路径] [--output-md 路径]
.venv\Scripts\python.exe scripts/evaluate_t0_cost_stress.py --bundle <档案包.json> [--output-json 路径] [--output-md 路径]
```

- 可达性：输入显式JSON快照（snapshot_version=1，tool_version=2）：账户现金/预留、持仓、挂单、逐标的最近价/来源/可见时间、冻结限额。`reserved_cash`已含挂单现金冻结、只扣一次；BUY挂单需提供side、quantity/filled_quantity（或remaining_quantity）及limit_price以计算单票/总敞口承诺，SELL不释放敞口；未知挂单或缺字段保持数据不足。缺价/陈旧/未来持仓估值不按0放行，重复持仓行累计。输出限额余量、整手含费成本、规则与输入指纹。**资金可达 ≠ 下单授权**。示例：[历史样本](../research/20261025-tradability-local-sample.md)。
- 成本压力：仅接受`bundle_version=2`：as_of档案截止、意图、后续bar的bar_time/available_at/OHLC/volume、空仓隔离账户initial_cash/min_cash_reserve/max_single_exposure/max_total_exposure、模拟费率、显式模型成本。v1包不能默认补造真实时间后冒充v2；已保存合成包显式用available_at=bar_time。8场景按提交/可见成交排序，先冻结再入账，保留限价并向不利tick取整、共享bar整手参与率。零/缺量和单价bar不假定成交；单次尝试余单取消，缺后续bar则pending/incomplete。输出现金流、未平仓权益损益、采样回撤及已结算模型估值；缺成本或pending>0使combined_net=unknown，估值非供应商账单。仅支持核验T0 ETF，不模拟完整调度/日历/模型路径重生成，不用于生产。详见[报告及输入包](../research/20261025-cost-stress-verification.md)。

## 快速链提示编辑与生效

2026-10-12用户授权重建并重新创建workbench，strategy-3/fast-2已在实际容器导入且hash与宿主一致，414容器测试/healthy/HTTP200通过。[操作与证据](../research/20261012-image-update-verification.md)。只运行工作台；下方此前未重建的描述按日期理解，交易仍须单writer入口。

主入口是 `src/trademind/agents/fast_prompts.py`（当前strategy-5/fast-2；2026-10-25起可开仓计划必须同时允许SELL且有非空可计算退出条件，草稿与离线评测同一校验入口）；strategy_examples提供独立中文配对示例，追加到系统提示，不能当当前行情。StrategyPlanner绑定输入Schema/可用字段/费用/限制及中文说明，FastDecider生成四题。更改语义时同步提升对应版本，保持Schema与程序字段所有权；不得靠降低阈值或清预算迫使买卖。旧版本计划被拒绝后仍遵守刷新冷却；无SELL的旧缓存计划按缓存未命中拒绝并在健康状态记录 `last_cache_reject_category=invalid_cached_plan` 与原因（每健康日一条，不改写历史行），按既有节流正常刷新即可恢复。

本机验证：`.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_fast_prompt_contract.py`，再跑完整回归。测试不调用真实API；提示表达效果须以后续真实档案对照确认。

容器需引擎恢复后重建镜像再重新加载，不能仅重启旧镜像。当Windows桥接异常而VM可用，使用 `rdctl shell docker-compose --project-directory /mnt/f/Project/TradeMind --file /mnt/f/Project/TradeMind/compose.yaml build`，同一入口执行 `up -d workbench`；paper仍用下方单writer helper。若两种入口都不可用先恢复Rancher，不宣称运行成功。本轮尚未重建/容器复验，见[提示验证](../research/20261011-prompt-verification.md)。

## 有界运行与采集（2026-10-10）

在根目录运行，使用pwsh.exe -NoProfile；真实验收前先核对[current-status](current-status.md)。

```text
.venv\Scripts\python.exe scripts/run_container.py check --via-rancher-vm
.venv\Scripts\python.exe scripts/run_container.py paper --steps 12 --max-seconds 900 --probe-models --via-rancher-vm
.venv\Scripts\python.exe scripts/collect_fast_archives.py --config config/t0-fast.local.toml --samples 3 --interval-seconds 300 --max-seconds 1000
```

--via-rancher-vm是Windows桥接异常时已验证的显式替代路径；正常Docker桥接可省略。使用同一compose/只读挂载/账本，代理仅子进程WSLENV传递。paper默认不带收费探针；新profile真实决策需本进程显式双模型探针，免费门禁不通过则blocked/非零。报告默认落在账本目录的paper-runs；直接runner可指定--report-output。报告ready不代表交易验收；账户权益按账本成本估值，非实时行情。

collector默认仅免费行情；真正前瞻双链档案需要上述采集命令显式追加--probe-models，仍须交易时段、完整新鲜分钟和费用门禁。输出data/runtime/fast-archives唯一JSON，旧/新独立账户与数据库，初始资金取配置`t0.initial_cash`（默认500，1000合法；零/负/非有限在建账前拒绝），bundle记录`initial_cash`与来源；不复用已有输出，不补造历史答案。采集选择本次固定一只合格标的，至少300秒间隔，最多12样本/3600秒。初次用小样本，检查两个隔离账户总费用，不能用新账户无限重试绕过额度。

离线对照：python -m trademind.runner t0-fast-compare --bundle <采集JSON> --output <对照JSON>；只消费已有档案，缺档/空档不通过，无标注不校准。结果交Task08，不能据此自动完成Task09。

运行退出禁止新模型预约，至多35秒排空未消费worker；排空对已有结果写终态审计（cancelled_before_start/model_error/expired_result_not_executed/valid_result_not_executed不锁定，worker_still_active/unknown/audit_write_failed锁定，见[ARCH-015](../architecture/15-model-drain-terminal-states.md)）；容器外层上限为max-seconds+75，强停只针对本次随机容器。停止失败不恢复workbench，先人工核对worker与账本，未知调用/费用不当0。直接宿主/collector时限是协作检查，HTTP受provider超时约束。详情见[本轮报告](../research/20261010-engineering-completion.md)。


Task18启动入口已加免费门禁。显式`paper --steps N --probe-models`闭市会blocked并退出非零，0收费/订单；无需修改配置。最新镜像重建、工作台恢复healthy/HTTP200，实际命令/预算/限制见[Task18报告](../research/20261009-task18-verification.md)。完整盘中启动尚未验收。

Task15显式容器入口完成；Task16窗口工程已验证308测试，真实盘中验收仍待执行。源码变更已触发镜像重建；工作台网络仍不自动继承入口参数。详见[Task16报告](../research/20261009-task16-verification.md)。

容器显式网络入口已实施：`scripts/run_container.py check` 或 `paper --steps N`，宿主venv运行；读取既有宿主代理，仅传子进程，不修改compose/.env网络。paper先停workbench并最终恢复，默认不收费探针。工作台本身不继承此参数。操作/验证与限制见[Task15报告](../research/20261009-task15-verification.md)。

后续变更按[Task15～18及原验收队列](../tasks/index.md#后续实施入口2026-10-09)实施；本手册只写已验证命令，规划中的新入口完成后由执行者补命令及结果。

## 显式ETF分钟源（2026-10-09）


## 免费分钟质量路由（2026-10-14）

[ARCH-020](../architecture/20-free-minute-source-routing.md) implementing（2026-10-15复核更正）。`scripts/check_fast_startup.py` 三态分离报告——`source_config`（pref是否配置）、`source_protocol`（逐源工程协议证据，来自router的`MINUTE_SOURCE_PROTOCOL_STATUS`注册表，sina_etf/akshare=protocol_qualified仅指工程证据）、`intraday_quality`（t0-data探针实测），分别给独立阻断项（no_minute_source_configured / fixture_in_paper_minute_pref / minute_source_protocol_unverified / fresh_minute_data_unverified）；检查恒返回2/blocked，不自动解除既有halted状态。排障：`runner` 内 `router.minute_source_log[证券]`（最多20条，ISO时点/provider/status/reason）与 `QualityReport.source_rejections` 给逐源失败原因；退避与每日计数在内存（进程重启清零），上限配置见[setup](setup.md#免费分钟质量路由2026-10-14)。源恢复不需要人工解锁——退避到期后下一次调用即为恢复探测；某源被每日限额拒绝时当轮按pref跳过，全部被拒则走既有拒绝路径。东财源对本机出口IP的连接封锁记录见[研究](../research/20261014-etf-minute-unit-verification.md)，恢复后可单次有界重试补验，不做高频诊断（避免再次触发WAF）。 2026-10-15复核发现的交叉观察工程缺口（[Issue15-01](../issues/ISSUE-20261015-01-跨源观察一致性与时间预算门禁不完整.md)）当日修复：观察status新增`unqualified`（观察源未来可见/陈旧/质量不合格时不作为比较依据）；超容差差异成为instrument级持续拒绝（后续pref与再请求均以note进入limit_violations并拒绝、不缓存，直至合格一致观察解除）；观察等待与候选共用整轮预算。全量570 passed。工程验收不等于盘中质量：三ETF真实双源质量仍归Task09-16。 最新独立复验补充：[Task15-02](../tasks/TASK-20261015-02-复验跨源观察四项修复.md)本轮570项全量通过，但发现已有缓存命中未检查证券级持续拒绝，仍可返回ok；Issue15-01重新open、Task14-04继续修复，完整缓存拒绝门禁未验收。配置字段/启动三态不等于此剩余边界已经通过。

分钟后台容量排障（[ARCH-023](../architecture/23-minute-request-inflight-capacity.md)，2026-10-17）：`minute_source_log`的`status=busy`及`QualityReport.source_rejections`区分同源在途已满与全路由容量已满；默认全路由4、每源1，无排队，busy不消耗每日请求或观察计数。容量只能由SDK worker实际返回/异常自动释放，日切不清活跃槽，不要手工清health或divergence冒充恢复。程序化`router.close(timeout_seconds)`拒绝新分钟SDK请求并在真实时限内join；False表示仍有活跃请求，可稍后再次有界等待，不能强制取消SDK线程。无新增CLI/TOML操作，真实SDK与容器资源效果未验。

使用现有container配置执行 `docker compose --profile check run --rm check`，不加载模型凭据；盘中未满足前报告stale正常。Key已验证；收费探针须使用已验证网络条件，2026-10-09 probe10/10已满，按翌日预算重置，不反复盲试。停止workbench后才运行共用账本paper，结束后 `docker compose up -d workbench` 恢复。

## 模型参考费率（2026-10-09）

container公共示例及container/fast local已填写：Flash输入2、输出8 CNY/百万token；JEV输入0.282786、输出0。Flash按高峰缓存未命中上限，JEV采用当日参考汇率；运行预算为估算，非供应商账单。来源、优惠时段及真实结果统一见[费率报告](../research/20261009-model-pricing.md)。价格变更需重新核验；修改local后重启workbench。当前JEV容器451、免费分钟连接与时段仍阻塞交易。

## Rancher Desktop容器入口

当前workbench已运行，http://127.0.0.1:8321 。依赖下载慢时，可本次构建指定 `docker compose build --build-arg PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple`，随后 `docker compose up -d workbench`；不改依赖版本或系统代理。

在项目根目录，启动Rancher Desktop并确认Docker引擎可用。首次复制 `config/t0-container.example.toml` 为忽略的 `config/t0-container.local.toml`，创建 `data/runtime/container` 目录；根目录.env只读挂载，不复制进镜像。

```powershell
docker compose build
docker compose up -d workbench
docker compose ps
docker compose --profile check run --rm check
```

工作台仅供本机访问 http://127.0.0.1:8321 。账户 `paper-t0-container`，数据 `data/runtime/container/trademind.db`，与宿主原模拟盘隔离。默认没有核实费率，预检会报告缺费用/时段/行情，不会自动开启交易。

费率与盘中数据验证后，停止工作台再进行有界paper试跑（同一数据库单个运行进程），结束后可重开工作台查看结果：

```powershell
docker compose stop workbench
docker compose --profile paper run --rm paper
docker compose up -d workbench
```

paper默认12轮并有启动模型探针，仍执行真实双模型、费用和市场门禁。停止服务用 `docker compose down`，挂载的数据保留；没有交易定时任务或自动重启策略。构建/运行实测以 [TASK-20261009-11](../tasks/TASK-20261009-11-Rancher容器运行.md) 为准。

## 快速链修复后的运行与回放

`jev_fast_decision` 的 `t0-paper` 用单模型 worker，主循环每5秒检查一次；`--steps` 计轮次，模型300秒节流保留。结果须在后续轮次由主线程消费，单步常为 `WAIT_MODEL_PENDING`。结束有待消费结果则 `incomplete` / 退出码2；worker无订单权限，关闭时取消未开始的计算，已开始请求受供应商超时限制。行情仍同步，5秒不是时延承诺。

`decision.diagnostics` 给出数据年龄、上下文/计划/意图期限、原因与预算，Guard原因在 `guard_reasons`。`cost_reserved_cny` 是门禁总额（已结算+待确认），`cost_settled_cny` 是有usage按配置费率计算的费用，`cost_pending_cny` 是失败/无usage等未结算预留；usage估价不代表供应商账单核验。

`t0-fast-compare` bundle 新增 `runtime_configs.old/new`，各含脱敏的完整 `t0`、`risk`、`paper` 三段；不接受供应商凭据配置。账户隔离且资金取runtime记录的 `t0.initial_cash`（默认500兼容；非正/非有限拒绝，记录paper与t0不一致拒绝，A/B两档案资金不同拒绝，比较报告按各自配置资金计算盈亏）。每条 archive 保留 `available_at`（响应完成时间）、`valid_until`、`holding_horizon_seconds`、`strategy_plan`、原账户及模型调用。完成可晚于输入，但须早于有效期且不得跨下一上下文；不以未来输入重新生成模型结果。新profile保留全局节流/持仓超时；缺配置或动作证据时 incomplete。原 `p50_ms/p95_ms` 是单模型延迟，新 `end_to_end_p50_ms/p95_ms` 是输入到响应完成延迟。

验证与限制见 [TASK-20261009-10](../tasks/TASK-20261009-10-修复快速决策架构问题.md)。

2026-10-09：[ARCH-006](../architecture/06-jev-fast-decision.md) 新 profile 的离线工程已实施（02、04～07 completed），266项测试通过；03真实认证/费用、08真实对照校准、09真实启动仍未完成。旧默认链保留，新profile须显式选择。见 [验证报告](../research/20261009-fast-decision-verification.md)。

## 新链操作与门禁

本次启动预检未通过，没有服务运行；真实新链不能据266测试通过启动五日。

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-fast.local.toml t0-fast-check
.venv\Scripts\python scripts/check_fast_startup.py --timeout 40 --output data/runtime/fast-startup-check.json
```

两条只读、不加载密钥、不调模型、不写订单。前者核配置/费率/时段，后者有界免费行情检查。新例配置价格默认空，先核验费率；还需修复分钟连接与官方模型认证。

准备条件通过后，显式有界paper入口：

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-fast.local.toml t0-paper --steps 1 --probe-models
```

此命令会加载已有密钥并在同一进程调用两模型探针及决策，全部受日预算；费用/认证失败不跑后续交易。不要用旧账户/数据库替代新隔离配置。不开启服务，Ctrl+C停止；重启沿用同一账户预算和未过期计划，OMS恢复挂单，跨日计划不复用。纯shadow需单独mode/account/database，Guard始终不提交订单。

离线对照：

```bat
.venv\Scripts\python -m trademind.runner t0-fast-compare --bundle YOUR_ARCHIVE_BUNDLE.json --output data/runtime/fast-comparison.json
```

bundle含按时间递增的ContextSnapshot列表、run_id、artifact_kind(recorded/fixture)、archives.old/new按context_id索引；每项须有facts_hash、available_at、action、target_weight、account(cash/reserved_cash/positions)、model_calls。快照决策时点必须已经能看到归档响应，未来响应拒绝；事实hash由evaluation.fast_comparison.facts_hash生成。独立账户状态不匹配标缺失，不重写响应。无归档/成本/标签时报告未完成，不在线补历史。当前fixture报告只证明工具可运行，08的真实对照/校准仍待补。

## 最近影子模拟启动检查

2026-10-08 23:23有界只读检查已执行，启动受阻，未启动决策或常驻进程。结果见[报告](../research/20261008-shadow-startup-check.md)，原始脱敏结果在本地`data/runtime/shadow-startup-20261008.json`。先完成行情与真实双模型门禁，再使用下方统一T0配置命令试跑；不能把t0-data检查成功当作模拟交易成功。

## 多市场观察与购买条件筛选

仓库根目录运行：

```bat
.venv\Scripts\python -m trademind.runner --config config/market-access.local.toml market-data
.venv\Scripts\python -m trademind.runner --config config/market-access.local.toml market-data --markets convertible_bond,futures
.venv\Scripts\python -m trademind.runner --config config/market-access.local.toml market-bars 110072.SH --kind convertible_bond --period 1
```

默认五市场扫描，每次覆盖本地报告`data/runtime/market-observation.json`，不启动常驻监控。完整返回0，连接失败/空数据/部分扫描返回1。market-bars支持五类与1/5/15/30/60分钟，输出JSON，不用于模拟成交。网络结果见[报告](../research/20261008-multi-asset-verification.md)。

confirmed_permissions只填已确认的cn_fund、convertible_bond、hk_stock（香港直接账户）、futures；未填unknown。港股通不当作全港股权限，需Task-06逐只核验。cash_cny/reserve_cny和cash_hkd/reserve_hkd分开计算，不换汇、不修改旧账户本金。

rules_file可指定JSON（复制空market-rules.example.json为market-rules.local.json），根键为513050.SH、00700.HK、RB2610.SHFE等规范ID。字段：source_url核验依据https链接、t0布尔、lot_size整数、currency、fee_buffer实际费用保守缓冲；期货另含contract_multiplier、margin_rate，可设effective_until。必须核实，不填未知数字制造通过；不含账号/Key。最小资金=价格×数量单位＋费用，期货再乘合约乘数×保证金率；不等于完整期货风险预算。

purchase_conditions_met仅配置筛选；paper_candidate还需时效和现有引擎，但不触发订单、不代替双模型/Guard/OMS门禁。新市场仅观察。工作台API只读报告，按类型显示前50条及报告年龄；页面刷新不抓行情、不调用模型。

## 服务器与部署目标

无部署目标；本地单进程运行（V1 范围）。

## 部署与进程

- 回放/演示：CLI 命令直接运行。
- 管理端：`serve` 命令前台运行 uvicorn（Ctrl+C 停止）。
- 数据库：SQLite 单文件。备份优先用存储层命令 `db.backup_to(target)`（WAL 一致快照，目标已存在则拒绝覆盖）；恢复=复制到独立位置后 `verify_database(path)` 校验（integrity/外键/Schema版本/悬挂引用），通过后再以新库打开。旧“停进程后复制 `data/runtime/*.db`（含 -wal/-shm）”方式仍可用。打开旧库时按 schema_migrations 顺序原地升级（当前 v1→v2，附加列/索引，不改写既有数据）；程序遇到更高 Schema 版本拒绝启动（SchemaVersionError），须用更新后的程序打开，不自动降级。

## 关键路径

| 路径 | 内容 |
|---|---|
| src/trademind/runner.py | CLI 入口 |
| data/runtime/ | 运行数据库（gitignore） |
| config/trademind.toml | 本地配置（gitignore） |

## 配置文件

config/trademind.example.toml → 复制为 trademind.toml 后修改；密钥走环境变量优先。

## 管理员设置：密钥与服务地址（2026-10-21）

工作台「管理员设置」区块替代手工编辑 `.env` 配置 DeepSeek/JEV 密钥、服务地址与 Tushare Token：保存原子写入 gitignored 的 `data/runtime/admin_settings.json`，**重启 serve/auto 进程后生效**；列表即时显示已保存覆盖的待生效掩码值（`****`+末4位），完整密钥不回传、不落日志。全部接口要求控制令牌（`TRADEMIND_API_TOKEN`，未配置则整体禁用，与控制端点一致）。优先级：系统环境变量 > 管理页覆盖 > `.env` > TOML。管理令牌自身与模型型号不可在页面修改（防自锁/型号锁定）。轮换流程：粘贴新值→保存→重启进程→列表来源显示"管理页"即生效。[ARCH-024](../architecture/24-admin-runtime-settings.md)

LLM 线协议切换（2026-10-21，[ARCH-025](../architecture/25-llm-multi-protocol.md)）：管理员设置中的 `llm_protocol`（env `TRADEMIND_LLM_PROTOCOL`）在 `openai_chat`（DeepSeek 官方，默认）/ `anthropic_messages` / `openai_responses` / `gemini_generate_content` 间切换；Base URL 留默认时按协议自动使用官方端点，自定义中转地址原样生效。切换后重启 serve/auto 生效；协议与模型型号独立，T0 型号锁定不受影响。四协议均经离线 mock 验证，真实调用与盘中效果未验。

## 常用命令（真实 Provider 能力未验证）

```bat
:: 状态（含 stub 标记与 kill switch）
.venv\Scripts\python -m trademind.runner status
:: 一次完整决策（无密钥=stub 链，输出标注 stub=true）
.venv\Scripts\python -m trademind.runner demo-decision 600036.SH
:: 轮询成交
.venv\Scripts\python -m trademind.runner poll-fills
:: 离线回放评估（默认最近 40 个交易日）
.venv\Scripts\python -m trademind.runner replay-demo
:: DeepSeek 能力探针（需先配置 key；输出脱敏，不打印密钥）
.venv\Scripts\python -m trademind.runner probe-llm
:: 崩溃恢复：UNKNOWN 对账 + 账本重建
.venv\Scripts\python -m trademind.runner recover
:: 管理端（默认 127.0.0.1:8321，浏览器打开即工作台）
.venv\Scripts\python -m trademind.runner serve
```

## 端口与网络

8321（管理端，仅本地回环）。DeepSeek/JEV/行情访问在真实 provider 启用时按需出网；replay 强制 fixture/stub，完全离线；paper 使用的网络来源由配置决定。

## 应急操作

- 暂停新增风险：kill switch（页面按钮或 API `/api/control/kill_switch?engaged=true`，需 token）；持久化，重启不失效；不自动平仓。
- 恢复运行前：`runner recover`（UNKNOWN 订单必须显式对账）。
- 第二写者冲突：任何写入口在另一进程持同库同账户写租约时立即失败，输出JSON `WriterLockHeld`（含锁文件路径与持有者元数据）且退出码1、零调用零写入；持锁进程死亡后内核自动释放，无需删锁文件（[ARCH-017](../architecture/17-writer-lease.md)）。
- 部分持仓行情失效：该标的跳过、不补价不交易，paper报告`diagnostics.valuation_failures`列明标的与异常；其他持仓风险退出照常，新开买入在估值完整前被`incomplete_account_valuation`否决（[ARCH-016](../architecture/16-position-valuation-isolation.md)）。

## 独立离线校验入口（2026-10-07已执行）

- `.venv/Scripts/python.exe -m pytest`：133 passed（2026-10-07 当时基线；当前基线 481 项全部通过，`tests/test_multi_asset.py` 因环境缺 pandas 持续排除）。
- `.venv/Scripts/python.exe docs/research/20261007-audit-repro.py`：40 项缺陷回归，临时库，无真实 Provider 请求。
- `.venv/Scripts/python.exe scripts/verify_install.py`：干净环境安装/测试、wheel 资源及仓库外 replay-demo 校验。

ReplayRunner 默认新内存库，重复运行互不污染；显式 db_url 仅允许没有运行记录的新库。回放不评估真实模型收益。在线旧 fixture 无新行情时拒绝决策是正常边界；真实观察先配置并验证数据来源。未知订单只允许显式 ACKNOWLEDGED/CANCELLED/REJECTED 对账，有成交必须提供入账证据。

## T0统一配置运行（2026-10-08）

先按[配置说明](setup.md#t0模型预算配置2026-10-08)准备独立TOML。--config放在子命令之前，所有命令使用同一账户/库/模式：

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml status
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --instrument 513050.SH --steps 1
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml serve
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml recover
```

配置中的api.port决定页面端口（示例8322）。steps每次等quote_eval_interval_seconds后读取当前时间，先轮询已有订单再决策；不会加一分钟读取未来行情。Ctrl+C停止；之后用同配置recover恢复。status也用同配置，否则查看的是默认账户。

返回码：0为当前provider能力已验证且本轮无决策错误；1为初始化/决策错误；2为本轮无错误但stub或provider能力尚未验证。安全WAIT不等于成交成功，订单可能等待下一条可见分钟。JEV问题和真实启动仍未验收，不以exit0/500初始现金当作完整闭环证据。详见[复验报告](../research/20261008-non-jev-fix-verification.md)。

## 2026-10-08 T0已登记全池更新

默认全池命令：`.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --steps 1`。--instrument省略轮询3只；加--instrument可只检查指定一只。免费连通检查：`.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-data`，模型调用0，不读.env；返回incomplete时看各标的source_errors。--steps为全池轮数，每轮等待60秒，Ctrl+C停止；本轮测试未启动持续监控。


## 股票广域观察与验证

以下入口只获取公开行情并写观察 JSON，不调用模型、不建账、不下单。它们不是后台全市场持续监控服务：

```bat
.venv\Scripts\python scripts\scan_stock_candidates.py --output-dir data/candidates
.venv\Scripts\python scripts\verify_stock_tradability.py --output-dir data/candidates --sample-size 60
```

验证脚本默认选择最新候选文件，可用 `--pool` 显式指定。输出位于 `data/candidates/stock_pool_<日期>.json` 与 `stock_tradability_<日期>.json`；核对文件内时点、missing_checks、样本范围及失败项，不能把观察名单全部视为可交易名单。抽样脚本的历史资金参数不代表当前 ETF 账户参数。

`config/t0-stock.example.toml` 是离线股票链示例，不能仅复制后视为可运行交付；模型费率、标的、股票行情路由、跨账户预算和退出接线须先按 [Task27-06](../tasks/TASK-20261027-06-盘中验收与对照.md) 核验。部署记录与当前开关状态统一看 [current-status](current-status.md)。
