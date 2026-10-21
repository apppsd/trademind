# 2026-10-09 模型费率与容器复验

## 官方价格与预算口径

单位均为每百万token。查询日期2026-10-09。

| 模型 | 输入 | 输出 | 本地配置 |
|---|---|---|---|
| deepseek-flash 高峰缓存未命中 | 2 CNY | 8 CNY | 2 / 8 |
| deepseek-flash 空闲缓存未命中 | 1 CNY | 4 CNY | 不自动切换 |
| deepseek-flash 缓存命中 | 高峰0.04 / 空闲0.02 CNY | 同上 | 当前预算仍按未命中估算 |
| jev-1.13.0 | 0.042 USD | 免费 | 0.282786 CNY / 0 |

[DeepSeek官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：北京时间工作日9:00–12:00、14:00–18:00（不含中国法定节假日）高峰，其余空闲。模型名保持deepseek-flash；官网当前对应V4.1-Flash，不替换为其他模型名。

[TypeSafe官方模型价格](https://docs.typesafe.ai/models)：0.042 USD/Mtok，仅输入收费。固定jev-1.13.0。

[外汇局人民币汇率中间价](https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do)：2026-10-09美元673.3/100美元，1USD=6.7330CNY；[中国外汇交易中心授权新华社公布](https://www3.xinhuanet.com/20261009/e1fc54279c3f4d2a8a1a7883d22fc9fb/c.html)确认单位。换算0.042×6.7330=0.282786CNY/Mtok。

JEV汇率是估算参考，不含支付渠道差价、税费；Flash按高峰未命中上限计算，不读取缓存拆分或自动应用空闲优惠。持久预算cost_settled_cny表示已获usage的配置费率估值，不等于供应商账单。未知usage保留pending预留。每日上限仍100请求/5CNY，实际总上限不是每天固定消费5元。

## 配置
公共container示例与忽略的container/fast local仅配置四价格；凭据从现有.env加载，不写入配置。其他账户与费率不覆盖。费率已配置，容器加载通过。

## 有界运行证据（2026-10-09 21:58～22:01北京时间）

- container内t0-fast-check：configured_prices=true、session_open=false、credentials_loaded=false，未发请求。
- 停workbench隔离写入后，`docker compose --profile paper run --rm paper --config config/t0-container.local.toml t0-paper --steps 1 --probe-models`：Flash成功、JEV失败，exit1，执行循环未启动。
- Flash真实1请求：deepseek-flash，537ms，usage输入62/输出19，配置上限估值0.000276 CNY。当前夜间和缓存折扣未纳入估值，不宣称实际扣款金额。
- JEV共3次请求（初探针及两次定位），814/1769/636ms；末次明确HTTP451，响应只有status/title，无model/answers/usage。官方base_url及固定模型检查正确。不记录响应原文或推定地区、账号原因；无自动重试，451确定后停止。宿主21:27 HTTP200是另一环境的历史成功，不能继承其能力到容器。
- 持久预算used4/probe4：usage已知估值0.000276、未知预留0.001023119748、合计0.001299119748 CNY。未知失败预留不释放，不等同实际扣费。
- 免费check：四价格均存在，3只ETF仍AKShare ConnectionError；session_open=false。check自身model_calls0不是账户累计调用0。
- DB订单0/成交0；500元paper账户，未进入交易循环、未开始五日观察。
- 重新构建公共示例费率镜像成功，未改依赖/业务源；重建workbench，根HTTP200，仅本机8321，未留下paper worker。

## 剩余工作

[JEV容器451](../issues/ISSUE-20261009-03.md)先定位合法支持的网络/服务访问条件；供应商未给出原因前不认为Key无效或代码协议有误。免费分钟连接按Issue-20261008-04修复，再在真实盘中完成Task03/08/09及五日观察。完整题型/真实费用账单仍未验收。此前280离线测试基线保留；本次仅配置/文档与运行验证，没有业务代码修改，不重复全套测试。
