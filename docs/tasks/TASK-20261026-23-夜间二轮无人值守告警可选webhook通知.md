---
id: TASK-20261026-23
type: task
title: 夜间二轮无人值守告警：可选webhook通知（halted/日历不足）
status: completed
priority: high
created: 2026-10-26
updated: 2026-10-26
owner: Claude Code
depends_on: [TASK-20261026-20]
related_issues: []
related_architecture: [ARCH-026]
---

# TASK-20261026-23 夜间二轮无人值守告警：可选webhook通知

## 任务信息

- 负责人：Claude Code；状态：completed；分支 / Worktree：main / F:\Project\TradeMind。
- 借鉴来源：开源社区无人值守 bot 的状态突变告警实践，见[借鉴扫描](../research/20261026-oss-borrowing-scan.md)。

## 实施

- 新增 `src/trademind/observability/notify.py`：`notify_webhook(text)`——未配置 `TRADEMIND_NOTIFY_WEBHOOK` 时为静默 no-op；payload 为钉钉自定义机器人 text 格式（通用 JSON 接收端亦可用）；**任何失败都吞掉并返回 False，绝不影响交易**。
- `execution/auto_paper.py`：监督器 tick 状态持久化点加**转换检测**——halted False→True、日历覆盖 False 各告警一次（条件清除后重新武装），零重构 6 个 halt 调用点；内容仅含账户别名/健康状态/中文原因/时间（白名单字段，无凭据无端点）。
- 测试 `tests/test_notify_webhook.py` 5 项：未配置 no-op、payload 格式、env URL、失败不抛、**真实 tick 驱动的一次性/再武装转换语义**（不镜像实现）。

## 验证

- 定向 5 项通过；全量 pytest exit 0（2026-10-26 实测，见 Task26-22 部署记录）。
- **默认关闭**：不设置环境变量即完全无行为，无凭据写入。启用方式：150 上为监督器 shell 设置 `TRADEMIND_NOTIFY_WEBHOOK=<钉钉机器人地址>` 后重启。

## 未验证/边界

真实钉钉机器人投递未验（无凭据，不代配置）；通知延迟受 tick 5 秒节奏约束；监控器自身死亡由看门狗覆盖（`scripts/watchdog_heartbeat.py`）而非本机制。
