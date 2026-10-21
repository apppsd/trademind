---
id: RESEARCH-20261014-ETF-MINUTE-UNIT
type: research
title: 免费ETF分钟源单位核验与连接失败记录（TASK-20261014-03）
created: 2026-10-14
author: Claude Code
related: [TASK-20261014-03, ISSUE-20261014-02, ARCH-020]
---

# 免费ETF分钟源单位核验与连接失败记录

执行 Task 14-03 的实际核验报告。初始证据见 [20261014-free-minute-sources.md](20261014-free-minute-sources.md)（Codex 规划核查，本文不覆盖其原始观察）。

## 环境

- Windows 11，Python 3.12.10（.venv），akshare 1.19.1，pandas 3.0.6，requests 2.34.2。
- akshare 1m 路径的 pandas 兼容性已离线验证：`fund_etf_hist_min_em` period=1 内部的 datetime 索引字符串切片与 `pd.to_numeric` 在 pandas 3.0.6 下正常。
- 本轮未读任何凭据，未调用收费 API。

## 来源矩阵（2026-10-14 核验）

| 上游 | 证券类型 | 周期 | 字段/单位 | 复权 | 时间语义 | 免费期限/额度 | 超时能力 | 实际合格状态 |
|---|---|---|---|---|---|---|---|---|
| 新浪 quotes.sina.cn（SinaETFAdapter） | 白名单ETF | 1m | open/high/low/close/volume/amount；份额口径由 Task09-16 盘中验收 | 未复权 | `day` 北京时间；仅返回已闭合分钟 | 公开免费，未验证长期稳定性 | requests timeout=8 | 主源；盘中验收归 Task09-16，本文不重复判定 |
| 东财 push2his（AKShare `fund_etf_hist_min_em`） | ETF（secid=116.*） | 1m | 时间/开盘/收盘/最高/最低/成交量/成交额/最新价；成交量=手（见下） | 1m 走 trends2 天然不复权（adjust 参数被忽略）；≥5m 走 kline，`fqt` 由 adjust 映射，本适配器显式传 `""`（不复权） | 北京时间分钟；仅服务近 ~5 交易日；客户端按窗口切片 | 公开免费网页接口，无额度承诺 | akshare 内部硬编码 timeout=15，不可配；路由层总预算见 Task14-04 | **未合格：三ETF真实取样未完成（本轮连接失败，见下）** |
| 东财 `stock_zh_a_hist_min_em`（股票分钟） | A股股票 | 多周期 | 原样透传 | qfq（既有行为） | 北京时间 | 同上 | 同上 | 范围外，未改未验 |
| BaoStock / Tushare | — | 无 ETF 1m 接口 | — | — | — | — | — | 排除（沿用规划研究结论） |
| 必盈 biyingapi | — | — | — | — | — | 文档页面超时；200条/日永久 vs 7天试用政策矛盾未解 | — | **保持禁用**，直到免费 ETF 1m 能力有证据 |

未知项一律标为未验证，不推测。

## 东财ETF 1m 成交量单位：接口级证据

1. **官方文档样本**：AKShare 官方文档 `fund_etf_hist_min_em` 示例行 2024-03-20 09:31：成交量=413、成交额=420929 元、收盘=10.192 元。`420929/(413×100)=10.1920`，与收盘价一致；按份额直读则为 1019.2 元/份，荒谬。该接口输出列为手，1 手=100 份（ETF 最小报价单位 0.001 元、100 份一手）。
2. **实现**：`AKShareAdapter` 仅在 `fund_etf_hist_min_em` 分支按接口级常量 `_ETF_MINUTE_LOT=100` 将成交量换算为份额；`stock_zh_a_hist_min_em` 股票分支保持原样透传，不对其他供应商一律乘 100（遵守规划研究警告）。
3. **自检安全网**：每行校验 `成交额/份额 ∈ [low×0.98, high×1.02]`。若上游某日把成交量改为份额（或任何其他口径），换算后量额关系必然破坏，**整源显式拒绝**（DataUnavailable），绝不静默错单位进入订单链。零成交分钟要求成交额同时为 0。
4. **1m trends2 的单位仍属同一未验项**：官方文档示例无法区分其采集自 trends2（1m）还是 kline（≥5m）路径；1m trends2 的成交量单位须由真实样本确认。离线测试与量额关系自检保证即使单位假设错误也是显式拒绝而非错误数据。

## 闭合与时间语义（已实现）

- 仅接受北京时间可解析分钟；不可解析/未知时间含义整源拒绝。
- `closed_before` = 取数时刻（北京）向下取整分钟；`bar_time ≥ closed_before` 的进行中分钟及任何未来时间戳行被排除，不返回未闭合数据。
- `fetched_at = available_at = 实际下载时刻`，不回填历史可见性（下载语义，与模块 docstring 一致）。
- 窗口外行过滤由适配器自行完成（1m 时上游为全量 5 日客户端切片）。

## 三ETF真实取样：未完成（连接失败记录）

取样方式：经修复后 `AKShareAdapter.get_minute_bars` 的有界公开免费探针（单次调用/标的，requests 强制 ≤10s），2026-10-14 22:45–22:52 +08:00。

| 时点(+08:00) | 尝试 | 错误类别 |
|---|---|---|
| 22:45:06 | 三ETF经系统代理 | ProxyError（Windows 系统代理不可达） |
| 22:45:26 | 清除代理环境变量后重试 | ProxyError（代理来自注册表） |
| 22:46:11 | 强制直连 | ConnectionError（RemoteDisconnected，远端直接断开） |
| 22:46–22:52 | 诊断矩阵（curl / stdlib urllib / http.client / requests，IPv4 与 IPv6） | 早期 curl×2、urllib×1 返回 200 且返回 JSON 信封，随后全部连接被远端重置；单一 curl 复测同样被重置 |
| 22:55:06 | 冷却约 10 分钟后单次重试 | ConnectionError（RemoteDisconnected），三标的同样失败 |
| 23:29:16 | 全部实施完成后最终单次重试（有界，单次/标的） | ConnectionError（RemoteDisconnected），三标的同样失败 |

诊断结论：端点可达性曾短暂成立（HTTP 200 + JSON），随后当前出口 IP 对所有栈（含 curl/schannel）被统一重置，符合东财 WAF 对高频诊断连接的临时软封禁模式；**非单位或代码问题，本地无法在该窗口完成取样**。

按验收标准：513050.SH、513100.SH、518880.SH 的东财 1m 真实样本核验**保持未合格**，未伪造任何样本；换网络/时段后的有界单次重试即可补验。不据此自动关闭 ISSUE-20261008-04 / ISSUE-20261007-13。

## 离线回归

- 新增 `tests/test_free_minute_sources.py`（12 项，stub akshare 模块，无网络）：官方 511220 换算证据、份额只换算一次、单位变化显式拒绝、缺成交额列拒绝、字段变更拒绝、时间不可解析拒绝、无效 OHLC 拒绝、零成交语义、未闭合/未来行排除、非白名单 ETF 拒绝且不发起请求、连接失败保留错误类别、股票分支原样透传且 qfq 不变。
- 既有 `tests/test_etf_minute_data.py`、`test_market.py`、`test_sina_etf.py` 全部通过（本报告时点 4 套件 57 passed）。

## 后续边界

- 东财备用源 qualified 与否由真实样本决定，属 Task09-16 盘中验收口径；本报告只交工程证据。
- 路由层有界等待/退避/额度（含 akshare 不可配 15s 超时的处理）归 Task-20261014-04。
