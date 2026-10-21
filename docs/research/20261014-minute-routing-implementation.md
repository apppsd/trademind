---
id: RESEARCH-20261014-MINUTE-ROUTING-IMPL
type: research
title: 分钟质量驱动多源路由实施验证（TASK-20261014-04）
created: 2026-10-14
author: Claude Code
related: [TASK-20261014-04, ISSUE-20261014-01, ARCH-020]
---

# 分钟质量驱动多源路由实施验证

Task 14-04 的工程验证记录。四组反例的初始复现在 [20261014-free-minute-sources.md](20261014-free-minute-sources.md)（Codex 核查）；单位证据与连接失败在 [20261014-etf-minute-unit-verification.md](20261014-etf-minute-unit-verification.md)（Task 14-03）。本文只记录实施后的离线证据，不覆盖前述原始观察。本轮无网络请求、无模型调用、无凭证读取。

## 四反例修复证据（tests/test_minute_source_routing.py）

| 反例 | 修复前 | 修复后（测试钉住） |
|---|---|---|
| 主源空（支持分钟） | 直接返回空，备用0次 | `test_empty_from_capable_primary_falls_back`：bad.calls=1→rejected，good.calls=1→selected，selected_provider=good |
| 主源无效OHLC | 质量失败仍返回主源 | `test_invalid_ohlc_primary_falls_back`：备用选中，主源拒绝原因含在 source_rejections |
| 主源行仅as_of后可见 | 未来行进入结果 | `test_future_visible_primary_falls_back`：可见性过滤后空→备用选中 |
| 主源陈旧 | 陈旧窗口直接返回 | `test_stale_primary_falls_back`：as_of+180s 判定陈旧→更鲜备用选中 |

全陈旧时不再抛异常：返回最不坏窗口（最多可见行、再最新）+ 逐源拒绝原因，交既有下游门禁拒绝——`test_all_stale_returns_report_instead_of_raise`。

## 有界性、限额与恢复

- 单源有界等待：`test_slow_source_bounded_by_per_source_timeout`（源延迟1.5s、超时0.3s→备用0.9s内选中；守护线程 join 超时弃取，覆盖 akshare 内部不可配 15s 超时）。
- 轮预算：`test_round_deadline_bounds_total_wait`（预算0.4s、两源各慢1s→只调用第一源即以预算拒绝结束，不对第二源发起注定超时的请求）。
- 退避与恢复：`test_backoff_skips_then_recovery_probe_retries`（失败后跳过→退避到期后下一次调用即恢复探测）。
- 每日限额：`test_daily_request_limit_stops_source`（达到限额后 RuntimeError，原因含 "daily request limit reached"）。
- 设计修正：空/陈旧属 (instrument, window) 属性，**不触发**源级退避（否则单标的临时空结果会让该源对后续标的沉默一个退避周期）；退避仅由传输异常与质量违规触发。

## 缓存

- 命中：`test_cache_hit_within_freshness_is_used`（30s 内同参数零额外请求）。
- 陈旧穿透：`test_stale_cache_falls_through_to_refetch`（缓存窗口相对新 as_of 陈旧→重新取数，不阻塞备用）。
- 隔离：`test_cache_isolated_by_pref`（不同 pref 元组不串缓存）。
- 重放兼容：缓存命中按可用性过滤后可直接返回空结果（"available 后才复用"的 TASK-08 语义，`test_t0_market_pool::test_live_observation_reused_only_after_available_time` 钉住：call_count=1 且 available 前为空、后可得）。

## 稀疏交叉观察

- 容差拒绝：`test_cross_check_divergence_refuses_and_reports`（同 bar_time 收盘差 6.7% > 0.1% 容差→两候选均拒绝、limit_violations 含 "cross-source divergence"、该窗口不缓存）。
- 默认关闭零额外请求：`test_cross_check_off_by_default_adds_no_requests`。
- 区间门控与一致通过：`test_cross_check_sparse_interval_and_agreement`（未到间隔不观察；一致时 report.ok）。

## 兼容性（既有语义钉住，实施中修复的三处回归）

1. `test_sina_etf::test_route_preserves_real_amount_and_fetch_availability`：无 as_of 的取数路径不过滤 available_at（历史窗口 + 真实下载时刻的行必须透出）；as_of 过滤仅当调用者传入。
2. `test_t0_market_pool::test_etf_errors_are_not_hidden_or_fallback_to_stock`：错误类别记录根因类型（DataUnavailable 的 `__cause__`，如 ProxyError），股票接口不回退。
3. `test_fast_archives::test_shared_market_inputs_keep_two_actual_accounts_separate`：FrozenMinutes 接受并忽略新参数，冻结快照行为不变。

## 回归命令与结果（2026-10-14，Windows，.venv）

```
.venv\Scripts\python -m pytest tests/test_minute_source_routing.py tests/test_free_minute_sources.py tests/test_etf_minute_data.py tests/test_market.py tests/test_fast_startup.py -q
→ 75 passed
.venv\Scripts\python -m pytest tests/ -q        （全量，无 --ignore）
→ 563 collected，562 passed；唯一失败 test_check_docs（文档同步完成前），同步后复跑 0 错误
```

## 边界

- 全部为离线/隔离验证；真实盘中双源切换、延迟、缺分钟恢复由 Task09-16 在上海交易时段验收。
- 本地生产配置（t0-fast.local.toml / t0-container.local.toml）未切换，运行实际仍 sina_etf 单源。
- 东财备用源对本机出口 IP 不可达（Task 14-03 研究报告），真实切源路径未在盘中观测。
- `MINUTE_SOURCE_PROTOCOL_STATUS` 的 protocol_qualified 仅指工程协议证据，不主张盘中质量。

## 2026-10-15 修复（ISSUE-20261015-01，同日独立复核后）

独立复核确认交叉观察四项工程缺口后当日修复（仅改 router.py 与 test_minute_source_routing.py，对外签名不变）：

| 缺口 | 修复 | 回归钉住 |
|---|---|---|
| 非零间隔下差异被改选源洗掉（round1差异→backup以ok=True选中并缓存） | `_divergence`（instrument→note）：超容差后后续pref候选与再请求一律把note写入limit_violations并拒绝、不缓存；interval/cap/预算/观察源不合格均不解除；合格且一致的观察解除并缓存 | `test_cross_check_divergence_refusal_survives_pref_and_rerequests`（900s间隔三轮：拒→再请求仍拒→一致观察解除）、`test_cross_check_daily_cap_and_day_switch_do_not_launder_divergence`（cap耗尽仍拒；北京日切换重置计数后重新观察，仍不一致仍拒） |
| 观察等待不计入整轮预算（0.1s预算实测约0.297s） | `_maybe_cross_check`接收deadline，观察等待=min(per_source, 剩余)；剩余≤0不发起请求、不消耗计数（status=skipped） | `test_cross_check_observer_waits_within_round_budget`（0.1s预算内完成，观察源无退避标记）、`test_cross_check_skips_observation_when_round_budget_spent`（零请求零消耗） |
| 首次观察依赖单调时钟值（last_mono=0：进程运行<interval即跳过；复核环境562/1失败的根因） | `last_mono=None`显式"从未观察"必观察；router时钟可注入`_clock` | `test_cross_check_first_observation_independent_of_monotonic_value`（时钟值10<interval=900仍观察）；`test_cross_check_sparse_interval_and_agreement`改FakeClock确定性覆盖初始/间隔内/间隔后 |
| 观察源未沿用as_of接受策略（未来可见行算出6.67%差异；_rep未检查） | 观察按同一as_of可见性过滤+同一normalize质量检查+同一quote_max_age新鲜度；不合格记status=unqualified，不比较、不算一致、不解除既有拒绝 | `test_cross_check_future_visible_observer_not_a_comparison_basis`、`test_cross_check_stale_and_invalid_observers_not_a_comparison_basis`（陈旧/质量不合格均unqualified） |

附加设计修正5：差异拒绝不再对候选源记退避（责任在源对之间不明确；持续拒绝由instrument级状态承担，请求消耗由每日限额约束）——避免修复缺口1时引入退避倍增与跨标的源沉默（ARCH-020附录）。

命令与结果：
- `python -m pytest tests/test_minute_source_routing.py -q` → 24 passed（17→24）。
- 相关八套件（minute_source_routing / free_minute_sources / etf_minute_data / market / fast_startup / fast_archives / t0_market_pool / context）→ 103 passed。
- 全量 `python -m pytest tests -q` → 570 collected，570 passed，0 failed（复核基线563 collected/562 passed/1 failed + 7新增；复核失败项转绿）。
- `python scripts/check_docs.py` → 0错误/36提醒（既有提醒）。

边界不变：全部离线/隔离验证；真实盘中双源质量归Task09-16；本地配置未切换；东财本机不可达未复测。

## 2026-10-15晚修复二（复验Task15-02/综合验收Task15-03确认的缓存分支残留）

| 缺口 | 修复 | 回归钉住 |
|---|---|---|
| 缓存命中未检查instrument级`_divergence`：同pref窄→宽→窄，差异拒绝设置后仍温缓存返回ok=True、violations=[]、零新调用 | `get_minute_bars` 缓存命中条件加入 `instrument_id not in self._divergence`——拒绝期间该证券任意pref/周期缓存键统一不命中，落入重新取数路径写入limit_violations；合格一致观察解除后缓存路径自然恢复 | `test_cross_check_standing_refusal_not_bypassed_via_cache`（修复前第3步calls=(3,3)不变+cached_violations=[]；修复后calls=(4,4)，ok由新鲜一致观察支撑） |

验证：路由套件25 passed；全量577 collected / 577 passed / 0 failed；check_docs 0错误。边界不变：真实盘中双源质量归Task09-16；本地配置未切换。
