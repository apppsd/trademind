# Task18 启动门禁工程验证（2026-10-09）

Task18 in_progress：离线安全工程已实施；依赖Task16真实盘中质量仍未通过。无真实双模型新链交易验收、档案或五日观察结论。

## 根因与修复

runner原先在行情预热前调用付费双探针；单个provider只检查自己的一次份额，可能先花Flash再发现JEV无份额。新入口先做免费费率/时段/预算/数据检查，再显式双探针。缺价、非有限价、收盘、过期/缺窗口、双探针请求数/费用不足均不发送探针。

DailyModelBudget.check_probe_pair只读，不清账或提高额度；按两provider实际UTF8输入+1024边界和输出预留检查两次总请求、probe类别和总成本，真实provider发送前仍分别持久预留。依赖单账户唯一writer，检查期间不启动异步worker；不承诺跨多个违规writer的原子二请求锁。Flash启动探针只尝试一次，结束后恢复策略调用的修复次数；失败保留未知usage预留。同进程两能力标记重新校验，历史HTTP200不可复用。

ContextBuilder按fetch后时刻检查最新已闭合分钟、180秒上限和全部滚动字段；至少一证券通过才允许探针，逐证券拒绝仍留报告；后续每次Pipeline先允许已有确定性风险退出，再以WAIT_DATA_QUALITY阻断缺特征的模型worker。盘中新鲜度不由单次免费预检永久授予，后续Guard/OMS原门禁继续生效。

## 实际文件

runner.py、models/daily_budget.py、models/jev.py、pipeline.py、scripts/check_fast_startup.py、tests/test_fast_startup.py。配置/compose/.env/模型型号/资金上限/依赖未改。

## 验证

- 新增13个反例：关闭时段先拒绝，None/0/NaN费率，过期/分钟断点/连接失败，probe/总请求/费用只够一次，免费预检不授予capability，成功双探针单次与设置恢复，缺指标阻断worker且保留程序止损。使用隔离DB/合成行情/Mock HTTP，不冒充真实盘中证据。
- `.venv/Scripts/python.exe -m pytest tests/test_fast_startup.py -o addopts='' -q`：13 passed。
- `.venv/Scripts/python.exe -m pytest -o addopts='' -q`：321 passed，31.31秒；1条既有Starlette/httpx弃用提示。
- Docker镜像重建成功；真实夜间拒绝与服务恢复通过，完整容器测试结果见下。

## 报告与剩余限制

启动拒绝输出status=blocked，退出2，preflight含原因/逐证券数据/特征版本及当前运行model_calls0/orders0；有异步结果未消费保持incomplete。新profile已验证双方且无未消费结果输出ready，trading_acceptance=not_evaluated，不能当BUY→SELL完整验收。t0-fast-check仅免费配置/时段检查，合格时也注明数据未验；check_fast_startup最终blocked包含未探测模型原因。

Task18保持in_progress：真实盘中启动、跨关闭边界/中断时active请求收尾、完整run/配置版本/成交审计报告和账户恢复仍需补齐。Task16、17/08/09/56原门禁保持。当前没有新增收费请求。

## 真实容器安全拒绝（23:12左右）

`.venv/Scripts/python.exe scripts/run_container.py paper --steps 1 --probe-models`：应用输出blocked，blockers=[not_continuous_session]，data=[]，当前运行model_calls=0/orders=0；应用退出2，Compose运行包装退出1（非零表示拒绝，不作为异常成功）。在检查行情/收费HTTP/ensure_account前终止；wrapper停工作台后finally重建恢复最新镜像。

只读SQLite复核：model_calls表仍10行，orders表0行；共享budget used/probe10，settled0.000783564924/pending0.002097140976/total0.002880705900 CNY，与Task15/16既有证据一致。未扩额度、清账或新增付费调用。费用仍是配置估值，不是账单。

workbench healthy且页面HTTP200；compose仅workbench服务，paper临时容器已退出。容器完整测试不挂载真实.env或运行账本；无实盘、提交推送或凭据输出。

容器完整回归：`docker run --rm --entrypoint python trademind:local -m pytest -o addopts='' -q`，321 passed，44.90秒；与宿主相同1条弃用提示。测试容器已退出，未挂载真实凭据/账户。
