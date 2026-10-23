---
id: ARCH-026
type: architecture-change
title: 本地 Windows 自动模拟运行入口与生命周期
status: implementing
created: 2026-10-22
updated: 2026-10-22
related_tasks: [TASK-20261022-01]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-026 本地 Windows 自动模拟运行入口与生命周期

## 状态

implementing（2026-10-22）。同日 [TASK-20261022-02](../tasks/TASK-20261022-02-独立复验本地双模型运行收尾.md) 独立复验发现两个停止边界缺陷（[Issue22-01](../issues/ISSUE-20261022-01-本地停止流程误启paper且强停状态未持久化.md)：stop期间经start(None)误启paper、强停halt仅内存未持久化），由 [TASK-20261022-03](../tasks/TASK-20261022-03-修复本地停止误启动与强停锁定丢失.md) 修复并补回归；门禁全部通过前保持 implementing。补充 ARCH-007（容器运行时）与 ARCH-013（自动监督）的本地差异，不替代、不重写其容器历史；Docker/Rancher 入口保留兼容。

## 背景

用户确认不再使用 Docker，要求 Windows 本地直接运行并便于改代码。现有 `scripts/run_auto.py` 是 rdctl/docker-compose 封装，无法脱离容器使用；`auto_paper.main()` 的互斥锁用 `fcntl`（Linux 专用），`healthy()` 在非 Linux 直接返回 False，`stop_child` 依赖 POSIX 信号语义（Windows 上 SIGTERM 等于 TerminateProcess，无排空、无报告）。

## 目标

不依赖 Docker/Rancher，在 Windows 上以项目 venv 提供本地 start/status/stop；同一运行根目录只允许一个 supervisor；停止可排空子进程；陈旧 PID/状态不会误杀其他进程；探活跨平台正确。

## 非目标

不改预算/调度/数量代码（16-02/16-04/16-08 所有权）、不改容器 compose 链路、不提供 halted 自动解锁、不接实盘、不承诺电脑关闭/休眠期间运行。

## 方案

- `auto_paper._pid_alive`：Windows 用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 探活（ERROR_ACCESS_DENIED 视为存活），POSIX 保持 `os.kill(pid, 0)`；`healthy()` 两平台统一走该函数，永不向 Windows 进程发信号零。
- `auto_paper.main()` 互斥锁：Windows `msvcrt.locking(LK_NBLCK)`，POSIX `flock`；锁文件永不删除（沿用 ARCH-017 语义），内核随进程死亡释放。
- 停止通道：`run_local.py stop` 写 `auto-paper.stop` 标志，supervisor 每 tick 轮询后置 stopping 并拒绝再启动新 writer；`run() finally` 排空子进程。排空中的状态消息明确显示"停止请求"，不伪装成普通等待。
- Windows `stop_child`：paper 子进程先等待其有界自然退出（100s）；超时才 TerminateProcess 并置 `halted`（结果未知，需人工核对）。serve 无订单，直接结束。POSIX 路径不变。
- `scripts/run_local.py`：start（fresh 心跳+存活 pid 则拒绝重复启动；端口被占拒绝；无 HTTPS_PROXY 拒绝——JEV 直连 451；DETACHED 启动并继承完整父环境，缺失 SystemRoot/PATH 会使 Winsock 初始化失败）、status（心跳/phase/预算/代码版本/日志位置，凭据不输出）、stop（写标志并等待排空 150s；超时仅在进程映像名确认为 python 后 `taskkill /T /F` 该精确 pid，并在 state 文件置 halted）。
- 配置：`config/t0-local.example.toml`——独立账户 `paper-t0-local-1k`、绝对 sqlite 路径（每实验一库）、初始资金 1000 CNY 写在 `[t0].initial_cash`（`_validate_mode_isolation` 无条件以 t0 值覆盖 paper 值）；300/200/100 敞口限额、费率、冷却、日预算全部沿用冻结契约不放宽。

## 模块影响

`src/trademind/execution/auto_paper.py`（仅生命周期差异，监督循环不重构）；新增 `scripts/run_local.py`、`config/t0-local.example.toml`；`tests/test_auto_paper.py` 一处 Windows 探活断言意图更新；新增 `tests/test_local_lifecycle.py`。

## 数据流影响

本地：`run_local start` → 脱离终端的 supervisor → 交易时段内有界 `t0-paper` 子进程（writer），午休/休市切换 `serve` 子进程（同一 8321 端口，paper 运行期间页面不可达，不等于交易停止）。停止：标志文件 → 排空 → 退出；强制结束 → halted 等待人工。

## 数据所有权影响

每实验一个 sqlite 库与运行根目录（state/pid/stop/lock/log/paper-runs 同目录）；账户账本、预算、日历所有权不变。旧 `paper-t0-container` 账户与库保持原样。

## 部署 / 运行影响

新增本地 Windows 入口；容器入口（compose `auto`/`workbench`）不受影响。运行环境要求记录于 `docs/project/setup.md` 与 `docs/project/operations.md`。

## 代码范围

- `src/trademind/execution/auto_paper.py`
- `scripts/run_local.py`
- `config/t0-local.example.toml`
- `tests/test_local_lifecycle.py`、`tests/test_auto_paper.py`

## 测试范围

跨平台探活（存活/已回收进程/陈旧心跳/禁止信号零）、msvcrt 互斥、停止标志排空且不启动新 writer、paper 排空与强制+halt、serve 直接结束、run_local 重复启动/端口占用/无代理拒绝/启动登记 pid/停止确认/强制升级前映像校验/拒绝杀非 python 进程、示例配置加载（1000 元、隔离账户、本地端口）。

## 实施中的设计变化

- 子进程环境改为继承完整父环境（初版只传代理变量导致子进程 Winsock 初始化失败 WinError 10106，已修复并有真实启动证据）。
- 创建参数补 `CREATE_BREAKAWAY_FROM_JOB`（含禁止 breakaway 时的降级路径）：真实事故 2026-10-22 13:10，启动方作业对象关闭时监督器整树被连带终止（日志无 traceback、子进程被优雅排空），是"关闭启动会话不影响交易"的必要条件。
- 停止状态机修正（Task22-03/Issue22-01）：stopping 时完全跳过启动转换块（原 desired=None 写法会落入 `kind != desired` 分支以 None 调 start 构造 paper 命令）；`start()` 拒绝 stopping 状态与未知 kind，不再把 None 默认为 paper。终态持久化：`shutdown()` 作为 run() 唯一收尾路径，排空子进程后按既有 `result_policy` 分类 paper 最终报告（缺失/损坏/未知/失败置 halted 并持久化，合法终态不误暂停，不清除既有 halt），再原子写回最终状态（含 halted 与原因）；Windows paper 强停立即原子持久化 halted，重启保持人工核对门禁；停止过程异常（kill/wait 抛错）置 halted 并持久化，不报告成功排空；持久化失败显式抛出，不悄悄成功。
- 启动恢复分类（Task22-01/Issue22-02）：硬终止跳过收尾时，`__init__` 对未终态 paper 旧状态执行 `_recover_interrupted_paper`——旧 `child_pid` 探活（存活即 halt）+ 按 `result_policy` 分类旧报告（缺失/损坏/未知/失败保留 halt 并原子持久化，合法完成正常恢复）；serve 被杀/首次启动/干净停止不误暂停；既有 halted 不清除；tick 状态新增 `child_pid` 供核验。

## 验证

`tests/test_local_lifecycle.py` 17 项 + `test_auto_paper.py` 19 项 + launcher/container 相关 52 项通过；全量 pytest 除既有 `test_check_docs.py::test_real_repo_has_zero_errors`（两个 F 盘历史 file:// 链接错误，TASK 创建时已记录、非本轮引入）外全部通过。真实行为证据：supervisor 启动（pid 心跳、状态文件、代码版本 843ab9f）、午休 serve 阶段工作台 HTTP 200（账户 `paper-t0-local-1k` 现金恰 1000、version 1、无持仓）、优雅 stop 排空退出、外部终止后排空、3600 秒轮转换（3 份有界报告）、收盘排空与日报生成（详见 [研究报告](../research/20261022-local-run-verification.md)）。真实盘中双模型有界运行由 TASK-20261022-01 验收（首日：探针/策略/费用真实，JEV 决策因计划合法 WAIT 未发生）。

## 当前事实同步

- `docs/project/operations.md`：已同步本地 start/status/stop 入口
- `docs/project/setup.md`：已同步 D:\trademind 工作副本、venv/uv、代理要求
- `docs/project/code-map.md`：已同步本地入口定位
- `docs/project/system-overview.md`、`flows.md`、`current-status.md`、`README.md`：已同步
- `docs/roadmap.md`：已同步本地验收阶段

## 关联内容

- Task：[TASK-20261022-01](../tasks/TASK-20261022-01-本地千元双模型自动模拟交易落地.md)
- Issue：无新增（Winsock 环境问题是本记录实施中的设计变化，非开放缺陷）
- Research：随真实运行验收补充分日期报告
- Previous Architecture：[ARCH-007](07-container-runtime.md)、[ARCH-013](13-auto-paper-supervisor.md)、[ARCH-017](17-writer-lease.md)
- Next Architecture：暂无
