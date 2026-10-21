# 2026-10-08 双模型模拟盘独立检查

结论：已有较完整的模块和离线测试，但当前不能认定 JEV + deepseek-flash 最小模拟盘已验收。179 项测试全部通过；真实组合接口、主流程接线和持续风险退出仍有阻塞。本次只检查并维护文档，没有修改应用代码、读取真实凭据、调用收费API或提交推送。

## 验证范围与结果

Windows / 本地 .venv / main 工作目录；应用 src/、tests/ 仍为 Git 未跟踪文件，不能依据提交差异判断实现完成度。先恢复项目入口，检查任务和相关代码，再运行离线测试与隔离复现。

- 全套 pytest：179 项通过，1 条 Starlette/httpx 弃用警告；通过 pytest.main(['-q','-p','no:cacheprovider']) 执行。
- 执行前将 config.ENV_FILE_PATH 和 DEFAULT_CONFIG_PATH 指向不存在的临时文件，移除当前测试进程的 TRADEMIND_* / DEEPSEEK_API_KEY 覆盖，封锁 requests.Session.request；未读取用户 .env。
- 另以 --collect-only -q -o addopts= 确认 179 项收集数。
- 独立复现使用测试的 MockLLM / Context 与实际 TypeSafeJEVProvider，HTTP transport 替换为 Mock；未发网络请求。
- CLI 错误复现使用临时 SQLite、无凭据配置及注入决策错误，未修改用户运行库。
- 已有 ETF 分钟撮合、T+0/T+1 区分、Guard/OMS 和账本测试通过。这证明组件测试通过，不证明真实双模型分钟决策已贯通。

## 阻塞发现

| 优先级 | 问题 | 位置 | Issue / 接手范围 |
|---|---|---|---|
| P1 | 双模型传 id，真实 JEV 要 question_id；官方请求和答案字段也不匹配 | agents/pipeline.py:145；models/jev.py:191、241 | ISSUE-20261007-12；TASK-41/57 |
| P1 | T0 决策仍构建日线 Context，Guard 的 reference_price 仍取日线收盘 | pipeline.py:159、219；context/builder.py:44 | ISSUE-20261007-13；TASK-43/58 |
| P1 | 止损/尾盘退出只是未调用的辅助函数；亏损计数和日切计数没接入生命周期 | guard/guard.py:54、61、284；pipeline.py:307 | ISSUE-20261008-01；TASK-45/58 |
| P1 | T0 每日100次/5元模型额度无消费者，两个供应商各用进程内预算 | config.py:111；models/provider_factory.py:17、58 | ISSUE-20261008-02；TASK-52/57 |
| P1 | t0-paper 错误仍 success / exit0，所有 steps 共用 now；serve 不继承其账户配置 | runner.py:127、143、156；runner.py:86 | ISSUE-20261008-03；TASK-58 |

### 1. 真实 JEV 链路与官方契约

候选 candidate_action=BUY，实际 TypeSafe adapter 配合 Mock transport，得到：
action=WAIT；jev_error=missing question_id in question spec；http_calls=0。测试 MockJEV 使用 id，掩盖了生产适配器需要 question_id 的差异。

单独传 question_id 后，捕获请求问题对象的字段只有 type/prompt/options。官方要求 instructions，Choice/Score 还要求 criteria。Mock 官方 Choice 答案 choice=yes / confidence=0.8 后，实际解析 value=''、batch status=ok；空 answers 也得到 status=ok 和 missing_reason=question_missing_in_jev_response。DualModelPipeline 只检查 batch.status，未阻止缺失答案进入最终汇总。

同时 JEV state 只收到候选 action 和三个分析文本，没有对应价格/特征/时点/账户事实。probe 仅凭 HTTP200 设置 capability_verified=True，未确认必需答案存在。修复应覆盖真实适配器组合测试、官方 Choice/Score/Noul、空/缺题答案、事实载荷及探针语义，再单独验证真实能力。

官方资料于本次核对：[TypeSafe API](https://docs.typesafe.ai/api)。请求、答案语义和缺失策略必须以官方契约为准，不以现有 Mock 格式作标准。

### 2. 分钟行情未接入主决策

ContextBuilder.build_minute 已有实现，但 Pipeline._run_decision 无条件调用 build；build 内为 get_daily_bars。reference_price 也始终取最后可见日线 close。分钟撮合独立存在，不能补足分钟信号/参考价缺口。默认 data.provider_pref 仍 fixture 优先。

修复验收：在明确 T0 配置下使用同一时点的真实分钟上下文/参考价，测试断言分钟接口调用且日线接口不能替代；缺失或陈旧时报 WAIT/错误而不制造报价。盘中免费数据实际可用性本次未验证。

### 3. 退出与计数未运行

check_stop_loss_exit / check_session_close_exit 在 src 中只有定义，无主流程调用；record_trade_result 和 reset_daily_counters 同样只有定义。已有持仓在 flash HOLD/WAIT 时直接返回，没有代码止损/14:55退出分支。三个连续亏损门槛的计数不会随实际平仓变化，买入日计数无法按交易日重置，也未持久化。

修复验收：已有持仓即使模型 WAIT/审核失败仍经 Guard/OMS 风险退出；用分钟行情触发0.8%止损和14:55清仓；真实成交更新亏损计数，跨日/重启恢复正确。禁止将 JEV 新仓门禁弱化为单模型放行。

### 4. 模型预算无 T0 强制门禁

daily_decision_call_budget=100、daily_model_cost_limit_cny=5.0 只在配置中定义；candidate_cooldown_seconds、quote_eval_interval_seconds 同样未消费。Provider factory 的 LLM budget 使用 llm.daily_call_budget，JEV 使用 batch_limit*100；未共享每日累计，进程重启可重置。JEV judge 审计未写官方 usage，无法由完整请求计费数据落实费用门槛。

修复验收：所有角色、重试、JEV/DeepSeek请求共享交易日累计，重启保持；额度不足时不发下一请求；区分模型费用/交易费用及价格未确认。测试应实际达到100次/5元边界，而不只是断言配置存在。

### 5. CLI 验收是假阳性

向 Pipeline.run_decision 注入 INJECTED_DECISION_FAILURE，运行 steps=2：exit_code=0；status=success；两个 decision.error 均存在。cmd_t0_paper 在 load_config 后局部改 t0.enabled/account_id/mode，没有将该配置传给之后独立启动的 serve；默认 serve 因而仍加载默认账户。for step 共用同一个 now，轮询每次都在 now+1分钟，不代表向前运行多个分钟。

修复验收：错误非0并区分初始化/安全WAIT/执行成功；有明确统一配置入口供 runner/status/serve/recover 使用；同账户同库；steps 时间与行情可见性真实前进，不提前制造 future account events。

## 其他未达到的交付标准

- DualModelPipeline.run 返回 meta 和 JEV batch 摘要，但 Pipeline 仅接收 intent/meta 后不保存 meta、判断批次或 T0 意图；WAIT 提前返回。现有 model_calls 不能替代完整候选→审核→最终意图→Guard 的关联记录。后续运行页面/复盘应可查询完整链。
- compare_prompt_versions 只比较动作、置信度、权重、持有期和文本，没有执行独立账本/费用/拒绝/净收益/回撤对照，也没有隔离版本运行账户。TASK-59 的对应完成勾选不满足原验收标准；先补齐58再恢复受控迭代。
- TASK-41/43/45/52/57/58 的 completed 是原实施方记录，不能作为独立验收通过。相关 Issue 保持 open；不直接覆盖其他 Agent 的历史实施记录。
- ISSUE-20261007-14 已出现新的 paper/分钟撮合组件，不能再简单称“未实施”；500账户完整双模型 BUY→SELL→清仓的端到端证据仍未得到，本次不关闭。

## 接手顺序

先处理 ISSUE-12 的 adapter/组合契约，再处理 ISSUE-13 的分钟决策入口；接着处理退出、预算、CLI一致性和审计持久化。增加覆盖上述实际组合行为的测试，回写相关 Task、Issue、当前事实和验收报告后，再运行预算内真实双模型/盘中行情探针。完成启动验收后才以隔离 baseline 迭代 Prompt。

## 未验证

未调用真实 DeepSeek/JEV；不能判断现有 Key、当前账户额度、响应延迟和可用型号。未取得盘中免费分钟行情，未运行连续前瞻测试，未证明收益或策略有效性。此前API测试记录仍是历史证据，不能替代本轮真实组合验收。

