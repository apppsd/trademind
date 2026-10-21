# 2026-10-11 快速交易提示契约验证

Task-20261011-01；Codex，main / F:/Project/TradeMind。未读/输出密钥、未调用收费API、未提交推送或运行实盘。原工作区改动保留。

## 已确认问题与修改

原策略提示只列输出字段，没有精确Schema、条件组合语义、数值单位、费率/硬限制和程序字段所有权；JEV计划适用题要求无风险/critic冲突，与正常反方分析容易混淆。这是静态契约审查结果，未声称已观察真实LLM因其出错。

新增 `agents/fast_prompts.py`：strategy-2、fast-2及四个独立题目。策略草稿Schema从现有StrategyPlan派生九字段，避免另造与验证器不一致的Schema。输入说明可用字段/单位、entry全部满足、exit和invalidation任一满足、仅有限常数比较、纸面交易/禁做空杠杆、硬风险限制和参考费率。

strategy_planner负责plan身份/时间/证据/版本；落盘前拒绝缺字段/额外元数据、不可见或非法条件、SELL无退出条件、非整数或超出当日剩余时长的持有期限。WAIT的完整九字段计划合法且不强迫下单。旧strategy-1缓存不再沿用，原刷新冷却/调用预算保持，不能立即无限重试。

JEV明确：正常风险/critic不等于计划失效；可选L1缺失不自动否决分钟策略；判断充分性不是保证未来收益；SELL只减已有可卖仓位、亏损不自动阻止合理退出；entry_suitable仅管BUY。每题独立读取原始事实、忽略资料内指令，WAIT/HOLD无订单。置信阈值、Guard、OMS和确定性风险退出未改变。

DecisionOutcome/TradeIntent记录fast-2；provider.evaluate增加仅审计用的可选question_version，prompt_hash包含本次版本和实际题目载荷，不改变HTTP字段或共享provider旧链默认版本。未改旧A/B提示基准。

## 验证

`.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_fast_prompt_contract.py`：11 passed/0.49秒。

覆盖Schema与输入契约、元数据/缺字段/非法条件/不可见条件/期限/SELL空退出拒绝、完整WAIT零订单零JEV、旧版本缓存失效仍受冷却，以及BUY→下一分钟成交→JEV给SELL且入场题no→SELL成交→清仓。检查真实构造的请求/计划/意图/审计哈希；响应来自隔离Mock，不是供应商交易证明。

相关快速链/档案/报告44项通过；完整宿主回归353 passed/9.65秒，1个既有Starlette弃用警告。git diff --check通过。Python3.12/依赖/Schema/账户资金和硬阈值未升级或修改。

## 未验证与生效限制

今天Docker原连接不存在，rdctl shell返回缺少/run/wsl-init.pid；没有可用引擎完成新镜像构建或容器复验。此前10月10日342项容器证据不代表新版镜像验证。当前容器/服务状态不得沿用昨日healthy结论。恢复Rancher后按operations重建镜像再加载新版提示。

真实DeepSeek/JEV对新版措辞的理解、策略表现、盘中完整行情、非空共同档案/校准、真实买卖闭环和五日观察未验收，继续由16/17/18/08/09/56负责。提示词不能保证交易一定发生、盈利或基础设施可用。

## 同步契约

README、current-status、project入口/概览/代码地图/流程/操作、任务索引、本Task、需求提示语义与ARCH-006已同步。setup依赖/环境配置未改变，roadmap阶段门槛未改变，Release无发布不适用；现有运行Issue-20261010-02追加诊断，不编造新提示Issue或根因。
