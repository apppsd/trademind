# 真实有界模拟循环启动

2026-10-12，TASK-20261012-03。用户明确授权启动模拟交易；使用原三ETF/500paper、Flash/JEV与既有预算，不改源码/配置/Key，不实盘。

## 启动过程与证据
09:45首次scripts/run_container.py paper --steps3 --max-seconds240 --probe-models --via-rancher-vm被免费行情门禁拒绝：两标的14根分钟不足、513100无数据；调用/订单0，工作台恢复。
09:47:48～09:49:02第二次--steps12 --max-seconds180：免费门禁通过（513050/518880有效，513100缺行情）。同进程Flash探针795ms、JEV探针659ms成功，真实策略请求完成。12轮/36标的检查，7个WAIT、12个NotFresh；无订单/成交，现金500。全池报告failed不是交易成功，worker退出，工作台恢复。

机器报告data/runtime/container/paper-runs/paper-49ce589feba34fd7b8cd8177ddd4f931.json。报告列3条调用，但今日预算used4（probe2/strategy2），成本settled0.027737442368 CNY、pending0；调用列表/预算差异本轮未定位，统计应保留两个口径，不以日志缺项清账。费用为配置估算，不是账单。

## 当前运行
随后用户授权范围内启动最长1800秒、最多360轮循环：
scripts/run_container.py paper --steps360 --max-seconds1800 --probe-models --via-rancher-vm。
后台使用Start-Process -WindowStyle Hidden；启动器PID18040，实际paper容器trademind-paper-2c0e76c32d1c468db3d32dbd51e49594已确认运行。不是仅创建命令。运行期间workbench停止，避免并发写原账本；helper结束或失败后恢复workbench。
日志data/runtime/container/20261012-paper-loop.stdout.log、stderr.log，最终运行报告由PaperRun写入同目录paper-runs。日志/报告只记录程序既有公开字段，不读取打印凭据。

## 接续与限制
Task保持in_progress，当前后台运行尚未最终验收。下个会话先核对PID/唯一paper容器、最终报告及是否恢复工作台；禁止在paper活跃时自行启动workbench写同一账本。要停止优先通过现有定向停止/恢复流程并核对worker排空。运行请求/费用仍受既有上限，等待或缺数据不强制BUY；513100缺行情仍待原行情任务核验。
当前真实模型探针/策略请求成功不是完整JEV动作/BUY→SELL→清仓或五日观察验收。
