---
id: ARCH-008
type: architecture-change
title: 显式新浪ETF分钟行情源
status: implemented
created: 2026-10-09
updated: 2026-10-09
related_tasks: [TASK-20261009-13]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 显式新浪ETF分钟行情源

## 背景、目标与变更前
东财分钟连接受阻，实测新浪公开证券行情端点能返回三只已核验ETF的OHLC/成交量/真实成交额。原DatasetRouter只有fixture/akshare/baostock/tushare。新增显式sina_etf来源，仅ETF分钟；不调用股票SDK回退，不混合来源。

## 方案与核心设计
只允许get_profile已登记security_type=etf；一分周期、8秒请求、字段/有限非负数字/OHLC检查、成交额除成交份额落在价格范围附近，否则拒绝疑似单位变更。available_at=fetched_at，旧行不伪造历史可见性；当前分钟不供执行。原时段/质量/Guard门禁继续生效。

## 非目标与接口、数据流、所有权
不接未知证券、不实盘、不承诺源完整实时。数据流sina_etf→DatasetRouter→既有上下文/执行；审计记录独立provider名称，账本与策略所有权不变，无Schema变化。日线/新闻/基本面不由新adapter提供。

## 模块与代码范围
market/adapters/sina_etf.py、adapters/__init__.py、market/router.py；container示例和本地快速配置显式选择新源。JEV协议/OMS/策略不改。

## 部署与实施变化
曾计划持久化容器已有代理配置，但写入命令被自动审批blocked by policy拒绝，未执行；本次不落地网络配置。代理仅诊断命令临时使用，不改系统环境。

## 测试与最终结果
已实施：Windows292测试通过，容器新12测试通过，三ETF两日476/356/476行、末条15:00；有限paper正确NotFresh，未新增模型或订单。夜间结果不替代盘中新鲜度或完整性验收。

## 当前事实同步
system-overview/code-map/flows/setup及运行文档已同步；Task13记录操作与Issue根因，实际证据[连接报告](../research/20261009-runtime-connectivity.md)。implemented仅指新源工程，不代表盘中质量与完整交易验收。
