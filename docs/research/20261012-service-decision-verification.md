# 容器服务与决策就绪核验

2026-10-12，上海09:20～09:23，关联TASK-20261012-01。用户要求核验已启动服务的操作与决策；本轮没有收费API调用、订单操作、镜像重建或代理改动。

## 服务实测
docker ps：trademind-workbench-1 healthy，127.0.0.1:8321；首页HTTP200，/api/status和/api/orders可访问。API显示paper、paper-t0-container、jev_fast_decision、deepseek-flash/jev_typesafe且非stub，三ETF白名单。当前进程Flash/JEV capability均false；queued0、executed0。

serve入口启动FastAPI/uvicorn，没有自动启动t0-paper循环。工作台健康不等于自动模型决策正在运行，也不等于当前进程完成了真实模型认证。

## 容器版本
fast_prompts导入ModuleNotFoundError，模块文件不存在。镜像ID sha256:0012b6ef97d65379c48c0bf85847eafaacee7a7f3b0d4e75ea81cc6ad000fcfc，创建时间2026-10-10 20:23:14+08:00；新版宿主提示未进该镜像。[ISSUE-20261012-01](../issues/ISSUE-20261012-01.md) open。该失败不代表既有fast planner/decider无法导入，两者文件均存在。

## 免费预检
执行容器python scripts/check_fast_startup.py --config config/t0-container.local.toml --timeout 25 --output data/runtime/20261012-service-precheck.json。
报告status blocked：费率四项已填、paper/快速链配置正确；session_open false，三ETF各bars0、unavailable。阻塞not_continuous_session、fresh_minute_data_unverified、real_models_not_probed_in_this_check。

此时尚非连续交易时段；预检不探测收费模型，最后一项是未验证，不等于Key失败。三ETF未取得分钟数据，具体网络/源原因本轮未定位。真实数据源检查发生在预开盘，不能推断整日行情质量。

完整机器报告保存在宿主data/runtime/container/20261012-service-precheck.json；凭据加载false、model_calls0、orders0。

## 隔离决策工程测试
容器执行python -m pytest -o addopts='' -q tests/test_fast_decision.py tests/test_t0_minimal_runner.py：34 passed，1.97秒。这些测试使用临时账户与mock/fixture，可证明当前镜像的既有决策/runner工程路径，不证明真实双模型输出、真实行情成交或新版提示表现；不会将fixture结果冒作真实服务决策。

## 结论与接续
工作台读操作正常；当前不能验收真实自动决策。先按授权重建并核对版本，再在真实连续交易时段做数据质量检查，按单writer运行入口停工作台/启有界paper/结束恢复，在同一决策进程内验证模型能力。禁止通过同时运行两进程写同一账本来测试；不强制BUY，不放宽资金/费用门禁。
原盘中质量、档案、对照校准、真实BUY→SELL→清仓和五日观察仍未完成。
