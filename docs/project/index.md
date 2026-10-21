2026-10-19最新独立复验：[Task19-11](../tasks/TASK-20261019-11-复验收盘候选与成熟修订修复.md) completed / Codex。Task19-02收盘候选/发布微秒边界、Task19-10日线修订early→late→early原反例全部通过；25项相关测试独立通过，保留两Task completed / Claude Code、Issue19-01/02 resolved（工程）。下方退回/重开为修复前历史。Task19-07已统一ID并新增三条后续触发文字记录，平台运行尚未由本聊天独立确认，保持in_progress/Issue19-03 open。部署/真实盘中/自动反馈/完整交易/五日仍未验。

2026-10-19最新独立探查：[Task19-08](../tasks/TASK-20261019-08-复验候选成熟门禁与执行端登记.md) completed / Codex；[新报告](../research/20261019-outcome-candidate-recheck.md)。33项相关回归通过，但Task19-02收盘成熟候选复验未通过：09:30提前入选、15:30发布后反而为空；保持in_progress/Claude Code，Issue19-01重新open。直接成熟与终点收益计算仍正确。Task19-07已认领但调度ID不一致、后续触发未验，保持in_progress/Claude Code、Issue19-03 open。审查端按用户最新要求每小时一次，旧15分钟为历史。分钟特征四组控制通过，无新缺陷；部署/真实自动链/交易五日仍未验。下方旧“resolved/已设置”断言以本条限定。

# 项目状态索引

当前事实唯一入口：[当前状态](current-status.md)（顶部为最新运行事实与未验清单）。最新实施入口：[任务索引](../tasks/index.md)；开放问题：[问题索引](../issues/index.md)。历史运行证据统一在 docs/research/ 报告与各 Task/Issue 维护，本页只做导航，不重复状态流水。

只读文档一致性校验（本地链接/重复ID/索引状态一致性/depends_on检查）：`.venv\Scripts\python scripts\check_docs.py`。

## 当前事实

- [当前状态](current-status.md)
- [系统概览](system-overview.md)
- [代码地图](code-map.md)
- [环境与依赖](setup.md)
- [运行操作](operations.md)
- [运行流程](flows.md)

## 工作入口

- [T0 影子需求（当前阶段）](../requirements-t0-shadow.md) · [V1 需求（历史/兼容）](../requirements.md)
- [Roadmap](../roadmap.md)
- [Tasks](../tasks/index.md)
- [Issues](../issues/index.md)
- 架构记录：[ARCH-000 基线](../architecture/00-initial-architecture.md) · [ARCH-001 V1 方案](../architecture/01-ai-trading-system.md) · [ARCH-002 T0 双模型](../architecture/02-t0-etf-shadow.md) · [ARCH-003 运行安全门禁](../architecture/03-t0-runtime-safety.md) · [ARCH-004 行情池轮询](../architecture/04-t0-market-pool.md) · [ARCH-020 免费分钟质量路由](../architecture/20-free-minute-source-routing.md)；其余 ARCH-005～019 见 [docs/architecture/](../architecture) 顺序编号（多市场、快速决策、容器运行、日报、监督、估值隔离、写租约、日历、数据库升级等）。
