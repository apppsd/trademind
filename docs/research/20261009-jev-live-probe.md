# JEV 官方有界探针（2026-10-09）

关联 TASK-20261009-03。用户明确授权验证新填入的 `.env` 官方密钥；不展示凭据、不下单。

边界：一次POST https://api.typesafe.ai/v1/systemone，jev-1.13.0，合成事实Noul测试；count budget=1、无重试、连接5秒/读取15秒；不构造Pipeline或交易账户。

费用来源：[官方Models](https://docs.typesafe.ai/models)，本次查证输入USD0.042/百万token、输出免费。按usage计算美元估价，不声称账单或CNY费用核验。生产四项CNY费率门禁保持。

## 结果

2026-10-09 21:27:35北京时间，一次POST成功，HTTP200；实际模型jev-1.13.0，耗时609ms，Noul合成事实检查通过，1个答案完整，usage为input288/output22。现有适配器类型、概率、模型与usage校验均通过，capability_verified=true（仅本次探针进程）。

按官方输入价格估价USD0.000012096，非供应商账单；输出免费。载荷182字节，测试前保守估价上限USD0.000050652。请求1/订单0，无重试、不改网络/生产配置/账户，不输出或保存密钥。脱敏机器结果在忽略目录 `data/runtime/jev-live-probe-20261009.json`。

认证与最小Noul能力已验证；Choice/Score真实题型、新链四动作、CNY费率/汇率与真实双模型模拟闭环仍未验证。未启动服务；下次进程仍须按启动门禁探针。
