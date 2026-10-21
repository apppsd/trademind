---
id: ARCH-023
type: architecture-change
title: 分钟行情后台请求在途容量
status: implemented
created: 2026-10-17
updated: 2026-10-19
related_tasks: [TASK-20261017-13]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

## 2026-10-19 close启动竞态修复，恢复implemented

Task18-01发现的反例（worker登记后、start前并发close抛RuntimeError）已由Task17-13修复：close对`ident is None`的worker跳过join，槽位仍由真实启动/退出持有并如实返回False；close后拒绝新预留，已获准任务按冻结语义完成；start失败路径幂等释放一次。Event/白盒回归3项钉住（未启动close、start中并发close、start失败），路由33项、全量611项通过。下方首次implemented记录与2026-10-18复核记录均保留为历史；真实SDK/容器/盘中效果仍未验。

## 2026-10-18 独立复核回到implementing

Task18-01验证原8次Event容量反例已由active/calls8/8降为1/1，30项路由测试通过；但worker对象赋给request.worker后、实际Thread.start前并发close，会join未启动线程并抛RuntimeError。新增close的启动交接/异常安全契约待原Task17-13补齐；原Owner保留，Issue17-03重新open，不新立重复实现。下方首次implemented记录保留为历史，以frontmatter及本节为准；原资源上限与质量路由修复不撤销。


# ARCH-023 分钟行情后台请求在途容量

## 状态

implemented（2026-10-17，工程口径）。原Event反例、资源生命周期回归和当前事实同步已完成；真实SDK、容器和盘中资源效果仍需独立观察。本记录在既有 ARCH-020 的 caller 有界等待之上补充后台资源所有权，不重写其质量路由历史。

## 背景

ARCH-020 用守护线程隔离不可取消的 SDK 请求，超时后调用方停止等待，但 worker 可能仍在运行。Issue17-03 用受控 Event 证明退避到期后的恢复探针可与旧 worker 重叠；每日请求数与单轮源数不限制当前实际在途数。

## 目标

为整个 router 与每个分钟源设置明确的在途上限，统一约束主源和跨源观察；容量占满时快速给出 busy 诊断并尝试仍有容量的健康备用源。槽位由真实 worker 生命周期持有，日切、超时或迟到结果都不能提前释放。

## 非目标

不强制取消 SDK 线程，不增加请求/模型/费用额度，不改变数据源、质量接受、持续差异拒绝、缓存、资金、订单或部署配置；不声称离线 Event 测试证明真实 SDK 会永久卡死。

## 变更前

`_bounded_fetch` 每次新建线程，超时只向 caller 抛 `TimeoutError`。router 不跟踪 worker，同源恢复探针及观察请求可在旧请求未结束时再次启动。

## 方案

`MinuteRoutingPolicy` 冻结两个进程内上限：全路由默认最多 4 个在途请求、每源默认最多 1 个。主源和观察源在增加请求/观察计数之前，以同一把锁原子申请无队列槽位；容量满立即返回 busy，不排队等待，因此容量等待不会越过整轮预算。

每个槽位绑定一个 worker。worker 在 `finally` 中释放一次；caller 超时不释放。迟到结果只写原调用的局部 box，不能回写缓存、选源、health 或 divergence。router 增加有界 `close`：拒绝新的分钟请求，在调用者给定的真实时间上限内 join 当前 worker，仍有活跃请求时明确返回未完成，不假装强制取消。

## 核心设计决策

- 全路由默认 4、每源默认 1：允许一个卡住主源之外仍有有限容量服务健康备用和其他标的，同时禁止同源 SDK 并发。
- busy 不启动 SDK，不消耗每日请求数、交叉观察次数或观察节奏；busy 候选不占实际请求尝试数，但会进入本轮拒绝诊断。
- 容量申请是非阻塞的，不使用线程池或等待队列；全局满时按同一 busy 规则终止或继续后续 pref。
- 北京日切只重置既有每日计数，绝不清空在途槽位。真实退出后才自动恢复容量，无手工清状态入口。
- 不新增 TOML 字段或改本地配置；上限属于 `MinuteRoutingPolicy` 的公开构造参数，`from_data_config` 使用上述默认值。

## 模块影响

仅 `market/router.py` 的请求生命周期与 `tests/test_minute_source_routing.py`。适配器接口和实现不变。

## 接口影响

`get_minute_bars` 与质量报告形式保持兼容；busy 通过既有 `source_rejections`、错误和 `minute_source_log` 暴露。新增 router 有界关闭接口；未关闭的既有调用方不需要改动。

## 数据流影响

主源/观察 → 原子在途槽位申请 → 既有请求计数与有界等待 → 原质量路由；busy → 零请求的明确诊断 → 有容量的备用或既有拒绝路径。worker 实际结束 → 仅释放槽位。

## 数据所有权影响

router 拥有进程内在途槽位与 worker 列表；适配器仍拥有源调用。槽位不持久化、不进入账户/模型预算，重启不会把已消失的旧线程当作仍活跃。

## 部署 / 运行影响

默认上限随代码生效，不修改 compose、本地配置或生产运行。真实 SDK/容器资源效果需独立观察。

## 代码范围

- `src/trademind/market/router.py`
- `tests/test_minute_source_routing.py`

## 测试范围

原 8 次恢复探针反例、全路由上限、健康备用、观察 busy 与计数、日切、异常释放、迟到结果隔离、有界关闭与最终 join；保留全部既有路由回归并跑相关套件和全量 pytest。

## 实施中的设计变化

无；按上述冻结方案实施，未新增配置字段或适配器改动。

## 最终实施结果

`router.py`新增原子在途槽位和逐源计数，主源/观察均在每日请求或观察计数之前申请；busy快速进入既有诊断和备用路径。`_bounded_fetch`接收已分配槽位，worker `finally`幂等释放，caller超时不再释放；线程启动失败与同步异常路径同样释放。`close(timeout_seconds)`以真实单调时钟限制join并拒绝新分钟SDK请求。公开取数/QualityReport结构不变，未改本地配置。

## 验证

原Event复现修复前8次恢复active=8/calls=8，修复后逐次active=1/calls=1，清理后0。`tests/test_minute_source_routing.py`30 passed（新增5项）；分钟路由及七个依赖套件合计112 passed；全量608 collected / 608 passed / 0 failed。仅有既有StarletteDeprecationWarning。真实SDK、容器资源与盘中质量未验证。

## 当前事实同步

- `docs/project/system-overview.md`：已同步
- `docs/project/code-map.md`：已同步
- `docs/project/flows.md`：已同步
- `docs/project/setup.md`：不适用（无新增 TOML 配置）
- `docs/project/operations.md`：已同步busy诊断、自动恢复和有界关闭
- `docs/project/current-status.md`：已同步工程证据与真实未验边界

## 关联内容

- Task：[TASK-20261017-13](../tasks/TASK-20261017-13-限制分钟行情超时后台请求并发.md)
- Issue：[ISSUE-20261017-03](../issues/ISSUE-20261017-03-分钟行情超时后台请求缺少并发边界.md)
- Research：不适用（证据保留在 Issue/Task）
- Previous Architecture：[ARCH-020](20-free-minute-source-routing.md)
- Next Architecture：暂无

2026-10-19独立复核：[Task19-05](../tasks/TASK-20261019-05-复验路由关闭竞态修复.md)已验证原启动竞态、关闭拒新、真实退出与8次容量恢复，33项路由测试通过。保留implemented工程状态，真实SDK/部署/盘中未验。
