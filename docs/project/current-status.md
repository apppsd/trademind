# 当前状态（唯一当前事实入口）

更新：2026-10-29。本文件只写当前状态——目标、能力、运行版本、限制、下一步入口。**历史过程与逐日记录不在本文件**：见 git 历史（本文件 2026-10-28 及之前的按日期版本）、[任务索引](../tasks/index.md)、[问题索引](../issues/index.md)、docs/research/。当前事实与 [system-overview](system-overview.md)（逻辑架构）、[code-map](code-map.md)（文件入口）、[flows](flows.md)（执行顺序）、[setup](setup.md)（环境）、[operations](operations.md)（操作）按职责分工，互不复制。

## 当前运行（150 主机，本机只开发不运行）

- 代码版本：150 = GitHub = 本地（见 [任务索引](../tasks/index.md) 最新部署行）。
- 监督器：`scripts/run_local.py`，账户 `paper-t0-local-1k`，独立库 `data/runtime/local-1k/trademind.db`，halted=false，脱终端存活。
- **影子盘资金：5000 元档**（2026-10-29 生效，[Task28-01](../tasks/TASK-20261028-01-影子盘资金上调至5000.md)）——初始 5000 / 单证券 1000 / 总持仓 1500 / 最低现金 500（原冻结表 20%/30%/10% 比例语义不变）。账本 deposit 4000 留痕，重放对账守恒。**版本化新 run：五日观察口径自 2026-10-29 重新起算**。
- 模型费用红线：5 CNY/日、100 请求/日（资金调整不改变调用预算）。

## ETF 影子实验（当前主链，fast profile）

- 运行面：开盘自动预检（双模型真实探针）→ 盘中 paper → 收盘排空与日报；策略 900 秒刷新、300 秒全局评估冷却、预约制预算、Guard/OMS 硬门禁。
- 三只 ETF（513050/513100/518880）**全部资金可达**（5000 档解除原 200 上限下 513100/518880 数学不可达，见 [Issue12-03](../issues/ISSUE-20261012-03-小资金最小申报单位不可达.md)）。
- JEV 实际决策仍为 0（截至 2026-10-28 观察）：主因是策略入场三条件 AND（close / ret_5m / volume_ratio>1.0）叠加评估冷却，无满足窗口——**策略保守性，非系统故障**。分钟线缺失阻断（2026-10-27 曾全天发生）10-28 未复现；根因定位仍开放于 [Issue26-27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)（归 Task09-16 域），真实买卖闭环验收归 Task27-06。
- 历史成交与等待统计、逐日核查证据：见对应 Task 与 research，不在本文件维护。

## 股票广域观察扩展（[ARCH-028](../architecture/28-stock-wide-observation-bounded-decision.md) implementing）

- **离线已交付**（Task27-02/03/05，completed 仅指离线工程）：观察池（4403 只候选，程序化剔除含逐标的淘汰原因）、新浪股票分钟源验证（单位=股；收盘 60 只 0 失败；盘中延迟 ≤1 分钟时点样本）、可交易性档案（affordable ≈17%、往返费用占比中位 2.87%）、`StockFastPipeline`（沪深主板+创业板，T+1；600000.SH 与 300750.SZ 全链 BUY Guard ALLOW 集成）、确定性选择器（持仓优先，≤1 候选/轮）、`paper-stock-1` 独立库幂等建账工具、`t0.stock_chain` 开关（默认关）。
- **运行接线未完成**：选择器生产调用点、候选/选择结果落盘、持仓并入选择器输入、股票分钟源运行路由、跨日退出（期限强平遇 T+1 的顺延策略）、两账户预算合计约束。
- **未验收**：完整交易时段连续质量（Task27-04 余项）、真实盘中决策（Task27-06）、沪市交易规则官方原文 source_url。
- **当前未启用**：`stock_chain=false`、`paper-stock-1` 未建账——启用为显式步骤，未经用户指示不执行。

## 中文大屏工作台 V2

代码返修完成（[Task26-16](../tasks/TASK-20261026-16-修复工作台V2独立复核缺陷.md)）；实际页面/交互/回退验收未完（26-04、26-08~12、26-14 in_progress，Owner Codex；[ARCH-027](../architecture/27-chinese-large-screen-workbench.md) implementing）。

## 机制与兼容（细节见对应文档，本文件不复制）

- V1 股票 paper/replay 兼容保留（ARCH-001 implementing、TASK-23 前瞻观察）；多市场扩展 ARCH-011 implemented；旧默认链（flash 候选→JEV 审核→flash 汇总）保留对照。
- 安全红线（永久）：不接实盘、不做空、不加杠杆、不静默换型号/stub/单模型开仓、禁止未来数据、账本一致性与 Guard/OMS 授权不变、未经用户口令不部署 150、候选增多不等于收益改善。
- 文档治理（2026-10-29 审阅，[Task28-03](../tasks/TASK-20261029-01-文档治理改造批次.md)）：夜间分工适用范围已修订、current-status/索引去重已完成、校验器已补斜杠状态识别与索引重复行检测；实验参数与永久规则分离、任务模板分级、索引归档分离、跨文档去重为后续批次。

## 下一步入口

- 活跃任务见 [任务索引](../tasks/index.md)：Task27-04（收口余项）→ Task27-06（盘中验收与运行接线验收）。
- 阻断：[Issue26-27-02](../issues/ISSUE-20261027-02-快速链JEV决策全天未发生全景就绪门禁与分钟线缺失.md)。
- [任务索引](../tasks/index.md) · [问题索引](../issues/index.md) · [逻辑架构](system-overview.md) · [Roadmap](../roadmap.md)
