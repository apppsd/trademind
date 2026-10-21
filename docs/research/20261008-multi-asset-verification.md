# 多市场接入验证（2026-10-08）

关联 [TASK-20261008-05](../tasks/TASK-20261008-05-多市场行情接入.md)、[ARCH-005](../architecture/05-multi-asset-market.md)。

## 实现与来源

行情发现：ETF fund_etf_spot_em、LOF fund_lof_spot_em、可转债 bond_zh_hs_cov_spot、港股 stock_hk_spot_em；期货 futures_symbol_mark 展开各品种 futures_zh_realtime 合约。清理期货连续代码、按实际交易所名称识别交易所，能源合约单独INE。一次全扫描预算60秒、单HTTP请求12秒，可配置；预算耗尽记not_scanned，不能声称覆盖全市场。

按需分钟数据：fund_etf_hist_min_em、fund_lof_hist_min_em、bond_zh_hs_cov_min、stock_hk_hist_min_em、futures_zh_minute_sina。成交量保留provider_native_unverified，不猜单位、不合成缺失成交额；港币独立。未来/非法OHLC行拒绝。抓取时点只代表available_at，不代表行情时点，也不代表可用于历史PIT回放。

接口核对：本机AKShare1.19.1签名及源码；[基金官方SDK文档](https://github.com/akfamily/akshare/blob/main/docs/data/fund/fund_public.md)、[债券文档](https://github.com/akfamily/akshare/blob/main/docs/data/bond/bond.md)、[期货文档](https://github.com/akfamily/akshare/blob/main/docs/data/futures/futures.md)、[股票文档](https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock.md)。规则来源：[上交所可转债细则](https://www.sse.com.cn/lawandrules/sselawsrules2025/bond/trading/currency/c/c_20250606_10781040.shtml)、原ETF白名单逐只source_url；其他品种规则需补充官方/券商依据，不能从名字认定T0。

## 免费真实探针

命令：`.venv/Scripts/python -m trademind.runner --config config/market-access.example.toml market-data`。

| 市场 | 得到的观察记录 | 结果 |
|---|---:|---|
| ETF | 0 | ProxyError |
| LOF | 0 | ProxyError |
| 可转债 | 327 | 批量报价成功 |
| 港股 | 0 | ProxyError |
| 期货 | 724 | 86个品种接口返回；83项partial包含被过滤的连续等非实际合约代码，3项ok |

合计1051个独立instrument_id。全量报告本地`data/runtime/market-observation.json`（忽略Git），CLI按市场汇总。返回incomplete/退出1；不能据此声称所有市场连通。时点及分钟单位尚未通过交易时段质量验收，快照不送OMS。327/724是本次返回数，不是长期固定数，也不是用户可购买数。

另取报告首只可转债110072.SH请求1分钟数据（8秒请求、12秒扫描预算），返回ProxyError。无模型调用、无凭据读取、无系统代理变更。不能因为新浪快照可用就认定东财分钟接口可用。见[连接Issue](../issues/ISSUE-20261008-04.md)。

## 离线验证

227项全回归通过，1条既有Starlette/httpx弃用警告。新增24项（含参数化）覆盖五市场发现/分钟路由、真实SDK期货目录格式、合约过滤、错误隔离、时间未知/未来、坏价与OHLC、人民币港币隔离、保证金/数量/费用/权限/规则门禁、扫描截止、SDK请求超时恢复、CLI不读dotenv、只读API不初始化模型，以及新资产不能误套A股执行profile。

完整回归进程将ENV_FILE_PATH与默认本地配置改为不存在的临时路径，移除进程TRADEMIND_* / DEEPSEEK_API_KEY，封锁HTTP；未读取真实密钥。页面静态入口与API已实现，未启动常驻服务，未做真实浏览器视觉验收。

## 购买条件与边界

用户尚未回复账户权限/本金问题。默认确认权限为空、CNY500预留100、HKD0；条件通过和paper候选均为0。含明确权限、T0、数量单位、费用缓冲、资金匹配才显示purchase_conditions_met；这个结果不是券商授权或订单授权。只有已有核验ETF引擎execution_supported=true，仍需现有分钟时点、配置白名单、双模型、Guard与OMS完整门禁。

跨境LOF/ETF需逐只核对；港股通不能当作香港直连账户的全港股权限；期货需合约权限、保证金/乘数/日历/到期等完整规则。上述执行扩展见后续Task-06/07/08/09，当前不伪装成完成。
