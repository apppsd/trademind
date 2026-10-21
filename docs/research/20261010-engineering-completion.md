# 2026-10-10 工程收尾与剩余真实验收

关联 Task03/13/17/18、ARCH-006/009；Codex，main，F:/Project/TradeMind。保留既有工作，没有提交、推送、实盘或新增收费请求。

## 已实施

- Task18：`execution/paper_run.py` 原子脱敏运行报告；runner 新增 `--report-output`、`--max-seconds`，记录配置哈希、上下文来源/特征版本、计划引用、模型/订单/成交差量、预算与账户核对。状态 ready 仅表示入口就绪，`trading_acceptance=not_evaluated`。
- 结束先禁止本进程新预约，再等待模型 worker 至多35秒；未消费动作丢弃，不在退出时下单。仍活跃则 incomplete，不关闭其数据库。策略调用读取超时15秒。
- 容器 helper 给每次 paper 随机名称；外层时限为运行上限+75秒，超时/中断只停止该容器，停止成功才恢复 workbench。强制停止报告计数 unknown，需要账本复核。宿主线程停止为协作式，不承诺 Python 解释器硬停止。
- Task17：`evaluation/fast_archives.py` / `scripts/collect_fast_archives.py` 采集同一真实分钟采样，旧新链独立500元账户/数据库/预算。默认仅行情，显式 `--probe-models` 且免费时段/质量门禁通过才加载模型凭据；同进程探针后前瞻生成档案。回放不联网补历史答案。
- 每次采集固定一个合格标的，保留共同事实哈希、可见原始行情、真实各组账户上下文、动作完成时间/TTL、计划、白名单模型审计及费用语义。部分结果原子保存；采集不覆盖已有文件。时限是协作检查，正在执行的 HTTP 受各 provider 超时限制。
- 对照先检查每个共同上下文的双链档案；空样本、短路缺档或协议版本错误不能变成成功。无标注时不输出伪校准。
- Task03：补401/429/500/超时测试，未知费用保留预约，不重复HTTP。认证正例复用既有官方三题型真实成功证据，不重新消费额度。
- 跨日预算：预约绑定上海日期；替换每日状态前存入 `model-budget:{account}:history:{day}`，迟到结算回写所属日，只读 snapshot 不清账。见 ISSUE-20261010-01。

## 验证与证据

宿主 Python3.12.10；完整回归340项通过，随后新增上下文/计划报告正例，相关8项通过。最终完整回归和容器结果在本报告下方收尾记录更新。已覆盖跨日/午夜结算、停止后的新HTTP阻断、未消费动作丢弃、报告失败脱敏、容器停止失败不恢复双写者、隔离账户及共同事实、空档缺档。

真实非交易时段免费采集：`data/runtime/fast-archives/ec5d499e138e4d1eb82a0f822dc45028.json` 返回 incomplete_archives、contexts0、model_calls0、outside_session_or_run_deadline。同文件网络禁用对照返回 incomplete_archives，结果 `.comparison.json`；不是 Task17/08 真实验收样本。

JEV真实认证/Choice/Score/Noul/usage 已有[连接报告](20261009-runtime-connectivity.md)；费用来源与汇率见[费率报告](20261009-model-pricing.md)。参考估值不是供应商账单；失败未知费用仍 pending。2026-10-09的10次历史调用不得称2026-10-10当日计数。

## 未完成条件

Task16须真实盘中多个时点的新鲜/连续分钟；Task18依赖它，工程齐备但保持 in_progress。Task17须非空真实共同双链档案，Task08须真实时间切分和明确定义结果标注，Task09须真实双模型有界 BUY→SELL→清仓及费用/资金守恒，Task56须随后五个完整交易日。ARCH-006/009仍 implementing；HTTP200和离线测试不代替这些条件。

多市场只读入口已实现，非ETF模拟执行仍按各自任务/用户权限推进；未扩大500元ETF订单引擎范围。

## 文档同步

Task03/13/17/18及索引、Issue12/费用02/跨日01及索引、README、project全部入口与事实页、roadmap、ARCH-006/009同步。需求、交易规则、配置Schema、数据库Schema和依赖未变；Release无发布不适用。Task03只按协议/费用范围关闭，其余真实任务保留。

## 最终运行证据

- 宿主完整回归342 passed/17.64秒；最新镜像容器完整回归342 passed/46.26秒，两者各1个既有Starlette弃用警告；VM入口测试13项通过。最新镜像重新加载到workbench，healthy/HTTP200。
- Windows Docker桥接多次Hyper-V socket超时，原连接未修复（Issue-20261010-02 open）；不重启其他项目。rdctl shell的VM Docker29.5.3可用，新增显式--via-rancher-vm并实测。
- 真实paper闭市报告：data/runtime/container/paper-runs/paper-566c89ac1160491a8ef4b775dcad541b.json；blocked/not_continuous_session，model_calls/orders/fills均0，worker_active=false，new_requests_closed=true，现金/成本权益500，持仓0/开放订单0，2026-10-10当日used0。
- helper本次先停workbench，随机命名paper临时容器，结束后恢复；workbench healthy，宿主HTTP200，仅TradeMind工作台无paper worker。
- 免费check --via-rancher-vm：data/runtime/container/container-startup.json，credentials_loaded=false/model_calls0/orders0，三ETF238/178/238行，最新10月9日15:00，当前闭市stale，正确blocked。不构成盘中质量验收。
- 只读SQLite复核：当前持久预算行仍day2026-10-09、used10、pending0.002097140976，模型审计10行、订单0行；没有新预约触发归档。新日snapshot0是当日视图，未清除昨日费用。14个本轮报告/当前事实/索引文档本地链接检查无缺失；git diff --check通过，无commit/push。Task03/Issue12/费用02已按范围关闭；所有真实任务保留。

