# 2026-10-08 T0已登记全池配置与验证

用户要求配置所有T0数据。按当前对话明确列出的本地已登记3只实施，异步询问是否要扩展全市场，本轮未收到回复；没有把3只称为全市场全部T0。

## 当前池与运行

| 代码 | 本地档案名称 | 配置状态 |
|---|---|---|
| 513050.SH | 易方达中概互联50ETF | 分钟行情/默认轮询已配置 |
| 513100.SH | 国泰纳斯达克100ETF | 分钟行情/默认轮询已配置 |
| 518880.SH | 华安黄金ETF | 分钟行情/默认轮询已配置 |

按data/etf_whitelist.json已有T0记录选取，510050.SH的T1档案排除。未新增或编造证券规则；没有重新核验全市场目录。Tushare/BaoStock不作为这些ETF真实1分钟的未经验证替代。

安全示例config/t0-shadow.example.toml已包含全部3只，已创建不含Key的config/t0-paper.local.toml（Git忽略）；未覆盖原用户配置。总资金500、总敞口300、单只200、保留100、最多1个持仓保持。3只监控不意味着3只都能买入，数量还受价格/最小手数和Guard约束。

DeepSeek价格字段按本轮已核验[官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)填入高峰/未缓存输入2元、输出8元每百万tokens。100请求为整个账户/全部标的共享，非每只100次；JEV段按用户此前排除范围保持，不声称官方适配已完成。

## 新行为

- t0-paper省略--instrument时轮询整个配置池；明确指定则只处理一只。一只报错仍完成本轮其他标的，各自记录结果；--steps是全池轮数，间隔60秒，候选每只冷却300秒，模型额度共享。
- t0-data是独立免费行情检查入口：load_credentials=False，不读取根.env，不实例化Pipeline/模型，不写账户或调用模型。每只显示ok/stale/unavailable/error，输出model_calls=0。配置中的其他凭据字段也被清空，不打印密钥。
- 实时分钟Router保留30秒只读观测缓存（fixture不缓存）；paper先抓取数据，再取as_of并复用缓存，available_at/source_time保持真实值，不回填到网络请求前。缓存超时或窗口不兼容重新请求，未来数据仍过滤。
- AKShare ETF只走fund_etf_hist_min_em，不回退stock接口；抓取异常保留脱敏错误类型，不再将ProxyError静默变成空数据。

## 命令

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-data
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --steps 1
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --instrument 518880.SH --steps 1
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml serve
```

serve同配置/账户/库/8322端口；只启动页面不等于启动后台监控。持续轮询须显式增加steps，Ctrl+C停止。默认无--config的普通入口仍不是T0。

## 验证结果

203项离线测试通过，1条既有Starlette/httpx弃用警告。6项新增测试覆盖全池/T1排除、错误隔离、单只兼容、数据入口不加载dotenv或模型、实时观测缓存边界和ETF异常诊断。离线回归继续封锁HTTP及真实凭据路径。

免费实测本机AKShare1.19.1接口存在，但3只均ProxyError。一次不限定日期的SDK调用仍ProxyError；单次无环境代理诊断也ConnectionError。本次未改系统代理，未记录代理URL/凭据，不能确定最终网络根因。模型调用0；真实分钟数据没有获得。故配置完成，行情可用性未通过，见[ISSUE-20261008-04](../issues/ISSUE-20261008-04.md)，原Issue-13仍open。

## 下一步与限制

在可访问数据服务的网络下，交易时段重新执行t0-data；3只输出ok且时间/单位/延迟质量验收后再进入paper。JEV仍有原协议/费用问题，完整双模型新仓不能据此声称可运行。未实盘、未commit/push、未触碰个人知识库。

若用户选择全市场T0，应新增有交易所依据的目录同步/规则验收Task，再扩容监控与模型筛选；当前未自动将所有ETF改T0或扩大资金。
