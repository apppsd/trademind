# 首版代码独立验收

日期：2026-10-07。以下为首次验收（修复前）；当前复验结论见末尾。首次结论：现有 93 项测试通过，但首版交易闭环验收不通过。当前是可运行的工程原型，存在阻断性缺陷；不能进入前瞻交易结果评估或据此证明 AI 有效。

## 范围与方法

读取实际 Python 配置、核心代码、现有测试及项目事实；运行 `.venv\Scripts\python.exe -m pytest -q`，exit 0，93 passed，一个 FastAPI / Starlette 弃用警告。

另运行 `.venv\Scripts\python.exe docs/research/20261007-audit-repro.py`，exit 0。脚本仅使用临时 SQLite、fixture 和 stub；不读取私有配置或环境密钥，不发网络请求，不修改应用代码。复现脚本使用现有测试工厂构建合法输入，错误授权输入仅用于验证必须拒绝的场景。

## 确认缺陷

| Issue / 修复 Task | 结论与证据 | 主要位置 |
|---|---|---|
| ISSUE-04 / TASK-30 | 有 3000 股且可卖 3000 股，SELL 到 0 被 VETO `replan_zero_lot_quantity`；减仓差值为负后被当成无数量 | guard/guard.py:146 |
| ISSUE-05 / TASK-31 | 100万元无持仓，只预留148148元，净资产变1148148元；撤单后预留仍148148元 | portfolio/ledger.py:265；guard/guard.py:167；execution/oms.py:69 |
| ISSUE-06 / TASK-32 | Guard 返回 VETO，传入 OMS 后仍创建 ACKNOWLEDGED 订单；没有校验 verdict、绑定、期限、账户或数量 | execution/oms.py:34 |
| ISSUE-07 / TASK-33 | 00:00 UTC 调用成交器，返回 07:30 UTC 才可见的行情成交；更早时间检索返回更晚分析的 Thesis；Replay 仍实例化真实 DeepSeek 并保留 AKShare 数据选择 | execution/paper.py:44；pipeline.py:139；evaluation/replay.py:89 |
| ISSUE-08 / TASK-34 | 日切后可卖3000，事件重建后变0；注入账本失败后 fills=1、持仓=0，重试不应用成交 | portfolio/ledger.py:107、237；execution/oms.py:79；storage/db.py:116 |
| ISSUE-09 / TASK-35 | requirements.txt 的 editable 依赖固定到仅有文档的旧提交；该提交无 pyproject.toml，README 安装第一步无法依此构建包 | requirements.txt 中 trademind editable 行 |
| ISSUE-10 / TASK-36 | JEV HTTP 载荷只有本地 ID、标的、时间和 question_id，没有快照特征、Thesis 内容或题目说明；无已实现服务端拉取契约 | models/stub.py:180；agents/trader.py:45 |
| ISSUE-11 / TASK-37 | Orchestrator 未被 CLI/API/Pipeline 引用；决策链始终把起始 as_of 当提交时的 now，模型耗时不能使意图超时 | pipeline.py:193；orchestrator/scheduler.py:83 |

前五类有离线动态复现；安装依赖由 `git cat-file -e 6d848108b2d853c26ad376d6929d58f9b4a74c6c:pyproject.toml` exit 128 确认，不实际运行网络安装。JEV 与调度问题是静态调用链核验，真实接口和耗时请求未测试。

## 现有测试为什么未发现

- `test_full_offline_chain_buy_holding_sell` 的默认 stub 返回 HOLD，BUY 分支可以完全不执行；即使进入，也不要求 SELL 成功、减少持仓或最终平仓。
- 风控并发测试实际顺序执行两次，不制造并发竞争；只检查现金不为负，未检查敞口与净资产守恒。
- OMS 测试用只含 id / verdict 的假授权对象，没有测试 VETO、过期和错误意图被拒绝。
- 恢复测试不检查可卖数量，也未注入 fills 落库与 ledger 之间故障。
- 日损测试将出金直接当亏损，没有验证存取款中性和市值损失。

这些测试仍有基本契约价值，但“93 passed”不能推出验收矩阵全部通过。

## 建议修复顺序与重新验收

1. TASK-35 先修安装入口；TASK-31 修账本资金不变量，TASK-34 修事务与恢复。
2. TASK-30 修合法卖出，TASK-32 修订单授权边界；其依赖以新 Task 为准。
3. TASK-33 修严格时点/Replay隔离，TASK-36 修 JEV 输入，TASK-37 接入调度与真实提交期限。
4. 针对所有复现加入强断言回归，再重跑全套测试和独立买入 → 成交 → 日切 → SELL → 平仓 → 重启 → 结果/反思场景。不能允许 HOLD 跳过验收。
5. 更新原 Task 的验证结论，只有修复和重新验收通过才解除 TASK-23 的代码阻塞；真实模型/行情与前瞻观察仍有独立门槛。

## 未验证与缺口

未调用真实模型、JEV、免费数据或券商。未测实盘、盈利、真实延迟、生产部署和完整浏览器交互；没有对所有模块作形式化审计。

CI、规则基线、向量检索、长期前瞻观察仍有文档声明的缺口。模式 shadow 是否真正禁止生成模拟订单、日损基准是否按真实市值维护、UNKNOWN 对账前是否拦截新增风险，以及数据源空返回能否降级，需要随 TASK-32 / 33 / 34 / 37 验证；尚未作为本次独立确认缺陷逐项定性。

本次未提交或推送。原有实现与未提交修改保持完整，修复交给执行对话。

## 修复复验（2026-10-07）

用户明确授权本对话修复代码，TASK-30～37 已完成，ISSUE-04～11 已 resolved。初次失败证据保留在上文。

| 验证 | 实际结果 |
|---|---|
| 全套 `.venv/Scripts/python.exe -m pytest` | 133 passed；1 个现有 Starlette 弃用警告 |
| `tests/test_audit_regressions.py` | 新增 40 项，包含资金不变量、部分成交、授权篡改/过期、故障回滚/重试、T+1/快照丢失恢复、UNKNOWN、未来信息、无网络 Replay、慢模型 TTL、shadow、JEV mock、队列与并发 |
| 脚本化 AI 实际交易链 | 三笔成交：BUY、部分 SELL、清仓；持仓与预留归零，不能由 HOLD 跳过 |
| 干净环境 | 临时 venv 按 requirements.txt 安装成功并测试通过；构建/安装 wheel，在仓库外确认 Schema/网页/fixtures 完整，replay-demo --days 5 无错误 |
| CLI 回放 | 5 个交易日、25 次默认 stub HOLD、0 错误；此结果只证明工程入口，非模型表现 |

代码事实以 system-overview、flows、code-map 为入口，具体解决方法分别在 ISSUE-04～11；本报告不复制每处实现。

安装校验命令：`.venv/Scripts/python.exe scripts/verify_install.py`。回归复验入口：`.venv/Scripts/python.exe docs/research/20261007-audit-repro.py`（改为运行强断言回归，原故障观测仍保留于上文）。

仍未验证真实 DeepSeek/JEV、免费数据网络接口、前瞻观察、实际延迟、完整浏览器交互、盈利与实盘。JEV HTTP 为项目自定协议，mock 通过不等于官方服务可用。ARCH-001 / TASK-23 继续进行。未 commit/push，未触碰个人知识库。

