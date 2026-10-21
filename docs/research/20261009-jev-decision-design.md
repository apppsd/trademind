# JEV 决策用途研究与采用边界

日期：2026-10-09。来自本会话已阅读的官方文档和公开实践；没有调用项目 API。结论用于 ARCH-006 设计，不是供应商认证或盈利证明。

- [TypeSafe 官方介绍](https://docs.typesafe.ai/introduction)：state + 类型化窄题目，Choice/Score/Noul；独立题目对相同 state 求值，组合逻辑由应用管理。适合动作选择与证据判断，不能把模型摘要当全部市场事实。
- [官方 confidence](https://docs.typesafe.ai/confidence)：confidence 与答案分布相关，不是股票获利概率。新题阈值要独立定义与校准。
- [官方 API](https://docs.typesafe.ai/api)：实施者需核对当前 instructions/criteria、模型与响应契约，做真实最小探针；本次不认定旧适配可用。
- [官方 function calling 示例](https://docs.typesafe.ai/cookbooks/function_calling)：可把请求映射成结构化调用，不能由此推导预测或盈利能力。
- [jev-trader 原始实现](https://github.com/jarrodwatts/jev-trader/blob/main/src/model.ts)：加密市场机器人把价格、价差、盘口、成交/收益特征等交给 JEV 选择方向。它的市场、期限和动作约束与本项目不同，不直接复制；源码的区块时间不是 JEV 请求耗时，不采用缺概率时伪造 one-hot 的做法。
- [ClawStreet 自述实验](https://www.clawstreet.io/blog/jev-ai-stock-trading)：小样本股票筛选显示偏向买入及重复请求差异，不能据此认定 JEV 是稳定投资顾问或长期有收益。

采用的是软件分工假设：flash 生成可检查计划，JEV 对事实回答原子问题，程序保留硬否决。是否更快、更便宜、更稳由 TASK-08/09 对照验证，不先设结论。官方协议、费用、免费行情质量仍需独立验收。
