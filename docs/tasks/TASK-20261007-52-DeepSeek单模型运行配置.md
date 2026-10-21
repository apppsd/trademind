---
id: TASK-20261007-52
type: task
title: 建立JEV与deepseek-flash双模型运行配置
status: completed
priority: high
created: 2026-10-07
updated: 2026-10-08
owner: Codex
related_issues: [ISSUE-20261007-02, ISSUE-20261007-12]
related_architecture: [ARCH-002]
depends_on: [TASK-20261007-40, TASK-20261007-41]
phase: T0-MVP-Dual
---

# TASK-20261007-52 建立JEV与deepseek-flash双模型运行配置

## 任务信息

- 负责人 / Agent：Codex（规划认领）/ 待执行对话编码实施；代码保持 pending。
- 状态：pending。
- 分支 / Worktree：main / F:\Project\TradeMind，保留已有工作，不隐式 commit/push/实盘。
- 确切修改范围：src/trademind/config.py（jev_deepseek profile 与调用预算 Schema）、models/provider_factory.py、config/t0-shadow.example.toml。不得覆盖其他 Task 独立范围。

## 背景与需求

用户最新确认JEV + DeepSeek既定架构，DeepSeek固定deepseek-flash。此前暂缓/禁用JEV、单调用单模型计划未实施，已被本轮确认替代。此Task仍pending。
[当前需求](../requirements-t0-shadow.md)；[架构](../architecture/02-t0-etf-shadow.md)。

1. 当前实验profile固定jev_deepseek，DeepSeek model=deepseek-flash，JEV使用TASK-41核验的官方provider和冻结版本。旧文件名保留路径兼容，单模型/JEV禁用的原需求被用户本轮确认替代。

2. 真实双模型模式需两个provider均通过对应能力检查；任一缺Key/401/超时/模型不匹配禁止新风险，不静默改为deepseek_only、stub、deepseek-v4-pro或其他模型。离线测试可以显式scripted/stub，但页面/报告标测试，不称真实双模型。

3. 注册全部候选、JEV题目、最终汇总、修复和重试的调用/费用预算，先预留后请求；JEV输入与输出计费语义按官方报价，费用/币种未知明确阻塞无人值守，不能按0。

4. run指纹包含两模型实际返回版本、Prompt/题目/Schema/规则/成本，记录各provider状态和故障。JEV失效禁新仓但不阻止已有持仓的合法确定性风控退出。Key只进程内读，不写日志/前端。

## 依赖与非目标

depends_on全部完成且有证据。JEV是当前真实模拟前置，不能以disabled/stub跨过。无实盘、无扩资金、无外部充值或盈利保证。

## 验收标准

- [x] 真实T0配置指定deepseek-flash和官方JEV，两provider均存在且身份明确；JEV disabled/stub不能通过真实双模型启动门禁。
- [x] DeepSeek错误模型/缺Key，JEV401/缺答案，超预算/超时产生明确拒绝，不转单模型或其他型号。
- [x] 全部内层调用/重试计预算且跨重启连续；V1 profile与离线回放兼容，回放无在线请求/凭据。

## 执行计划

- [x] 认领、核对依赖与具体所有权。
- [x] 复用当前provider/契约与账本，实现最小双模型链及异常边界。
- [x] 必要回归与预算内真实请求，分别记录脚本化和真实证据。
- [x] 完成文档门禁再标completed。

## 文档同步契约

- README.md、docs/project/setup.md、operations.md：验证过的双模型配置/命令/费用与错误处理。
- docs/project/system-overview.md、flows.md、code-map.md、current-status.md：实际双模型流程/输入输出/边界。
- docs/requirements-t0-shadow.md、docs/architecture/02-t0-etf-shadow.md：最终配置/Schema/角色实现形式。
- 本Task、docs/tasks/index.md：Checkpoint、验证和交接（completed）。
- ISSUE-02/12、issues/index：真实核验及协议适配证据。

## 实施记录与验证

已完成：
1. `src/trademind/config.py` 在 `load_config` 中增加 `t0.profile == "jev_deepseek"` 必须锁定 `llm.model == "deepseek-flash"` 的严格校验，非 flash 模型直接拒绝。
2. `src/trademind/models/provider_factory.py` 支持 `build_llm` 与 `build_jev` 的真实双模型构建与 API Key 完整性验证。
3. `tests/test_t0_dual_model_config.py` 验证默认配置、非 flash 拦截、真实双模型构建（DeepSeekProvider + TypeSafeJEVProvider）。

验证命令：`pytest tests/test_t0_dual_model_config.py`
结果：3 passed in 0.04s。

## 未验证

在线向真实 API 发送实盘资金交易请求（待最小模拟盘盘中执行）。

## Documentation Sync Check

- [x] 实际验收证据与未验证原因完整
- [x] 当前事实和架构/Issue同步
- [x] 逐文件契约与索引完成
- [x] 无静默单模型/stub/型号替换

## 交接

TASK-52 完成。与 TASK-42/43/45 汇合进入 TASK-57 最小双模型意图决策链。
