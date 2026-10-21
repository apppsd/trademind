---
id: ARCH-017
type: architecture-change
title: 跨进程账户单写租约
status: implemented
created: 2026-10-14
updated: 2026-10-14
related_tasks: [TASK-20261013-14]
related_issues: [ISSUE-20261013-06]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 跨进程账户单写租约

## 背景、目标与非目标

实施前，账户单写者约束只覆盖部分启动入口：直接CLI与auto子进程可各自构造Pipeline并发写同一账户（ISSUE-20261013-06），进程内锁无法跨进程。目标：所有可能写账户的入口（直接CLI、auto paper子进程）在打开数据库、做恢复/迁移或任何付费预约之前取得同一互斥；第二个写者立即非零失败、零调用、零账户写入。非目标：不改变进程内账本锁、不改SQLite事务边界、不改auto监督器结构（ARCH-013保留）、只读报告不引入租约。

## 方案与边界

`src/trademind/execution/writer_lock.py`：

- 租约=数据库旁专用`.writer.lock`文件上的字节范围锁（Windows `msvcrt.locking`非阻塞 / POSIX `fcntl.flock`非阻塞），键=规范化数据库路径（Windows统一小写）+`sha1(account_id)`；同库多账户仍可并行写，跨账户一致性由SQLite事务保证。
- 内核在持锁进程死亡时释放锁：崩溃不留陈旧锁，不存在"任意删锁解锁"；锁文件永不被删除或截断（unlink与new holder竞争会在同一inode上产生两个写者）。文件内holder元数据（pid/host/account/时间）仅供诊断，永不用于陈旧判断。
- 取得时机：Pipeline构造时（`acquire_writer_lease`）先于数据库打开、恢复、迁移与预算预约；冲突抛`WriterLockHeld`，runner两个写入口捕获后输出JSON `status=failed`并退出码1。输者全程未打开数据库文件（测试断言db文件不存在）。
- `:memory:`库无跨进程租约（返回None）；非本地sqlite URL（远程host/非sqlite scheme）拒绝并要求本地库。
- 移交与释放：auto supervisor→paper子进程天然经Pipeline构造交接；serve（工作台）前由runner显式释放租约——工作台报告路径只读，不需要写租约（测试覆盖：租约被持有时日报仍可生成）。

## 部署与安全

改动：新增`src/trademind/execution/writer_lock.py`；`pipeline.py`（构造取得/`release_writer_lease`）、`runner.py`（WriterLockHeld JSON失败输出、serve前释放）、`evaluation/fast_comparison.py`与`evaluation/prompt_paper.py`（隔离评测复用前释放）。`api/app.py`与`scripts/run_auto.py`本轮未改（合同曾列出）：auto子进程经Pipeline构造自动覆盖，serve当前无账户写路径；若未来加入经serve/API的账户写入，必须先取得租约，该扩展未实施亦未验证。

## 实施范围与验证

2026-10-14 Windows宿主实测（真实子进程，非Mock）：`tests/test_writer_lock.py`8项通过——锁键路径规范化、非本地URL拒绝、第二Pipeline零打开零写入、释放后可重取、同库双账户并行写、真实子进程竞争（持有者存活时rival DENIED退出码3、同进程第二句柄冲突、持有者死亡后内核释放可重取）、CLI第二写者退出码1+`WriterLockHeld`+数据库文件未创建、只读日报不占租约。持有者死亡释放原为立即重试，Windows内核清理可晚于进程对象信号，实测暴露竞态：测试改为10秒有界轮询（生产获取路径保持立即失败语义不变），修复后连续3次独立运行稳定通过；连同auto/container测试共43项通过。全量回归481项通过。Linux容器侧真实子进程验证未执行（见Task未验项），POSIX路径走`fcntl.flock`同一设计。

## 最终结果

代码边界已实施并有Windows真实子进程证据；Linux容器验证与真实auto↔手动并发场景未验，由后续验收判断。与ARCH-013并存：ARCH-013描述监督器结构，本记录定义账户写互斥边界。

## 2026-10-14 复核更正（补充记录，上方原文保留）

独立复核（[TASK-20261014-01](../tasks/TASK-20261014-01-独立校验改进任务完成质量.md)，[ISSUE-20261013-06](../issues/ISSUE-20261013-06-账户单写者互斥仅覆盖部分启动入口.md)重新开放）发现本记录"方案与边界"第5条与"部署与安全"中"serve（工作台）前由runner显式释放租约——工作台报告路径只读，不需要写租约""serve当前无账户写路径"**与实际代码不符**。更正如下（原文不改，以本节为准）：

实际行为（`src/trademind/runner.py:88-95` cmd_serve、`src/trademind/api/app.py:119-127`）：

- serve入口经`init_app`构造Pipeline——租约在Pipeline构造时取得并**持有至serve进程退出**（无显式释放，进程退出由内核释放）。这是必需的正确行为：serve存在token门控的账户写端点（`api/app.py:260` kill_switch、`:268` decide、`:276` poll_fills，经`require_token`校验），serve是一个真实写入口，必须持锁。
- runner运行类命令（t0-paper等）在进程结束前的finally中显式释放（`runner.py:303`）；评测脚本（`evaluation/fast_archives.py`、`fast_comparison.py`、`prompt_paper.py`）复用前释放。
- 只读端点（status/account/orders/decisions/theses/judgments/memories/audit/trace、日报页面）不取得租约——另一进程持锁时只读日报仍可生成（test_read_only_daily_report_needs_no_writer_lease）。

入口/取得/释放矩阵（现行实现）：

| 入口 | 取得 | 释放 |
|---|---|---|
| runner 运行类命令（t0-paper等） | Pipeline构造 | 进程结束前finally显式释放（runner.py:303） |
| runner serve（工作台） | init_app→Pipeline构造 | 持至进程退出（内核释放） |
| auto supervisor→paper子进程 | 子进程Pipeline构造 | 子进程退出内核释放 |
| 评测脚本（fast_archives/fast_comparison/prompt_paper） | Pipeline构造（如构造） | 复用前显式释放 |
| 只读日报/查询端点 | 不取得 | — |

同轮更正锁键路径身份：原实现`writer_lock.database_file`用urlparse解析sqlite URL、保留前导`/`，与`Database`（直接剥`sqlite:///`）语义不一致，相对URL与绝对拼写会得到不同锁文件。现两者共用`storage/sqlite_url.py::sqlite_url_to_path`（相对URL保持cwd相对、绝对URL取绝对路径），同一实际库的任何拼写得到同一租约；Windows/POSIX各有真实子进程竞争测试（tests/test_writer_lock.py）。

Linux实测（2026-10-14，python:3.12-slim容器，真实子进程，fcntl.flock）：相对URL持有者取得租约且锁文件生成于相对路径数据库旁；另一cwd下同相对URL（不同实际库）允许并行；同一实际库的绝对拼写被拒（WriterLockHeld）。输出四项断言全真、退出码0（脚本与JSON结果存Task-14验证记录）。注意：该验证覆盖租约原语本身（Pipeline所用的同一`acquire_writer_lease`入口）与相对storage.url+可写挂载；auto监督器→paper子进程的完整编排未在容器内重演，仍归容器验收。

原记录"输者零打开零写入、同库多账户并行、持锁进程死亡自动释放、锁文件永不删除"等设计表述经复核与代码一致，本节不更正。
