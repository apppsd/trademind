# 开源项目借鉴扫描（2026-10-26 夜间，Task-20261026-24）

目的：为"个人大模型模拟交易系统"寻找可借鉴的成熟实践。结论先行——**架构不换框架**，只吸收运维与评估纪律。扫描范围：freqtrade、NautilusTrader、qlib 及个人算法交易运维讨论。

## 采纳

| 借鉴点 | 来源实践 | TradeMind 落地 |
|---|---|---|
| 无人值守告警 | 个人 bot 社区标配：状态突变即推送 | `observability/notify.py` + 监督器 halted/日历不足一次性 webhook（Task26-23，默认关闭 `TRADEMIND_NOTIFY_WEBHOOK`） |
| health-check 包裹交易逻辑 | r/algotradving 实践：所有交易逻辑外裹健康判据 | 晨间体检 `scripts/check_morning_readiness.py`（Task26-22）+ 监督器既有心跳 |
| dead-man switch | 心跳停了必须由外部发现（进程自己死了无法自报） | `scripts/watchdog_heartbeat.py`：心跳>120s 陈旧→本地告警文件+webhook；**计划任务注册由用户决定**（见 operations） |
| 当日账本备份 | 通用数据安全 | run_local start 当日自动 `backup_to`（Task26-22） |

## 采纳但需数据积累（暂缓实施，条件明确）

- **影子对照常态化**（freqtrade：dry-run 与实盘并行对照，检测系统性漂移）：对应本项目的 `t0-fast-compare` / 确定性基线对照。条件：五日观察产生真实样本后，把对照写入日报。现在做是零样本伪统计。
- **标准绩效指标**（最大回撤/胜率/盈亏比进日报）：同样等待真实成交样本；cost_stress/replay 评估器已具备计算能力，接入口留待五日观察后。

## 不采纳（含理由）

- **NautilusTrader / qlib / backtrader 框架引入**：架构重写级代价，违背本项目"单进程 SQLite、最小依赖、契约冻结"设计；其事件溯源思想本项目已用账本事件实现。
- **付费数据源、外部 dead-man 云服务**（healthchecks.io 类）：需要账号/付费，违反"不默认购买付费数据"边界。
- **institutional 风控监控产品**（Eventus 等）：非个人规模。

## 来源

- [freqtrade Strategy Quickstart（dry-run 对照纪律）](https://www.freqtrade.io/en/stable/strategy-101)
- [freqtrade basics（dry-run/live 循环）](https://www.freqtrade.io/en/stable/bot-basics)
- [freqtrade issue #7300（dry-run 与 live 并行对照）](https://github.com/freqtrade/freqtrade/issues/7300)
- [r/algotrading：100% 自动化系统讨论（health check 包裹）](https://www.reddit.com/r/algotrading/comments/1ar00k6/who_has_a_100_automated_system_does_algo_trading)
- [NautilusTrader（评估后不引入）](https://nautilustrader.io/)
