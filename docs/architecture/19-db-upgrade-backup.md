---
id: ARCH-019
type: architecture-change
title: 持久化升级与备份恢复职责
status: implemented
created: 2026-10-14
updated: 2026-10-14
related_tasks: [TASK-20261013-17]
related_issues: [ISSUE-20261013-09]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 持久化升级与备份恢复职责

## 背景、目标与非目标

实施前，打开旧库只登记固定版本号，`schema.sql`追加的索引/列不会被补建；遇到更新程序写的库也不拒绝——降级程序打开新库会静默产生结构不一致（ISSUE-20261013-09）。备份只有"停进程复制文件"一条路，恢复后没有任何验证手段。目标：打开时按版本注册表顺序原地升级（每步一个事务、失败完整回退含版本戳）；更高Schema版本拒绝启动；提供WAL一致在线备份与只读恢复校验。非目标：不做降级迁移、不改任何既有表结构语义、不引入SQLite以外的存储、v1→v2只做附加变更不改写历史数据。

## 方案与边界

`src/trademind/storage/db.py`：

- `schema_migrations`注册表最先建立；先读已应用版本，若高于`LATEST_SCHEMA_VERSION`立即抛`SchemaVersionError`拒绝打开/修改——任何结构写入之前。
- v1基线：`schema.sql`幂等（IF NOT EXISTS）执行后登记版本1。
- `MIGRATIONS`有序升级，每版本一个`BEGIN IMMEDIATE`事务：任一语句失败整体回滚（含版本戳），下次打开自动重试；全部为附加变更。
- 迁移2（首个升级）：`intents`补`created_at`列并从`payload_json`回填（窗口化查询需要真实列，TASK-20261013-16窗口化读取的前置）+四个报表窗口索引`ix_intents_created`/`ix_calls_created`/`ix_ctx_asof`/`ix_plan_hist_acct_avail`。
- `backup_to(target)`：SQLite backup API在线一致备份（含WAL内容），目标已存在拒绝覆盖。
- `verify_database(path)`：对副本用原始只读连接（绝不走会迁移/盖戳的`Database()`）校验integrity_check、foreign_key_check、schema版本不超过支持值、orders/fills/account_events/reservations引用闭包（悬空成交/事件/预留计数），返回结构化结论。

**（2026-10-14复核更正，原文保留：独立复核（[ISSUE-20261013-09](../issues/ISSUE-20261013-09-数据库升级只登记固定版本且不拒绝未来版本.md)）发现两条缺口并已修复——①"拒绝发生在任何结构写入之前"不彻底：`__init__`曾先执行`PRAGMA journal_mode=WAL`再检查版本，DELETE-journal未来库文件在拒绝前被改写。现行顺序：路径解析→建目录→`_refuse_newer_schema`（`file:...?mode=ro&immutable=1`只读连接预检版本，不产生`-wal/-shm`旁文件；`mode=ro`单独使用会在WAL库上创建旁文件，故必须immutable）→才connect与任何PRAGMA；任何`__init__`失败关闭连接。②verify_database原只做物理/引用检查，账目不平的副本仍ok=True：现逐账户重放事件账本（`_replay_account_ledger`，与Ledger算术一致，含从成交note复算fee/price与移动平均成本），对账现金/冻结/Σactive预留/持仓(quantity+avg_cost)/version==max_seq五项，`ledger_ok`并入`ok`；T+1 availability刻意不重放（需市场profile知识）。详见[TASK-20261013-17](../tasks/TASK-20261013-17-建立可验证数据库升级与恢复流程.md)"修复实施"。）**

恢复流程：副本复制到独立位置→`verify_database`通过→再以`Database()`打开（自动补齐未完成的迁移）。升级发生在写者入口：Pipeline构造即取写租约（[ARCH-017](17-writer-lease.md)）后才打开数据库，迁移不会与第二个进程交错。

## 部署与安全

改动`storage/db.py`（迁移/备份/校验逻辑与内联MIGRATIONS定义），schema.sql基线未变（v2索引仅存在于迁移2，新库打开时同样经migrate补齐）；无业务代码改动；`scripts/verify_install.py`增加Database/backup_to/verify_database安装冒烟。生产库在下次以新程序打开时自动完成v1→v2（附加性，已有数据不改写）；该真实库迁移尚未实际观察到（见Task未验项）。

## 实施范围与验证

2026-10-14宿主实测：`tests/test_storage.py`+`tests/test_recovery.py`共22项通过——覆盖旧库打开自动补建v2列/索引、版本戳幂等（重开不重复执行）、更高版本拒绝（SchemaVersionError）、迁移失败完整回退重试、backup_to内容一致（WAL）、拒绝覆盖、verify对完好/截断/引用悬空库的正反例。连同market/calendar共49项、全量回归481项通过。真实生产库（含容器内库）的v1→v2迁移未在真实数据上观察。

## 最终结果

代码边界已实施并有隔离库测试证据；生产库实际迁移与跨版本恢复演练未执行，属运维首次升级时的观察项。本记录定义持久化升级/恢复职责，与ARCH-017（升级在写租约保护下进行）配合。
