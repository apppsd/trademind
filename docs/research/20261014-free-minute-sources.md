# 2026-10-14 免费ETF分钟多源核查

关联：[TASK-20261014-02](../tasks/TASK-20261014-02-免费多数据源代码核查与任务规划.md)。只读源码、公开官网和离线合成输入；没有注册、领证、调用行情或模型接口。下列政策核查日期为2026-10-14，网页描述不等于实测稳定性。

## 选型结论

先复用现有新浪ETF适配器，以AKShare东财ETF接口作为待验备用；少量并行观察用于交叉核验，正常决策每次选择一个合格来源。两个供应平台不同，但尚不能证明其底层数据完全独立。免费源无已验证的持续可用承诺。

| 候选 | 官方/代码证据 | 本项目定位 |
|---|---|---|
| 新浪公开ETF K线 | 现有sina_etf仅允许已核验ETF、1m，8秒超时，检查OHLC/量额单位；历史连接证据见下文 | 保留主源；实时完整性仍待Task09-16 |
| AKShare东财ETF | 官方fund_etf_hist_min_em支持1m，仅近5个交易日且不复权，输出OHLC、成交量、成交额 | 修复单位映射及闭合处理后作为备用；此前连接失败不能视为已恢复 |
| 必盈 | 免费试用政策存在页面矛盾，未确认三ETF免费1m权限和字段；文档访问超时 | 候选待验，当前不纳入自动执行链 |
| baostock | 当前项目适配器只有日线/日历，无分钟方法；历史API文档列5/15/30/60m | 盘后研究/日历候选，不能顶替本项目1m执行数据 |
| Tushare | 当前适配器调用股票daily，无ETF fund_daily或分钟实现；官方ETF分钟需独立权限 | 本方案不依赖付费分钟源；已有其他项目订阅可按实际权限用于盘后研究，不能据此认定1m可用 |

参考：[AKShare官方ETF文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)、[BaoStock Python API源码文档](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)、[Tushare权限说明](https://tushare.pro/document/1?doc_id=290)。新浪证据引用[既有真实连接报告](20261009-runtime-connectivity.md)的ETF章节；报告旧JEV认证段为历史，本轮不沿用。

## 所贴文章需纠正的地方

- [必盈首页](https://www.biyingapi.com/)称免费版每日200次、基础接口、无限有效期；[免费试用页](https://www.biyingapi.com/get-free-cert.html)却写7天有效、邮箱验证、每邮箱限领一次。永久免费及无注册门槛不能据文章确认。
- 文章同时给出每日200次、每分钟20次和无限制，不能当成同一套餐的已验证限制。禁止把换密钥规避额度作为实现方案。
- 官网基金行情文档的搜索索引列有5/15/30/60分钟等周期；本轮正文打开多次超时，尚未确认ETF1m覆盖及免费权限，不能断言支持或不支持所有1m接口。[官方基金行情入口](https://www.biyingapi.com/doc_jjhq)。
- 官网股票API搜索索引示例采用hslt/list、hsstock等路径，与文章stock/history、stock/realtime/a不同；文章代码未运行，也未证实其接口有效。[官方股票API入口](https://www.biyingapi.com/doc_hs)。
- 零延迟、零报错、企业级稳定及全海外覆盖没有本轮实测依据；供应商网站营销排名不能用于本项目验收。
- 按每交易日约240分钟、每分钟刷新一次估算：即使存在一次覆盖全池的接口也需约240次/日；三只ETF各一次则约720次/日，尚不含预热、核验、重试。200次/日不够承担这个刷新目标；具体取决于批量能力和采样频率。低频条件备用仍需确认权限和期限。

## 实际代码与隔离复现

环境：main工作区、现有.venv Python，项目要求Python>=3.12；不升级依赖或更换工具。反例从PowerShell7 here-string送入`.venv\Scripts\python.exe -B -`；没有保存业务脚本、初始化真实供应商或连接生产库。

### 路由未按质量切换

源码：[router.py](../../src/trademind/market/router.py)、[quality.py](../../src/trademind/market/quality.py)、[builder.py](../../src/trademind/context/builder.py)。

构造仅实现get_minute_bars/capabilities的两个Fake对象，注入DatasetRouter._instances，pref=[primary,backup]。as_of=2026-10-14 10:00:30+08:00，查询前一小时。备用源有有效1m行，四种主源如下：

| 主源返回 | 实际选择 | 备用调用次数 | 结果 |
|---|---|---|---|
| 空列表，minute_bars=True | primary | 0 | rows=0，quality.ok=True |
| high=0.5、open/close/low=1 | primary | 0 | rows=1，quality.ok=False |
| 最新bar距as_of约20分钟 | primary | 0 | rows=1，quality.ok=True |
| available_at在as_of之后30秒 | primary | 0 | rows=1，未来可见行=1 |

get_minute_bars只对请求异常/未支持空数据继续尝试；首次归一化未传as_of、不按report.ok筛选，没有业务freshness接受条件。builder后续拒绝无效OHLC、过滤可见行并记录stale，但已不会回到router尝试备用。**这证明备用没有被使用，不证明未来数据已用于订单。** 当前安全门禁必须保留。缓存命中按as_of重新过滤，但不等同于质量失败后的备用选择。

### 东财ETF单位映射

源码：[live.py](../../src/trademind/market/adapters/live.py)的AKShareAdapter.get_minute_bars直接将成交量填入MinuteBar.volume（统一约定为份额）。

用官方文档511220示例的2024-03-20 09:31行构造DataFrame（成交量413、成交额420929元、收盘10.192元，合成OHLC范围10.19～10.193），通过__new__和假SDK调用实际适配器，无网络。返回volume=413，amount/volume=1019.198547，单位检查False；若该列为手，换成41300份后约10.191985元，与价格相符。证明当前映射不能把文档该示例转换成统一份额；其他ETF和周期仍需逐接口验证，不能所有供应商一律乘100。

另：适配器按代码前缀选择ETF接口，1m仍传adjust=qfq（官方1m实际不复权），当前没有新浪适配器同等的闭合/单位检查。能力声明也不代表ETF代码、周期和字段均合格。

## 方案与验收边界

规划见[ARCH-020](../architecture/20-free-minute-source-routing.md)，实施入口[Task14-03](../tasks/TASK-20261014-03-核验免费ETF备用源与单位映射.md)、[Task14-04](../tasks/TASK-20261014-04-实现分钟行情质量驱动多源回退.md)。盘中真实性复用[Task09-16](../tasks/TASK-20261009-16-验收盘中ETF分钟数据质量.md)，不重复关闭其Issue或替代模型闭环验收。

保留单行、窗口及source/fetched/available时间来源，决策窗口优先整段单源切换；不平均供应商价格、不拿快照伪造分钟OHLC、不默默补缺分钟。复用既有180秒时限；只对已配置的免费合格源做有界回退。分钟数据不等于逐笔/盘口实时数据，秒级检查不能让上游一分钟数据变成秒级行情。

未验证：当前三ETF盘中新鲜度、免费源延迟/完整性、东财连接恢复、供应商底层独立性、必盈实际权限/条款/接口响应、服务器部署及真实运行。新方案未切换运行数据源。
