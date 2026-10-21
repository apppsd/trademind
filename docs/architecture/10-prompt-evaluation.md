---
id: ARCH-010
type: architecture-change
title: 提示评测与显式版本管理
status: implementing
created: 2026-10-11
updated: 2026-10-11
related_tasks: [TASK-20261011-03, TASK-20261011-04, TASK-20261011-05, TASK-20261011-06, TASK-20261011-07, TASK-20261011-08, TASK-20261011-09]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-010 提示评测与显式版本管理

## 状态

2026-10-11完成复核Checkpoint：保持implementing；原提示Task08的晋级/回滚未形成工程闭环，见[Issue-20261011-01](../issues/ISSUE-20261011-01.md)。待统一评测报告Schema、真实门禁和实际版本切换接口，不改变既有生产Guard/OMS。旧完成声明以此为修正。
implementing；04～06离线工程完成，07真实审计及08晋级待验，不替代ARCH-006/009。

## 背景
strategy-2/fast-2及生产校验已实施，现有prompt_experiment偏旧链意图对比，fast_archives/fast_comparison具备共同输入工程。真实档案与校准仍待原Task17/08；新增固定语义案例、分项提示评测及版本晋级过程需要解释其读写/收费边界。

## 目标与非目标
目标：[提示评测需求](../requirements-prompt-evaluation.md)F1～F5，F6条件后置。不改交易三方职责、单写账本、模型型号、资金/风控或旧链；不引入新的在线调度器/消息队列，不默认装框架或让模型自主上线。

## 变更前
生产Schema校验及提示契约测试已存在；提示常量决定版本/缓存。离线测试不能衡量未调用的真实供应商语义；已有档案可输出对照，但缺专用固定行为标签和明确晋级报告。是否确有字段差距由07核查后实施最小增量。

## 方案与核心决策
新增离线案例/响应评测器，复用生产Schema和原始档案。标签独立于模型自评，检查模型建议和最终风控结果两层；保留合理BUY正例与合法退出，不能靠一律WAIT获得成功。

真实响应由既有前瞻采集器取得，评测器不请求模型、不写账本；缺档不补模型输出。提示变更保留基线及输入指纹，只有显式选择版本，真实晋级依据原Task08的时间留出和冻结指标。

## 模块与接口影响
fast_prompts：配对示例/版本；拟新增evaluation/prompt_eval与离线脚本：只读响应/案例、写报告；fast_archives：最小白名单审计字段；版本接线复用现有机制，Task08认领时决定具体注册方式。文件所有权见各Task，不并发改原17/08同一文件。

离线报告计划有Schema版本、数据集/提示/模型/配置指纹、输入来源、统计分子分母、状态与失败案例引用。未知版本/损坏输入拒绝或标incomplete，旧档案缺字段不升级为完整结果。CLI具体参数/退出码在06认领时冻结并写操作文档。

## 数据流影响
合成案例/标签＋已保存响应 → Schema/行为断言 → 分项报告 → 人工失败分析 → 候选提示 → 同输入比较。
真实行情 → 原Task17隔离双链/审计 → 非空真实档案 → 原Task08时间切分 → 新提示晋级报告 → 显式版本选择/核验。
没有新增评测报告到订单引擎的自动写入路径。

## 数据所有权影响
案例归评测模块维护，有版本；真实响应归原采集模块导出，保留来源/hash。生产账户、OMS、预算仍由既有主线程/单写者持有；评测器只读快照。新报告只白名单脱敏字段，不持久化密钥/认证头或完整配置。保留期和真实输出片段长度在07认领时据实际存储确定，须明确而非无限积累。

## 部署 / 运行影响
先复用Python/pytest，不升级依赖。新增CLI默认离线，不触发收费调用；真实采集沿用原预算/同进程能力/盘中质量门禁。提示改动须确认宿主与容器实际版本；部署操作另按授权执行。回滚版本不清账，旧缓存失效但保留冷却。无后台自动优化服务。

## 代码与测试范围
04～08按各Task记录确切文件。覆盖Schema/数据集完整性、时点隔离、网络零调用、无写账本、失败计数与未知费用、审计脱敏、A/B指纹一致、版本/缓存/回滚恢复。供应商响应质量及真实市场覆盖另验，无法观察的类别保留未覆盖。

## 实施中的设计变化
04～06实现Checkpoint：strategy-3配对示例、固定标签及只读评测CLI。输入限定为保存的九字段草稿或归一化四题answers；程序观测由输入提供，不在评测器重放OMS。每例每批一条响应，多次尝试单独评测，不当费用总账。A/B同输入/模型/配置；报告单文件原子写入，不是跨文件事务。无新依赖或账本写权。

## 最终实施结果
04～06工程已实现；07失败审计扩展、白名单脱敏与08显式版本注册、晋级门禁、安全回滚工程均已实现，新增自动化测试通过；09提示自动优化可行性评估完成，得出明确不采用结论。真实contexts0，07/08真实表现待盘中数据支持，保持implementing。

## 验证与当前事实同步
工程证据见[提示评测验证](../research/20261011-prompt-eval-verification.md)与[自动优化评估](../research/20261011-prompt-optimizer-feasibility.md)；README、current-status、system-overview、code-map、flows、operations已同步。setup无依赖/部署变化不适用。真实模型质量及容器strategy-3未验。
实施后逐项同步system-overview、code-map、flows、setup（依赖/部署变化时）、operations、current-status；规划阶段这些事实文档不新增未实现模块。project/index与roadmap仅链接规划。

## 关联内容
[任务队列](../tasks/index.md#提示评测增强队列2026-10-11) · [需求](../requirements-prompt-evaluation.md) · [调研](../research/20261011-prompt-engineering-practices.md) · [ARCH-006](06-jev-fast-decision.md) · [ARCH-009](09-paper-readiness-and-archives.md)
