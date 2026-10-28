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

- [A 股广域观察与有界决策设计](../architecture/28-stock-wide-observation-bounded-decision.md) · [股票只读扫描](operations.md#股票广域观察与验证) · [当前边界](current-status.md)

- [中文大屏工作台V2需求与页面规划](../requirements-workbench-v2.md) · [执行任务](../tasks/index.md) · [ARCH-027](../architecture/27-chinese-large-screen-workbench.md)（implementing，代码返修已验证、实际视觉待验）

- [T0 影子需求（当前阶段）](../requirements-t0-shadow.md) · [V1 需求（历史/兼容）](../requirements.md)
- [Roadmap](../roadmap.md)
- [Tasks](../tasks/index.md)
- [Issues](../issues/index.md)
- 架构记录：[ARCH-000 基线](../architecture/00-initial-architecture.md) · [ARCH-001 V1 方案](../architecture/01-ai-trading-system.md) · [ARCH-002 T0 双模型](../architecture/02-t0-etf-shadow.md) · [ARCH-003 运行安全门禁](../architecture/03-t0-runtime-safety.md) · [ARCH-004 行情池轮询](../architecture/04-t0-market-pool.md) · [ARCH-020 免费分钟质量路由](../architecture/20-free-minute-source-routing.md)；其余 ARCH-005～019 见 [docs/architecture/](../architecture) 顺序编号（多市场、快速决策、容器运行、日报、监督、估值隔离、写租约、日历、数据库升级等）。
