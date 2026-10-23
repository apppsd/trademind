# 本地千元双模型当日收尾独立复验（2026-10-22）

关联：[Task22-02](../tasks/TASK-20261022-02-独立复验本地双模型运行收尾.md)、[原实施Task22-01](../tasks/TASK-20261022-01-本地千元双模型自动模拟交易落地.md)。复核者Codex，main / D:\trademind，工作区有原Owner未提交改动。检查截至上海15:26前后；未停止/重启当前服务，未调用模型或修改账户。

## 结论

真实运行与合法WAIT的核心结论有证据支持，但本地停止流程存在两个独立复现的缺陷，不能认可A项6/6及全部生命周期安全验收。原Task保持in_progress正确；修复与独立复验完成前，ARCH-026的implemented需限定或退回implementing，由原Owner同步。

## 独立核对通过的事实

- 只读SQLite `data/runtime/local-1k/trademind.db`：8条model_calls，均ok且is_stub=0，分别flash_probe 1、jev_probe 1、strategy 6；8次attempts。JEV实际决策0、订单0、成交0、reservations 0。
- 账户paper-t0-local-1k现金1000、冻结0、version2；两个事件为deposit 1000与rollover，未见重复入金。报告写version1是早期采样，非当前值。
- 最终预算used8、strategy6、probe2、JEV0、pending费用0，轮转换前后累计衔接。实际日费用上限5 CNY，请求上限100；未放宽限额。
- 最后paper报告06:57:00.944868 UTC完成，status=incomplete，worker_active=false、unconsumed_result=false、new_requests_closed=true、drain_terminal_state=no_pending_result。上一轮06:26:45完成，下一轮06:27:06启动；可证明轮转换和收盘排空。
- 15:25状态：supervisor pid186380存活、fresh heartbeat、phase=serve、halted=false、session_open=false；日报html/json/md存在。收尾独立HTTP GET工作台返回200，pid186380仍存在。次日自动恢复尚未发生，不能先勾选。

## WAIT结论：支持，但原说明过度概括

- 6份计划中5份只允许WAIT；**14:13计划plan-e1ada89404b042d8bd55允许BUY/WAIT**，入场同时要求close>=0.9433、ret_5m>0、volume_ratio>=1.5。
- 该计划关联的3次非冷却意图（14:13:05、14:18:08、14:23:17）均为no actionable strategy conditions；对应Context特征分别为close/ret_5m/volume_ratio：0.933/-0.001071/3.2618、0.936/0.003215/1.1463、0.935/-0.001068/0.7105。均未满足入场条件，程序合法WAIT，不支持异常截断假设。
- 全天525条意图中，513050：145条global cooldown、17条no actionable strategy conditions、14条new-entry cutoff；另外两个标的322条最小手数不可达、27条尾盘禁止开仓。未发现invalid fast-decision state等截断原因。
- fast_pipeline的jev_called标记由allowed_actions数量产生，**实际跳过HTTP调用的分支在FastDecider.decide**。不能把标记赋值本身称为调用分支。
- 因而可撤回“饥饿已证实”，但应把“全天计划只允许WAIT”改为“当日实际评估均未形成可执行入场条件”。单个14:49样本不能替代逐轮核验。

## 新发现：停止安全门禁不通过（高优先级）

[ISSUE-20261022-01](../issues/ISSUE-20261022-01-本地停止流程误启paper且强停状态未持久化.md)记录两个原反例及关闭条件：

1. serve活跃时stop会走start(None)，实际构造新的t0-paper命令，且kind为None；隔离FakeChild复现，无真实启动。
2. finally强停paper后内存halted=true，持久状态仍false；重启可能丢失未知结果暂停。

复现脚本：[20261022-local-stop-repro.py](20261022-local-stop-repro.py)。当前成功收盘报告仍有效，不能推断这些未覆盖分支安全。

## 文档与证据口径需原Owner更正

- 原报告“6次策略费用0.0644”不精确：策略总计0.064166 CNY，flash_probe 0.000184，jev_probe 0.000081442368，总计0.064431442368；这些是operator_price_usage/reference token价格估值，不是独立供应商账单核验。
- “费用0.0644/100”混淆单位：5 CNY/日才是费用上限，100为HTTP请求次数上限。
- “900秒精确到秒”过强：6次策略创建间隔约924、924、925、939、925秒；可说900秒下限加轮询/执行延迟。
- 原Task“git diff --check未授权commit所以未跑”理由不成立，该命令只读；本轮已执行并通过。
- status.code_version仅为查询时git HEAD=843ab9f，没有dirty源码指纹，也不是运行进程启动时版本证据。当前工作区有未提交实现，A项“代码版本可核验”证据仍不足。
- 正常stop不遗留、强停持久halt、真实双开、跨日及持仓风险退出不能合并为已全部验证；应按上述缺口校正勾选，保留JEV决策/交易闭环未验。

## 本轮测试与限制

- 生命周期四套件：52 passed，2.40s。
- 全量：652 passed、1 failed，57.79s；唯一失败为test_check_docs.py::test_real_repo_has_zero_errors，两处旧F盘file链接不存在。
- check_docs：新增复验Task/Issue/报告后再跑仍2错误、36提醒，均为已知历史链接/索引/Owner提醒，无新增。
- git diff --check通过。独立隔离停止反例两项均复现，说明上述通过测试未覆盖缺陷。
- 未验证：真实stop/重启新补丁、强停生产实例、启动作业关闭后的breakaway降级、次日恢复、JEV决策、真实成交及五日观察。未更改原Owner代码、运行状态、阈值或预算。
