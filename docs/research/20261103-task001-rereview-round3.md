# TASK-20261103-001 第三轮独立复审

2026-11-03，Codex；本地既有 dirty 工作树。关联 [Task](../tasks/TASK-20261103-001-收盘后核验冷却修复与分钟行情可观测性.md)。前两轮 FAIL 记录保留不改。

**VERDICT: FAIL**。R1、R3、R4 修复通过；R2 子因贯通有进展，但没有满足安全白名单要求。

| 项目 | 结论 | 文件:symbol 与实测证据 |
|---|---|---|
| R1 / P0 | FIXED | `reports/daily.py:generate_report` 先解析 payload、检查 account_id，再计数；外账户排除，缺归属/非对象/坏 JSON 单列 unknown_account。`test_pre_decision_rounds_are_account_scoped` 通过：双账户、缺账户及坏 payload 均覆盖，本账户 data_reject=1，外账户 model_pending 不混入。 |
| R2 / P1 | NOT FIXED | `pipeline.py:Pipeline.run_decision` catch（约604–609行）仍用 `str(exc)[:160]` 填充 diagnostics 和审计；`runner.py:_public_decision` 只白名单顶层字段，整个 data_reject 原样通过。`PaperRun.record_progress`、`daily.generate_report` 的 sanitize_evidence 是正则脱敏/截断，不是子因白名单。独立探针注入不含凭据的 `NotFresh('SYNTHETIC_UNTRUSTED_EXCEPTION_BODY')`：正文进入 public、health 和日报 JSON（3项 true）；Markdown/HTML 本样本未显示该正文（2项 false，未知英文经翻译回退隐藏）。不能将此视为安全贯通完成。 |
| R3 / P1 | FIXED | `daily.generate_for_config`→`generate_report(t0_cfg=cfg.t0)`→`read_intraday_health(t0_cfg=...)` 接线存在；`FastDecisionPipeline.run` 输出 model_window/claim role/trace；daily 关联逐决策窗口和 action_gate 并呈现窗口节。`test_generate_for_config_carries_window_and_claim_facts` 通过：interval=300、next_eligible=claim+300、历史截止剩余=0（非None）、claim_this_tick=true、strategy role及trace存在。条件真值、public/health gate/window、日报gate相关5项测试通过；`context/fast_state.py:condition_truth_rows/action_gate` 使用既有匹配语义。无配置的直接入口仍保留 unknown，符合边界。 |
| R4 / P2 | FIXED | `FastDecisionPipeline.__init__/run` 使用 `min(180, quote_max_age_seconds)`，`Pipeline.run_decision` 构造时传有效配置，意图上限单列 intent_max_age_seconds=180。`test_age_threshold_follows_configured_quote_max_age` 通过：60秒配置的正常BUY公共阈值60，100秒旧行情被拒且阈值仍60。 |

R2 正向覆盖已通过：`test_notfresh_subtypes_reach_public_summary_health_and_daily` 验证缺bars、超龄分开聚合，参考报价、异步条件变化保留公共子因；`test_age_106s_with_14_bars_is_data_quality_not_stale` 等验证连续段不足独立于超龄。残余是任意异常文本仍可进入这些字段，现有绿灯没有覆盖此反例。

**Non-regression: OK（本地已审范围）**。`test_wait_only_ticks_leave_window_for_short_lived_other_instrument`、`test_flash_only_wait_claims_and_old_timestamp_still_blocks` 及准入拒绝、并发复检、失败留窗、T+1兼容测试通过：纯/缓存WAIT不新认领，成功准入后最终WAIT仍认领。`config.py:T0Config.fast_eval_seconds` 默认仍300。相关diff未见新增FeeModel、资金/风险/Guard/OMS或预算数值修改；既有Task30-03限定账户候选过滤保留原范围。`test_new_chain_buy_fill_sell_and_plan_reuse`、股票决策及程序风险退出等回归通过，未发现fill/trade损坏。未重写P2认领语义。

Claude残余返工：

1. R2：在NotFresh诊断及审计写入前使用封闭子因分类；只允许已知常量与经过类型校验的结构化参数，未知异常归unknown，不复制正文。public/progress/health及日报JSON/Markdown/HTML应一致遵守；正则脱敏与截断不能代替白名单。覆盖含动态底层信息的异常（如calendar coverage包装异常）及未知异常。
2. 补回归：注入合成异常正文，断言上述全部消费端均不包含正文；同时保持缺bars、连续段不足、超龄、参考报价、异步过期/账户变化/条件变化可区分。修正Task/索引中R2已完成声明，再独立复验。

验证记录：先执行git status与git diff --stat（大量既有dirty，71个tracked文件变更）；未改实现。使用现存 `.pytest-tmp2/task30/python/cpython-3.12.13-windows-x86_64-none/python.exe`，`PYTHONPATH=src;.venv/Lib/site-packages`、禁写pyc、pytest禁cacheprovider，basetemp `.pytest-tmp2/rereview3-task001`。实际 **274 passed，1 warning，14.65秒，退出0**；套件为 test_minute_observability、test_fast_decision、test_daily_report、test_fast_architecture_repairs、test_strategy_failure_diagnostics、test_stock_decision_chain、test_tradability、test_t0_runtime_repairs、test_paper_run_report、test_auto_paper、test_context、test_etf_minute_data、test_daily_revision_visibility、test_t0_minimal_runner、test_fast_startup、test_t0_contracts。提醒为Starlette/httpx弃用。

独立stdin探针在临时离线fixture库执行，最终退出0并关闭/清理临时库；首次因误导入不存在的render_html失败，改用实际write_report后取得上述结果。相关源文件git diff --check退出0。唯一新增交付文件为本记录；未连接150、部署、读取凭据或调用收费API。真实行情、费用、运行加载及成交闭环未验。
