# 本地 Windows 千元双模型运行验收（2026-10-22）

任务：[TASK-20261022-01](../tasks/TASK-20261022-01-本地千元双模型自动模拟交易落地.md) · 架构：[ARCH-026](../architecture/26-local-windows-runtime.md) · 副本：`D:\trademind`（Python 3.12.15/uv）。运行源码为**未提交工作区**（含本轮生命周期修改），不能用 git HEAD `843ab9f` 当作运行代码指纹；版本仅表示基线提交。

> 2026-10-22 更正（[Task22-02](../tasks/TASK-20261022-02-独立复验本地双模型运行收尾.md) 复验指出）：①当日计划并非全部只允许 WAIT，也存在含 BUY 条件的计划，"计划合法 WAIT→不调 JEV"仅对被核验轮次成立（如 14:49 plan-08c55），全天零 JEV 决策的逐轮关联复核由 16-04 执行；②下方费用为含探针的总结算口径；③共享硬上限 5 CNY/日、100 请求/日与分项上限同时生效；④900 秒为计划常规刷新间隔下限，实际间隔受冷却/评估节流影响，不是精确节拍。

## 结论口径

**本地监督、真实行情、DeepSeek 策略及 JEV 连通已验证；JEV 实际决策、完整买卖闭环未验。**

## 运行窗口与进程

- 12:16 supervisor 启动（pid 171456）→ 13:10 因启动方作业对象连带终止（非代码缺陷，已修 `CREATE_BREAKAWAY_FROM_JOB`）→ 13:27 重启（pid 186380）持续至收盘后。
- 进程周期：午休/休市 serve（工作台 8321）↔ 盘中有界 paper（720轮/3600秒上限），单 writer（ARCH-017 租约）。

## 真实证据（全部可从库/报告复核）

| 时间(+08) | 事件 | 证据 |
|---|---|---|
| 13:00/13:05/13:10 | 启动门禁三轮拦截 | `fresh_complete_minute_data_unverified`（开市分钟线不足，逐标的年龄/缺失字段在 preflight 报告，模型调用 0） |
| 13:26:45-47 | flash_probe ok / jev_probe ok（jev-1.13.0 经代理）/ 首份真实 StrategyPlan | model_calls 表 is_stub=0，plan-93b53c86… |
| 13:33-14:57 | 意图循环（~10秒/轮×3标的），计划刷新间隔≥900秒（13:26/13:42/13:57/14:13/14:28/14:44 共 6 份） | intents 81+、model_calls strategy×6、费用累计 ¥0.0644（含探针，provider 估算结算） |
| 13:43 | 513100/518880 报 `minimum lawful lot unaffordable` | 当时价格一手 ≈¥570/¥841 > 200 CNY 单标的上限（价格相关结论，未放宽限额） |
| 14:49:13 | 513050 冷却通过轮完整定性 | plan-08c55… `allowed_actions=['WAIT']`、entry_conditions=[]、trend 判 TRENDING_DOWN 无入场机会；state.allowed_actions=['WAIT']；`jev_called=False`（fast_pipeline.py:89 设计：仅 allowed_actions>1 才调用 JEV） |
| 14:27 | 3600 秒轮转换 | 报告 status=incomplete（有界到期，非错误），15 秒后自动续轮，未 halted |
| 14:57-15:01 | 时段结束排空 | paper 排空→serve 恢复（HTTP 200）→日报 2026-10-22 html/json/md 生成 |

## 费用与预算

- 当日模型费用结算 **¥0.0644**（含两次探针与 6 次 deepseek-flash strategy，provider 估算口径；JEV 决策 0 次故无 JEV 决策费用）。
- 预算计数：策略 6/30、JEV 0/60、探针 2/10（第二轮起复用认证未重复探针）、共享硬上限 5 CNY/日与 100 请求/日未触及、费用上限 100 CNY/日——全部在冻结契约内。

## 未验边界

- JEV 实际决策（当日计划合法仅允许 WAIT；逐轮关联复核由 16-04 按用户口径执行，未证明异常前不缩短冷却/放宽条件）。
- 完整 BUY→SELL→清仓闭环、成交/资金对账、次日自动恢复、五日观察（沿用原 Task）。
- 电脑关机/休眠期间不运行（设计承诺）。

## 脱敏说明

本报告不含 API Key、代理凭据或完整日志原文；`.env` 全程未读取内容。命令入口见 [operations 本地节](../project/operations.md#本地-windows-自动模拟监控2026-10-22-arch-024)。
