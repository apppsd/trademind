# 双模型最小 API 连通测试

日期：2026-10-07，Asia/Shanghai，约22:46。关联TASK-50；用户本轮明确授权测试两个API。固定官方HTTPS主机、禁止重定向、不重试，不发送交易资料；Key仅在内存读取，未输出、复制或存档。

## 实测结果

| 项目 | DeepSeek | TypeSafe JEV |
|---|---|---|
| API | GET /models；POST /chat/completions | POST /v1/systemone |
| 官方主机 | api.deepseek.com | api.typesafe.ai |
| HTTP | 清单200；生成200 | 401 |
| 请求模型 | deepseek-flash | jev-latest |
| 实际响应模型 | deepseek-flash | 未取得 |
| 生成请求耗时 | 0.719秒 | 0.671秒（失败响应，不是推理耗时） |
| 清单请求耗时 | 0.437秒 | 未请求 |
| 结构校验 | JSON对象中ok=true | 未取得答案，未验证 |
| usage | prompt38 / completion5 / total43 tokens | 未取得 |
| 结论 | 本次最小请求通过 | 认证未通过 |

DeepSeek模型清单返回deepseek-flash、deepseek-v4-pro，只测试flash，没有测试pro。使用固定非敏感输入，max_tokens=128、不流式、关闭thinking；共一次生成请求。实际收费未查询，token数不等于已核算金额。

JEV请求使用同一无敏感状态和Noul/Choice/Score三类题目，只有一次POST。401证明收到认证失败响应，不能判断账户余额、模型能力或交易效果。不得将失败耗时作为JEV速度指标。

## JEV 本地格式检查

后续用户澄清：当时的JEV变量实际上填的是DefAPI提供的Key。故此次401仅说明该Key未通过TypeSafe官方认证，不能说明DefAPI Key无效。用户曾表示准备换官方Key，但没有再次实测；随后明确暂缓JEV，当前不继续验证。原探针HTTP和时间证据保留。

进程实际使用本地.env的TRADEMIND_JEV_API_KEY；值存在，无首尾空白，未匹配示例占位值。仅记录布尔结果，不记录长度、前后缀或Key内容。

这排除了当前未填和常见首尾空白情况，不能证明Key正确或适用于TypeSafe官方服务。后续用户在TypeSafe控制台确认/重建有效API Key后替换本地值；若来自第三方，则先确认该服务官方地址和协议，不将Key发到猜测的主机。

## 边界与后续

本测试直接做官方协议探针，没有改变项目Provider、capability_verified持久状态或运行模式，也未启动交易。JEV本地adapter仍未适配官方协议，TASK-41/ISSUE-12保持待实施/open。ISSUE-02部分更新：DeepSeek本次最小请求可用，JEV认证及完整角色链未通过。

后续由TASK-41实现正式adapter、异常/预算/脱敏测试并重测JEV；TASK-48仍需工程验收，TASK-49不能启动。此次单次响应不证明长期稳定、并发能力、真实费用、行情质量或盈利。

协议来源：[TypeSafe API](https://docs.typesafe.ai/api)。DeepSeek最小协议按现有OpenAI兼容Provider核对并取得真实响应。
