# TASK-20261103-001 独立只读审阅

日期：2026-11-03；审阅者：Codex。关联 [Task（含 t279u 补丁）](../tasks/TASK-20261103-001-收盘后核验冷却修复与分钟行情可观测性.md)。

**VERDICT: FAIL**。P2 窗口语义通过本地核验；可观测性与 t279u 验收未完整交付。以下为本地已验证结论，不代表150运行状态。

## 范围与事实

- 工作区 `F:/Project/TradeMind`；HEAD `ffde7534bed3624c2028437645dc9220e2eda96a`。`git diff ffde753 62b5be1 -- src` 为空。相关修复在既有 dirty 工作树，不能按 HEAD 推断已加载。
- 核对 features/builder/pipeline/fast_pipeline/runner/paper_run/locale/daily、P2 planner/decider/provider 准入链、预算事务及对应测试。既有其他 dirty 未改；本轮只新增本报告，未改实现、参数或任务所有权，未 commit/push/部署，未连接150或调用行情/收费模型。
- A：cached/pure WAIT 不新建窗口；成功准入 flash 后最终 WAIT 仍留窗；拒绝 tick 不续期；跨连接首次准入复检、预算/角色拒绝、失败退避、重启持久化及股票 T+1 兼容有测试支持。事务先完成预算预约与写窗，再发 HTTP；不以最终 WAIT 撤销已认领窗口。
- B：已存意图与前置轮次分母分列的方向正确，但账户隔离和细分证据有缺陷，见 R1/R2。
- C：连续根数从完整可见集计算；无数据保持 unknown；106秒/14根可与超龄分开。条件真值与有效门槛仍有缺口，见 R3/R4。新增字段中未发现凭据输出；未读取真实凭据。
- D：未发现本轮变更 FeeModel、资金计算、Guard/OMS、风险配置、特征门槛、fast_eval_seconds 或预算数值。features 连续段抽取与原算法等价；BUY/SELL、程序风险退出与股票兼容定向回归通过。既有30-03账户限定候选过滤按原范围保留，不能算作本轮新增资金算法。

## 必须返工

### R1 / P1：日报前置分母跨账户混算

位置：`src/trademind/reports/daily.py:307`（audit读取入口在第92行附近）。循环未按 payload.account_id 过滤；先计数，再解析 payload。

离线反例：同库建立 paper-test 与 another-account，仅给 another-account 写一条当日 data_reject，生成 paper-test 日报，其 `pre_decision_rounds.buckets.data_reject.count=1`，reason 为外账户原因。两个独立分母虽存在，前置分母并非当前账户分母。

返工：解析并校验账户后再计数；缺失归属行应标未知/单列，不冒充账户数据。补双账户、缺失账户及坏 payload 回归。

### R2 / P1：NotFresh 细分证据未传到 paper-run/health，日报也合并缺bar与超龄

位置：`pipeline.py:578`、`runner.py:204`、`execution/paper_run.py:537`、`reports/daily.py:322`。

离线反例：让真实 pipeline 的 `_fast_model_result` 抛 `NotFresh('async strategy conditions changed before execution')`；内部 error 有子因，经 `_public_decision` 后仅 `NotFresh`，diagnostics.reason/data_reject 均为空，health.last_decision_reason 同样仅 `NotFresh`，只能得到 data_reject 总桶。新 catch 写审计却没有补诊断白名单字段；参考报价等子因同样会丢失。

另：缺bars与超龄都在 pipeline 第418行抛相同总原因；catch 的审计只存总原因、不存 missing_fields，日报按 reason 聚合后无法区分二者。现有测试仅断言内部 error/审计总串，未覆盖完整公共输出链。

返工：按安全分类白名单保存子因与可获得的 missing_fields；贯通公共摘要、health和日报。补缺bar、超龄、参考报价失败、异步过期/账户变化及条件变化的端到端断言；保留 unknown，不输出原始异常体或凭据。

### R3 / P1：t279u明确要求的条件真值与冷却证据穿透未完成

位置：Task 的 t279u 第4节；`runner.py:211`、`execution/paper_run.py:499`、`reports/daily.py:340`。

Task 实施记录明确承认逐条件真值遥测未实施，却将相关可追溯验收项勾选。t279u已将它列为验收要求，不能由实现方自行降为后续范围。完整 t0_chain 审计保留 state 不等于 paper-run/health/日报已发布逐条件结果。

当前公共输出缺少 no-actionable 的 plan_id、allowed_actions、field/operator/threshold/actual/matched/missing 与动作排除分类；本轮是否认领窗口等也未完整穿透。日报调用 `read_intraday_health` 不传 t0_cfg，所以其中 effective interval/remaining 即使有claim行也为未知；Markdown/HTML未补完整证据呈现。

返工：按t279u补安全字段及三个消费端的一致呈现，使用现有条件匹配语义，不改阈值；补固定样本验证与JEV调用/跳过断言。修正Task中尚未满足的勾选及交付声明。

### R4 / P2：快速结果覆盖了有效行情门槛

位置：`agents/fast_pipeline.py:205` 与 `pipeline.py:489`。

pipeline 起初记录 `min(180, quote_max_age_seconds)`，随后用 fast diagnostics 整体替换；后者硬编码180。离线将 quote_max_age_seconds 设60，在合格行情的正常BUY意图上，最终 `age_threshold_seconds=180`，与有效门禁60不符。本反例只修改内存测试配置。

返工：保留有效行情门槛；若需要表示意图180秒上限，使用含义明确的另一个字段。补小于180的配置回归，不改变实际门禁。

## 验证与限制

- 默认 `.venv/Scripts/python.exe` 启动失败：它指向已不存在的Python312。改用Task已有隔离解释器 `.pytest-tmp2/task30/python/cpython-3.12.13-windows-x86_64-none/python.exe`，`PYTHONPATH=src;.venv/Lib/site-packages`，禁止写pyc、禁用pytest缓存，使用独立临时目录。
- 实际复跑 **265项全部通过，退出0**：test_minute_observability、test_fast_decision、test_daily_report、test_fast_architecture_repairs、test_strategy_failure_diagnostics、test_stock_decision_chain、test_tradability、test_t0_runtime_repairs、test_paper_run_report、test_auto_paper、test_context、test_etf_minute_data、test_daily_revision_visibility、test_t0_minimal_runner、test_fast_startup、test_t0_contracts。仅见Starlette/httpx弃用提醒。无全量测试声明。
- 另以stdin执行三个独立离线缺陷探针，分别确认R1、R2、R4；最终确认轮3/3命中，退出0，未新增测试源码。首轮探针因fixture writer lease未释放而清理失败，随后确认轮正确释放并退出；首轮临时目录清理被自动审批拦截，保留于忽略目录 `.pytest-tmp2/review-probes-5q_jm819`，不涉及运行库。
- 审阅源文件 `git diff --check` 通过；文档检查报告0错误/36项既有提醒（报告写入前）。265项绿灯未覆盖上述反例，不能替代t279u验收。
- 150代码/dirty/进程加载仍为 **UNKNOWN**；未验证真实行情、费用账、盘中冷却占比及成交闭环。未遇到 QUOTA_STOP。

Claude返工：R1～R4；保留已通过的P2、预算与交易边界。修复后重新独立复验，当前不接受完整工程交付声明。
