---
id: ARCH-018
type: architecture-change
title: 交易日历数据所有权与更新接口
status: implemented
created: 2026-10-14
updated: 2026-10-14
related_tasks: [TASK-20261013-15]
related_issues: [ISSUE-20261013-07]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 交易日历数据所有权与更新接口

## 背景、目标与非目标

实施前，日历CSV被当作无限事实：范围外的日期查询会静默按边界日期截断，覆盖何时到期、数据来自谁无从得知，运营者没有任何受控的更新通道。目标：日历带显式覆盖边界，边界外成员/导航显式失败而非猜测；提供离线、只读合并的官方日历更新接口与出处旁证；自动模拟在覆盖不足时停模拟与付费请求等待人工。非目标：程序永不联网抓日历、永不编造交易日填补空档；不改变交易时段判断（09:30-11:30/13:00-14:57）与退避策略。

## 方案与边界

`src/trademind/market/calendar.py`：

- `TradingCalendar`带`coverage_start`/`coverage_end`（首末交易日行）；`covers()`为唯一范围判断。
- 覆盖外成员/导航抛`CalendarCoverageError`：`is_trading_day`/`prev`/`next`/`add_trading_days`等一律`_require_covered`，导航永不截断到边界日期；范围内非交易日是事实（休市），只有范围外才是"未知"。
- `coverage_warnings()`：覆盖尾距今日≤20个交易日提示更新；缺`.meta.json`出处（source/checked_at）提示出处未验证。

`src/trademind/market/calendar_update.py` + `scripts/update_calendar.py`（更新接口，纯离线）：

- 运营者下载官方交易所日历，转为fixture CSV形态（`date`表头+ISO日期行）后运行合并命令；`parse_calendar_csv`严格校验，不可解析即拒绝。
- `merge_official_calendar`：与现有行并集（历史保留）、排序去重、临时文件原子替换；默认拒绝缩小覆盖（新数据结尾早于现有需`--allow-shrink`显式确认）；写`.meta.json`旁证（source/version/checked_at/覆盖范围/交易日数）。`TradingCalendar.load()`读取旁证作为出处。

**（2026-10-14复核更正，原文保留：上条"并集"已被段级替换语义取代——并集无法删除官方撤销的错误交易日（[ISSUE-20261013-07](../issues/ISSUE-20261013-07-交易日历缺少覆盖范围与越界保护.md)）。现行语义：官方输入是其自身区间`[首,尾]`的完整权威陈述，与该区间重叠的已确认段整段撤回（官方未重列的日期随之失效），区间外段保留历史；纯后延文件仍是扩展。覆盖建模为**已确认段**：相邻段间隙>14天（`MAX_CONFIRMED_GAP_DAYS`）记为未确认空档，不是休市——`covers()`只认段内，导航拒绝跨越空档，`coverage_warnings()`逐空档提示。`.meta.json`新增`replaced_range/replaced_days/carried_history_days/csv_sha256`，`load()`比对csv_sha256检出半应用更新（sidecar stale）。详见[TASK-20261013-15](../tasks/TASK-20261013-15-完善交易日历覆盖与更新门禁.md)"修复实施"。）**

`src/trademind/execution/auto_paper.py`：

- 每tick`calendar_covered`检查；未覆盖或临近结尾时健康状态显示"日历覆盖不足"，停止模拟与付费请求，等待人工更新；不会用截断日期继续跑。

## 部署与安全

改动`market/calendar.py`、新增`market/calendar_update.py`与`scripts/update_calendar.py`；`auto_paper.py`接覆盖门禁；`runner.py`暴露状态；`scripts/generate_fixtures.py`生成带边界的fixture日历。当前`data/fixtures/calendar_xshg.csv`覆盖至2026-12-31（下一年须由运营者按上述接口更新，当前无2027官方数据时范围外拒绝是设计行为）。无网络访问、无凭据。

## 实施范围与验证

2026-10-14宿主实测：`tests/test_market.py`+`tests/test_fast_startup.py`共27项通过（覆盖边界异常、导航越界拒绝、缩覆盖拒绝/允许、旁证解析、覆盖警告、update CLI端到端等）；`test_auto_paper.py`覆盖不足停模拟与付费请求用例包含在Task 14相关43项运行中。全量回归481项通过。真实2027年官方日历合并、跨年自动停机与恢复未验（官方数据尚未发布），由运营者实际更新时检验。

## 最终结果

代码边界已实施并有离线测试证据；真实跨年场景与官方数据源实际格式适配未验。日历数据所有权自此明确：`data/fixtures/calendar_xshg.csv`+.meta.json旁证为唯一运行日历，更新只能经合并接口，程序侧只读。
