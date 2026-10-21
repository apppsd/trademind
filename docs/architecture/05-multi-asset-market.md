---
id: ARCH-005
type: architecture-change
title: 多市场行情发现与购买条件筛选
status: implemented
created: 2026-10-08
updated: 2026-10-08
related_tasks: [TASK-20261008-05]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-005 多市场行情发现与购买条件筛选

## 状态与背景

implemented（观察与筛选边界）。ARCH-004 只覆盖3只核验ETF。用户现在要求接入更多可购买市场。

## 目标与非目标

全量发现ETF、LOF、可转债、港股、境内期货实际合约；统一观察报价并筛选账户/资金/规则资格。不扩张原人民币ETF执行规则到其他市场，不实盘、不调用收费模型。

## 变更前

t0-data 逐只抓3只分钟行情；t0-paper 仅核验ETF，人民币500虚拟资金。

## 方案与核心设计决策

独立 MultiAssetMarket → AKShare批量快照 → 标准化 AssetQuote → 规则覆盖与 AccountAccess → MarketReport(JSON)。规则覆盖文件与账户配置不含凭据。快照缺原始时点则 freshness unknown；不进入原Paper/Guard。可观察≠账户可以买≠本系统已能模拟执行。

## 模块与接口影响

新增 market/multi_asset.py；配置增加 market_access；CLI新增 market-data。无OMS/数据库Schema改变。所有市场每轮批量抓取，期货按官方SDK品种映射展开，错误独立、有界扫描并报告未扫描项。

## 数据流与所有权

行情是当前抓取的观察数据，不是回放PIT。报告只写来源与标准化字段，不保存HTTP响应、代理地址、账号或Key；观察报告由新模块所有。现有账户/订单/模型预算仍归原组件。

## 部署与代码范围

同一CLI进程，复用可选AKShare依赖；无新服务。代码与测试范围见Task-05。

## 测试范围

五市场路由、代码标准化、交易单位/币种/保证金、权限未知、未知T0、时间缺失、坏数据/异常隔离、超时部分扫描、凭据隔离及旧回归。

## 实施中的设计变化

新市场先接观察与条件筛选；现有引擎只能ETF，完整跨市场模拟交易必须独立适配，避免用ETF规则假装已经支持。

## 最终结果与验证

五市场观察/分钟入口、规则与账户筛选、报告与页面已落地；旧profile拒绝其他市场误套A股规则。227项测试通过，1051条真实快照。新市场执行尚未适配，见Issue-05和Task-06～09；真实网络/分钟质量不完整，详见research/20261008-multi-asset-verification.md。

## 当前事实同步

system-overview/code-map/flows/setup、README/status/operations/roadmap及索引已同步。

## 关联内容

Task-05；ARCH-004；ISSUE-20261008-04。新市场执行扩展后续独立架构记录。
