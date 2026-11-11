# TASK-20261103-001 第四轮独立复审

2026-11-03，Codex，只读审阅本地既有 dirty 工作树。关联 [Task](../tasks/TASK-20261103-001-收盘后核验冷却修复与分钟行情可观测性.md)；[第三轮 FAIL](20261103-task001-rereview-round3.md)及前两轮记录保持不变。

**VERDICT: PASS（本地工程复审范围）**。第三轮 R2 异常正文透传反例已关闭；R1、R3、R4 未发现回退。此结论不代表运行加载或真实交易验收。

| 项目 | 结论 | 代码与执行证据 |
|---|---|---|
| R2 / P1 | FIXED | `errors.py:NOT_FRESH_PUBLIC_REASONS/classify_not_fresh_reason` 输出限于封闭常量集，非字符串及未知正文归 `unknown`；三种旧动态前缀仅映射常量，不保留尾部。`pipeline.py:reference_price` 的参考报价、日历包装异常等 raise 已使用固定字符串；`run_decision` catch 在写 `diagnostics.data_reject.reason` 和 `decision/data_reject` 审计前分类，估值失败诊断也分类。`runner.py:_public_data_reject` 重建公共拒绝块；`PaperRun.record_progress`、`read_intraday_health` 和 `daily.generate_report` 对对应子因再次分类。新增三项白名单/合成正文/旧动态消息测试均通过；独立探针结果见下文。 |
| R1 / P0 | FIXED | `daily.py:generate_report` 第373–395行先解析 payload、校验对象及账户，再计数；外账户排除，缺归属、坏 JSON 及非对象进入 `unknown_account:*`。`test_pre_decision_rounds_are_account_scoped` 通过：本账户 data_reject=1，外账户 data_reject/model_pending 不混入；无归属与坏 payload 单列。 |
| R3 / P1 | FIXED | `daily.py:generate_for_config` 将 `cfg.t0` 传给 `generate_report`，后者第426–427行传入 `read_intraday_health`。`FastDecisionPipeline.run` 保留窗口 interval/claim/role/trace/next_eligible/remaining 与本轮认领事实；日报关联逐决策窗口及条件证据。`test_generate_for_config_carries_window_and_claim_facts` 通过：interval=300、next_eligible=claim+300、截止时剩余=0，均非 None；claim_this_tick、strategy role、trace 和报告呈现均有断言。条件真值及 public/health/daily 相关测试通过；无配置的直接入口仍保持 unknown。 |
| R4 / P2 | FIXED | `FastDecisionPipeline.__init__/run` 使用 `min(180, quote_max_age_seconds)`，`Pipeline.run_decision` 传入风险配置；意图时效另用 `intent_max_age_seconds=180`。`test_age_threshold_follows_configured_quote_max_age` 通过：60秒配置下正常 BUY 公共阈值为60，100秒旧行情拒绝时仍为60。 |

R2 分类仍可区分：缺 bars（`missing visible minute bars`）、连续段不足（`WAIT_DATA_QUALITY` / `incomplete minute features`，附缺失特征及连续根数）、超龄（`stale visible minute context`）、参考报价失败，以及异步过期/账户变化与条件变化。异步过期和账户变化沿用同一既有子因 `async model result expired or account changed`，条件变化独立。相关真实离线路径及拒单测试均通过，未更改门禁语义。

独立探针通过 stdin 执行，使用离线 `make_pipe` fixture，在原第三轮的 `_fast_model_result` 抛出点分别注入：

1. `NotFresh("SYNTHETIC_UNTRUSTED_EXCEPTION_BODY")` → `unknown`。
2. 日历覆盖旧前缀 + 同一合成尾部 → `trading calendar coverage insufficient`。
3. 分钟参考报价旧前缀 + 同一合成尾部 → `no fresh visible minute quote`。

三个样本均检查 public、progress 文件、health、data_reject 审计、日报对象及实际写出的 JSON/Markdown/HTML；搜索合成标记均为 **false**，health 与日报桶中的子因与分类结果一致。探针退出0，临时库关闭、writer lease 释放，临时目录清理完成。现有新增测试另覆盖从行情获取处抛异常、旧 progress reason 读取时重分类及动态日历包装正文。内部 `DecisionResult.error` 仍保留异常文本；本轮核验的公共入口 `_public_decision` 将其截为异常类，未将内部错误正文当作公共诊断发布。

**Non-regression: OK（本地已审范围）**。

- P2 pure/cached WAIT 不新认领窗口；成功 provider 准入后最终 WAIT 仍占窗，拒绝 tick 不续期。纯 WAIT、准入拒绝、并发复检、预算拒绝、失败留窗、重启持久化及 T+1 兼容测试通过，未发现认领语义回退。
- `config.py:T0Config.fast_eval_seconds` 默认仍300。`config.py`、portfolio、Guard、OMS、`models/daily_budget.py` 的 tracked diff 为空；相关修改中未见新增 FeeModel、资金/风险或预算数值调整。既有 Task30-03 的限定账户候选过滤保持原范围。
- `test_new_chain_buy_fill_sell_and_plan_reuse` 及股票决策链、程序风险退出等相关回归通过，BUY→fill→SELL→清仓路径未发现损坏。

验证记录：

- 首先执行 `git status --short`、`git diff --stat`；HEAD 为 `ffde7534bed3624c2028437645dc9220e2eda96a`，已有大量修改及未跟踪文件，tracked diff 为72文件。Git 有全局 ignore 无权限与部分 CRLF 提醒，不影响本轮测试；没有 `docs/handoff/`。核对了项目入口、Task、前三轮审查及相关代码。Python 要求>=3.12，setuptools/src布局，pytest；依赖声明在 `pyproject.toml` / `requirements.txt`，未调整环境或依赖。
- 使用现存 `.pytest-tmp2/task30/python/cpython-3.12.13-windows-x86_64-none/python.exe`，`PYTHONPATH=src;.venv/Lib/site-packages`，`PYTHONDONTWRITEBYTECODE=1`，pytest 禁用 cacheprovider，basetemp 为 `.pytest-tmp2/rereview4-task001`。
- 实跑 **277 passed，1 warning，13.80秒，退出0**。套件：`test_minute_observability`、`test_fast_decision`、`test_daily_report`、`test_fast_architecture_repairs`、`test_strategy_failure_diagnostics`、`test_stock_decision_chain`、`test_tradability`、`test_t0_runtime_repairs`、`test_paper_run_report`、`test_auto_paper`、`test_context`、`test_etf_minute_data`、`test_daily_revision_visibility`、`test_t0_minimal_runner`、`test_fast_startup`、`test_t0_contracts`。提醒为 Starlette/httpx 弃用；不作全量测试声明。
- 相关六个源文件 `git diff --check` 通过。独立探针三样本全部通过，不仅依赖实现方新增断言。
- 唯一新增交付文件为本报告；未修改实现、Task、索引或前三轮记录，未 commit/push，未连接150、部署、读取凭据或调用收费 API。测试及探针仅生成隔离临时产物。真实行情、费用、进程加载及真实成交闭环未验证。

本轮无必须返工残余。
