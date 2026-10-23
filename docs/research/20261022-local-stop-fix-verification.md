# 本地停止边界修复验证（2026-10-22）

任务：[TASK-20261022-03](../tasks/TASK-20261022-03-修复本地停止误启动与强停锁定丢失.md) · Issue：[Issue22-01](../issues/ISSUE-20261022-01-本地停止流程误启paper且强停状态未持久化.md)、[Issue22-02](../issues/ISSUE-20261022-02-异常终止重启未核验上一轮paper结果.md) · 前序：[Task22-02 独立复验](20261022-local-independent-review.md)、[隔离复现脚本](20261022-local-stop-repro.py)

## 三轮修复（Task22-01启动恢复，Issue22-02）

外部硬终止会跳过 shutdown，启动侧直接信任旧 halted=false。修复：`Supervisor.__init__` 新增 `_recover_interrupted_paper`——旧状态 `phase=paper` 且未终态时，先对旧 `child_pid` 探活（存活即 halt），再按 `result_policy` 分类旧报告（缺失/损坏/未知/失败 → halted 原子持久化；合法完成正常恢复）；serve 被杀/首次启动/干净停止不误暂停；既有 halted 不清除；tick 状态新增 `child_pid`。回归 6 项对照全绿 + 复验者风格反例（损坏报告）halted=True/零新paper。真实实例已用修复版重启（pid 267928）。

## 二轮修复（Task22-04 复验退回后）

Task22-04 发现第三处缺口：`shutdown()` 不核验 paper 等待期间自然退出后的最终排空报告，缺失/未知结果仍持久化 halted=false。修复：`shutdown()` 排空后按既有 `result_policy` 分类报告（读失败按空报告），halt 类结果置 halted 并以原因原子持久化，合法终态不误暂停，既有 halt 不被清除。回归：lifecycle 新增 2 项（missing/unknown/error/clean 四类对照 + 既有 halt 保留），lifecycle 29 项 + supervisor 19 项通过；**Task22-04 隔离片段逐字复跑转绿**（missing True/True、unknown True/True、classifiable False/False）。

## 修复内容（`src/trademind/execution/auto_paper.py`，最小范围）

1. **BUG-1 stop期间误启paper**：`tick()` 停止时不再置 `desired=None`（该写法使 `kind != desired` 成立并调用 `start(None)`，start 把非 serve 值一律当 paper 构造 t0-paper 命令）；改为 stopping 时完全跳过启动转换块。`start()` 增加双守卫：stopping 状态与未知 kind（含 None）直接抛错，不再默认为 paper。
2. **BUG-2 强停halt未持久化**：Windows paper 强停路径设置 halted 后立即 `persist_halt()` 原子写回状态文件；`run()` 收尾统一走 `shutdown()` = 排空子进程（异常捕获置 halted）→ `persist_final_state()` 原子写终态（halted/原因/terminal 标记）。持久化失败显式抛出不报告成功；宽限期正常退出不误置 halted；已有未知/风控暂停不被清除（halted 从内存原值写回）。

## 验证

- 修复前：`docs/research/20261022-local-stop-repro.py` 输出 BUG-1、BUG-2 两行（隔离 FakeChild+临时目录，无网络/收费/生产操作）。
- 修复后：新增回归 **7 项**（serve/paper/已退出child三种stop入口零launch、start拒绝None/未知/stopping、强停halt跨重建持久且不启paper付费预检、shutdown终态持久且保留既有halt、停止过程异常置未知并持久化），`tests/test_local_lifecycle.py` 24 项（新增7项回归+2项加强）+ `test_auto_paper.py` 19 项全绿。
- 历史复现脚本按计划保留原样（以"缺陷存在"为断言）；修复后运行其 BUG-1 断言失败（line 53 AssertionError）——此即修复生效证据，不要求脚本转绿。
- 全量 pytest：1 个既有文档元测试失败（F盘链接）+ 1 个**环境敏感**失败见下节；其余全部通过。`git diff --check` 通过；check_docs 2 个既有 F 盘错误 + 索引状态一致性错误已随本Task状态行同步消除。

## 环境敏感测试记录（非本Task缺陷，不改他人测试）

`test_minute_source_routing.py::test_cross_check_observer_waits_within_round_budget` 在 15:0x 起 3/3 稳定失败：实测本机线程启动+join 延迟升至 **140-188ms**（当日早间同测试通过），超过该测试 0.1s 轮预算，观察 worker 无法在预算内启动。`src/trademind/market/` 未改动（git diff 证实），停掉 supervisor 后仍失败，排除本任务改动与后台负载。推测与本日大量新建文件触发的系统扫描或长时间高负载有关；属时序敏感测试的环境脆弱性，留给路由测试 Owner 知悉。

## 未验项

- 真实运行实例的强杀验证按任务要求不执行（"不能用正在交易的实例测试强杀"）；停止边界修复以隔离回归+代码审查交付。
- Task22-01 的 JEV 决策、买卖闭环、次日恢复继续按其事实保持未验。
