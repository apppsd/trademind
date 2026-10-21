# 2026-10-08 非JEV模拟盘修复与复验

关联：[TASK-20261008-02](../tasks/TASK-20261008-02-修复非JEV模拟盘问题.md)、[ARCH-003](../architecture/03-t0-runtime-safety.md)。用户授权修复代码，但排除JEV适配问题。

## 已修复行为

| 范围 | 实现 | 验证 |
|---|---|---|
| 分钟主流程 | T0调用build_minute；持仓估值、Guard参考价均取可见分钟；source_time/available_at/bar_time必须可见；过期/异常OHLC阻止模型；撮合minute_only不回退日线 | 缺失/陈旧/未来/异常质量、决策与撮合不调用日线 |
| 已有持仓风险退出 | 模型调用前检查0.8%成本止损和14:55清仓；生成确定性SELL，仍经原Guard/OMS/费用/授权检查 | 模型完全禁用时止损/尾盘触发，下一可见分钟成交；重复轮询零重复成交 |
| 交易计数 | 买入审批计数事务内持久化；兼容从订单回补；亏损连续次数由账户fill事件的完整平仓交易重建，部分卖出尚未清仓不计完整交易 | 审批后重启保留次数，次交易日重置；实际亏损平仓后重启保留连续亏损 |
| 模型额度 | Pipeline统一每日请求计数，SQLite事务防跨连接超限；DeepSeek所有角色/格式重试各消费一次；UTF-8输入上界+输出max_tokens，按配置价格预留费用 | 次数耗尽阻止第二次修复请求、双连接40次争抢最多10次放行；费用恰好5元允许，重启后下一请求拒绝 |
| 模型费用安全 | model_input_cny_per_million/model_output_cny_per_million未填时阻止收费DeepSeek请求；超时/失败保留预留；cost_source明确为operator_price_request_upper_bound | 无价格/超费用时HTTP零调用；成功有CNY上界审计，超时后预留不释放 |
| CLI配置与时间 | --config在子命令前统一供run/status/serve/recover；t0-paper不再局部改账户/universe；要求T0 paper及合格白名单；steps等待真实间隔，轮询当前时点；错误exit1，未验证exit2 | 配置复用、错误注入非success、步骤60秒推进、未对未来轮询；未验证stub明确标注 |
| 审计与对照 | T0意图持久化、候选/审核可用摘要/最终汇总meta进关键审计；Prompt有带时点OHLC时使用两个独立SQLite/500账户，复用Pipeline/Guard/OMS/账本；报告带成交/交易费/PnL/回撤/拒绝/待成交与档案 | 两版本一方成交一方WAIT，资金互不影响、原账户不变；历史账户归档可JSON导出 |

辅助修正：分钟路由保留配置供应商优先顺序；示例/默认白名单改为已存在规则档案的513050.SH；示例行情只用akshare，不默默转fixture；TRADEMIND_STORAGE_URL覆盖实际生效。未改JEV配置段、官方Provider和双模型中的JEV题目/协议/解析。

T0在flash最终意图之后直接构造PortfolioIntent，保留模型原目标权重，由代码计算数量/Guard做硬否决，避免继续调用旧V1风险/组合两个AI角色。原股票V1路径保持；T0新买入要求白名单和T+0，旧T+1账户测试明确关闭T0开关。

## 费用口径

5元门槛当前落实的是DeepSeek按操作者已核验CNY token价格计算的保守请求预留上界，并非供应商账单；缓存折扣不参与计算。输出上限默认1024，输入按实际消息UTF-8字节数加1024框架余量估算上界。max_tokens参数已核对[DeepSeek官方Chat Completions文档](https://api-docs.deepseek.com/api/create-chat-completion/)，仅核对协议参数，不代表真实调用成功。失败也保留预留，避免未知账单时重新释放额度。改价格需核对当日官方价格和计费币种，本次没有填写或声称真实价格。

JEV参与共享请求次数，但它的usage/费用未解析，属于用户排除的JEV工作。因此不能声称“全部双模型费用已证明小于5元”；ISSUE-20261008-02保留open，仅剩JEV费用/完整总费用验收。没有以0元替代未知JEV成本。

Prompt报告的net_pnl为交易费用后、模型成本前结果；model_cost_cny未知时为null，DeepSeek有可匹配审计时单独给deepseek_cost_upper_bound_cny。缺乏带available_at/source_time的历史分钟OHLC时，报告标intent_only_missing_recorded_minutes，不编造成交/PnL。档案返回在paper_accounts.archive中，包含快照、意图、订单、账户事件、审计和可用模型调用；临时数据库随后关闭清理，调用方应保存report.to_dict()作为版本实验档案。样本末仍持仓/有挂单如实报告，未强行制造未来清仓。

## 验证结果

197项测试通过，1条既有Starlette/httpx弃用警告。新增18项运行安全回归，旧CLI假阳性测试改为独立无凭据配置并明确unverified；旧无分钟数据测试改为断言安全阻塞，旧手工内存亏损测试改为持久成交历史。未降低业务断言来隐藏缺陷。

执行方式：本地.venv中，先将config.ENV_FILE_PATH/DEFAULT_CONFIG_PATH指向不存在的临时文件，移除该测试进程的TRADEMIND_*与DEEPSEEK_API_KEY，封锁requests.Session.request，再pytest.main(['-q','-p','no:cacheprovider','-o','addopts=','--tb=short'])。全部数据库/模拟账户来自测试临时目录。无真实凭据读取、收费API调用、持续服务、commit/push或实盘。

## 状态与限制

- ISSUE-20261008-01/03本地实现缺陷resolved。
- ISSUE-20261007-13的分钟接线缺陷已修复；原关闭条件还含盘中真实质量报告，保持open，剩余工作为实际免费数据能力/时点/延迟验证。
- ISSUE-20261008-02非JEV额度/费用部分已修复；JEV费用排除，保持open。
- ISSUE-20261007-12/14保留open；完整双模型真实启动与BUY→SELL闭环未验收。JEV问题不能由stub/单模型替代。
- 本次离线mock验证不证明当前Key、供应商真实能力/价格、分钟行情权限/可用性、前瞻稳定性或盈利。未触碰个人知识库。

## 统一命令

复制安全示例为独立本地TOML（不要覆盖已有配置）；核对账户/库/白名单、DeepSeek当前价格、供应商配置后，所有命令使用同一路径：

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml status
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --instrument 513050.SH --steps 1
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml serve
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml recover
```

steps每次按quote_eval_interval_seconds等待当前行情，不是加一分钟虚构未来。Ctrl+C停止循环/页面服务；恢复同账户用recover。原默认serve仅服务默认配置，不能据此认定是T0账户。JEV适配未修好时真实新仓依旧阻塞。
