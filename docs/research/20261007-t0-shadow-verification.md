# T0 影子模拟官方来源核验

日期：2026-10-07。只浏览公开文档，没有读取 .env，没有请求带凭据的 API。文档核验与运行验证分开。

## 外部 JEV

此前“无官方来源”的结论已更新。TypeSafe 官方 [API](https://docs.typesafe.ai/api) 给出 POST https://api.typesafe.ai/v1/systemone，以 state、model 和 questions map 请求，返回 answers map、实际 model 与 usage。支持 Choice、Score、Noul。模型别名可用 jev-latest，实验应记录实际返回版本。

[官方 confidence 说明](https://docs.typesafe.ai/confidence)：Choice/Score 的置信度源自选项分布，Noul 不单独返回 confidence。其判断语义取决于题目，不能直接认作市场胜率。

已观察本地 models/stub.py 仍使用自定 /v1/judgments，不能仅设置官方 base_url 就启用。TASK-41 负责协议适配、探针与脱敏。官方文档存在不代表本地 Key/地区/余额/模型可用。

## ETF 规则

[上交所 ETF 常见问题](https://www.sse.com.cn/assortment/fund/etf/question/)说明不同 ETF 的交易机制有区别；必须逐证券核验当日回转资格及交易单位。该通用页面不足以决定具体白名单、价格限制、费用或当前报价。TASK-42 保存每个采用证券的交易所/基金公告链接、日期和规则版本；深交所证券使用对应官方规则，不能照搬上交所。

## 未核验项与后续

补充免费源文档核验：[AKShare基金文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)包含fund_etf_spot_em快照与fund_etf_hist_min_em分钟接口；1分钟记录文档称仅近5交易日且不复权。这只是接口文档，不是本机网络或数据可用性证据。TASK-43先实测一条源并持续本地存档，不能依赖长期历史可随时补拉。

- DeepSeek 当前账户可用模型及结构化输出：TASK-41 通过官方模型清单和最小探针确认，不因文件有 Key 标 verified。
- ETF 免费分钟/盘口接口覆盖、单位、历史深度和延迟：TASK-43 实测；不能先保证 Tushare、AKShare、BaoStock 均支持。
- 券商费率：此轮无真实券商，TASK-40 冻结明确的模拟假设及敏感性场景。
- 交易日及具体观察日期：由实际交易日历与 TASK-48 就绪结果确定，不预填明天开始。
