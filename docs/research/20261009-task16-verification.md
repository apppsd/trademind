# Task16 分钟特征工程验证（2026-10-09）

本轮只完成离线工程，Task16保持in_progress。真实盘中至少三个分开采样时点尚未取得；夜间15:00数据不能用于验收新鲜度。

## 已观察根因与实际修复

ContextBuilder请求两日分钟数据；此前短周期收益、ATR、量比按行数取窗口，遇缺分钟/午休/隔夜仍可能当成连续分钟，VWAP累计两个日期。现在ret_1m/5m/15m、ATR14、量比和regime只取最新连续1m片段；断点重新预热，缺字段写原因。VWAP只用最新上海日期已观察到的量额；不是完整日数据保证，513100早盘缺失原因仍需查明。成交额为零且成交量为正时不再用收盘价估算VWAP。5m聚合不能伪装1m指标。

分钟feature_version更新为minute-feat-2026-10-09；日线版本保持feat-2026-10-07。Schema、数据所有权、资金及180秒新鲜度门禁未改。

## 验证

- `.venv/Scripts/python.exe -m pytest tests/test_etf_minute_data.py tests/test_container_run.py -o addopts='' -q`：23 passed。
- `.venv/Scripts/python.exe -m pytest -o addopts='' -q`：308 passed，21.99秒；1条既有Starlette/httpx弃用提示。
- 新增6个用例覆盖缺口/午休/隔夜、恢复窗口、5m周期拒绝和成交额缺失；既有日线/策略/风险回归通过。
- 本轮免费容器检查/paper证据见[Task15报告](20261009-task15-verification.md)。新增付费API请求0。

## 未验证与下一步

容器工程复验已通过；真实盘中数据质量、513100缺失、独立源交叉检查未完成。特征缺失作为质量事实输出，本轮没有改交易入口，不声称所有missing字段都会阻止收费请求；Task18将收敛先免费检查再收费探针。Task16实际验收后再执行依赖它的Task18/17。今日probe10/10，不增额度、不清预算。

## 容器复验与最终运行状态

`docker compose build --build-arg PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple`成功，既有固定依赖保持不变。

`docker run --rm --entrypoint python trademind:local -m pytest -o addopts='' -q`：308 passed，34.58秒，1条相同弃用提示；测试使用隔离临时数据，没有挂载真实.env或运行账户账本。

`docker compose up -d workbench`重建并恢复，2026-10-09 23:03左右健康，http://127.0.0.1:8321/ HTTP200；compose只运行workbench，无paper/check交易worker。镜像包含最新分钟特征版本。无收费请求、无新增订单，无提交推送。
