# 新 JEV 快速决策链实施与验证

日期：2026-10-09。关联 TASK-20261009-02～09、ARCH-006。实际 Python 3.12.10，pydantic2、pytest；未升级依赖、未重构目录、未提交推送。

## 已实施工程

- 官方 JEV 适配：state + instructions/criteria，Choice/Score/Noul 类型、概率、题目及 usage 校验；保留旧 judge 契约映射。分布 confidence 与 Noul yes 概率有独立语义，缺题不伪造。
- flash 低频策略：严格可计算条件、版本化缓存和配置指纹；按日/期限/规则/Prompt/证据时点失效，未知字段或模型内容不作为代码执行。
- 原始事实输入：10条可见分钟记录与来源/时点/单位，程序特征、现金/冻结/可卖数量/挂单/版本、费用假设及风险；无 L1 明确 null。
- 分频调度：行情60秒、全池模型槽最短300秒、flash刷新900秒/TTL1200秒、不确定刷新最短300秒；持仓优先、其他候选轮换。JEV60/flash30/探针10为角色上限，仍受总100次及5 CNY预约限制；格式修复也计数。
- 独立新 profile jev_fast_decision，旧 profile/V1 保留。计划/JEV有效且真实探针通过才新增风险；账户版本变化/过期/未知费用 WAIT。原 Guard/OMS 确定数量与合法成交，shadow 永不提交。首次模拟 BUY 成交保存持有截止时间，重启可恢复；程序止损/时间/尾盘退出不等待模型。
- 新 CLI：t0-fast-check 不读取凭据；t0-paper --probe-models 在同进程做有界真实探针（前置核验费率），能力标志不跨进程凭配置冒认。
- t0-fast-compare：纯离线同可见事实与独立500账户，重放归档意图及规则基线；缺响应/账户不匹配/未来响应不联网补齐，成本未知保留 null。

## 验证

执行 `.venv/Scripts/python -m pytest -q`：266 项通过（旧227 + 新39）。现有 FastAPI/httpx 弃用警告，非本次失败；未升级依赖。

新测试包含 BUY→下一可见分钟成交→SELL→清仓、策略复用/重启/日切、缺题/非法概率/未知费用、账户变化/请求超时点、低可信刷新、并发额度、shadow零冻结、程序止损及持有时间退出。真实供应商请求在测试中被替换，仅验证工程，不能认作官方认证。

实际 CLI fixture 对照命令：

```bat
.venv\Scripts\python -m trademind.runner t0-fast-compare --bundle data/runtime/fast-comparison-fixture-bundle.json --output data/runtime/fast-comparison-fixture-20261009.json
```

得到 archived_intents_replayed、current_model_calls=0，三个独立账户均无模型在线请求。数据明确 artifact_kind=fixture，仅证明工具运行；不是实际股票表现/费用/收益对照。校准仍 unavailable，尚无定义标签及按时间切分的真实留出样本，TASK-08 不标 completed。

## 真实启动检查

北京时间 2026-10-09 10:49:58，使用新独立本地配置，不加载凭据、模型 provider 或账户数据库。交易时段判断为 true（本地日历，非官方日历验收）。

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-fast.local.toml t0-fast-check
.venv\Scripts\python scripts/check_fast_startup.py --timeout 40 --output data/runtime/fast-startup-20261009.json
```

预检 blocked/退出2；新配置四项价格尚未填写。免费数据检查三只ETF均 AKShare ProxyError，未取得新鲜分钟。额外一只ETF仅当前进程绕过代理的8秒请求得到 ConnectionError；没有修改系统代理、全局环境或生产路由。不能据此断定代理是唯一根因。

收费模型请求0、订单0、常驻服务0；没有读取.env。忽略的脱敏证据：data/runtime/fast-startup-20261009.json、fast-direct-data-20261009.json、fast-comparison-fixture-20261009.json。

## 官方费率与未验证范围

[官方模型说明](https://docs.typesafe.ai/models) 当前 jev-1.13.0 输入 USD 0.042/百万token、输出免费；新示例固定版本，支持输出费率0。实际 CNY 价格需核验兑换口径，不能把美元直接当人民币。本次未填写假设费率进行收费试跑。协议参照 [官方 API](https://docs.typesafe.ai/api) 与 [confidence](https://docs.typesafe.ai/confidence)。

TASK-03 工程协议已通过离线测试，真实认证/实测费用未完成；TASK-08 工具完成，真实档案/标签/校准未完成；TASK-09 已完成有界只读预检，真实盘中双模型闭环未完成。以上均保持 in_progress。02、04～07 的离线交付 completed。ARCH-006 implementing，真实工程验收未完整完成，不标 implemented。

下一步：核验本地四项 CNY 费率和官方 JEV 凭据/协议探针，恢复真实分钟连接质量；随后有界新链 paper及共同可见数据对照、冻结校准与真实窗口，再 TASK-56 五交易日观察。

最终文档检查：25份入口/新任务/事实文档的251个本地链接全部存在，索引与Task状态一致；git diff --check通过。最后补充费用估算不当实际金额的负例后，全回归266通过。对照测试共7项，输出净结果在只有费用预留估计时仍为unknown。

## 用户要求复测（2026-10-09 11:07，北京时间）

命令 `.venv/Scripts/python -m pytest -o addopts='' -q`：266 passed、1 warning，5.78秒；warning为既有Starlette/httpx弃用提示。

启动预检命令 `scripts/check_fast_startup.py --timeout 40 --output data/runtime/fast-startup-retest-20261009.json`：未启动。513050.SH与518880.SH各返回241条历史分钟，最新均为2026-10-08 15:00，距本次检查约20小时，状态unavailable，不能当今日有效行情；513100.SH仍ProxyError。四项核验价格未填，官方模型未探针。模型请求0/订单0/未加载凭据/无后台服务。

此次不改应用代码。Task-09保持in_progress，真实模型/费用/行情质量门禁仍未通过；历史分钟可连接不代表网络与今日盘中质量已解决。
