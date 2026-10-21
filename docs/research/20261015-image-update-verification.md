---
id: RESEARCH-20261015-IMAGE-UPDATE
type: research
title: 容器镜像更新与源码一致性核验（2026-10-15晚）
created: 2026-10-15
author: Claude Code
related: [TASK-20261015-03, TASK-20261014-04, TASK-20261013-07, TASK-20261012-05]
---

# 容器镜像更新与源码一致性核验（2026-10-15晚）

用户授权将最新工作区代码更新到本机 Rancher Desktop（同日晚间，已收盘）。本次为**机械镜像更新与核验**，不构成 [Task13-06](../tasks/TASK-20261013-06-修复后真实盘中验收.md) 的部署真实修复验收，不解除 halted，不启动交易。

## 部署内容

- 旧镜像 `f2a53f717d78`（2026-10-13 11:44）→ 新镜像 **`cfa3888eda83`**（2026-10-15 23:04:25+08 构建）。
- 相对旧镜像的代码增量：当日全部工作区改动——跨源观察四项修复+缓存分支修复（router.py，[Task14-04](../tasks/TASK-20261014-04-实现分钟行情质量驱动多源回退.md)）、采集/对照工具配置资金适配（fast_archives/fast_comparison/prompt_paper，[Task13-07](../tasks/TASK-20261013-07-修复采集对照初始资金硬编码.md)）、以及此前已在工作区而旧镜像缺失的改动（含 10-13 后新增的 storage/sqlite_url.py、execution/writer_lock.py、market/calendar_update.py 等模块）。
- 构建命令：`docker compose build --progress=plain --build-arg PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple`（华为镜像源，[TASK-20261009-11](../tasks/TASK-20261009-11-Rancher容器运行.md) 已验证的构建方式，本机官方 PyPI 实测约 27KB/s 不可用；依赖版本不变）。`.dockerignore` 排除 .env/local 配置/runtime，镜像不含凭据。

## 验证证据（2026-10-15 23:04～23:10）

1. **镜像内隔离回归**：`docker run --rm --entrypoint python trademind:local -m pytest tests/test_minute_source_routing.py tests/test_fast_archives.py tests/test_fast_comparison.py -q` → **47 passed**（缓存拒绝、资金适配、预算口径相关套件）。
2. **容器重建**：`docker compose --profile auto up -d auto` → trademind-auto-1 Recreated/Started，镜像 `sha256:cfa3888eda83…`，RestartCount=0。
3. **源码一致性**：容器 `/app/src/trademind` 与工作区逐 .py 文件 SHA256（CRLF/LF 归一化，与 Task15-03 验收同方法）→ **86/86 全部一致**。验收报告所列 28 份不同、3 份缺失（writer_lock/calendar_update/sqlite_url）全部消除。
4. **运行状态**：healthy；`auto-paper-state.json` phase=serve、**halted=true（保持，未被重建清除——解锁须明确授权）**、session_open=false；首页 `http://127.0.0.1:8321/` HTTP 200。
5. 数据边界：仅 bind 挂载 `.env`（只读）、容器 local 配置（只读）、`data/runtime/container`（读写）；账户/账本数据未触碰。

## 边界与未验

- halted=true 仍在：真实恢复关闭归 Task13-03/13-06 门禁，本次不处理。
- 新代码的真实盘中行为（双源路由、盘中质量、完整买卖闭环、五日观察）未验收，归 Task09-16 与原依赖链。
- 部署修复验收（13-06）仍被 13-04（Antigravity，in_progress）/13-05（被 13-04 阻塞）阻塞。
- 构建期间一次长时间无进展的官方源构建被终止重跑（华为镜像源约 15MB/s），未产生半成品镜像；`trademind:local` 单标签指向新镜像。
