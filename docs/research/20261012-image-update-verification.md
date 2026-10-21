# 工作台镜像更新验证

2026-10-12；关联TASK-20261012-02、ISSUE-20261012-01。用户明确授权镜像更新；未改业务源码、密钥、代理、交易参数，未启动paper循环或收费探针。

## 实际命令
```text
rdctl shell docker-compose --project-directory /mnt/f/Project/TradeMind --file /mnt/f/Project/TradeMind/compose.yaml build workbench
docker run --rm --entrypoint python trademind:local -m pytest -o addopts='' -q
rdctl shell docker-compose --project-directory /mnt/f/Project/TradeMind --file /mnt/f/Project/TradeMind/compose.yaml up -d --no-deps --force-recreate workbench
```

构建成功，只重新创建workbench；原runtime/config/.env挂载保留，凭据只读，没有删除数据库/volume/旧镜像。测试容器无生产runtime或凭据挂载。依赖声明与Dockerfile未修改。

## 镜像与版本
旧镜像：sha256:0012b6ef97d65379c48c0bf85847eafaacee7a7f3b0d4e75ea81cc6ad000fcfc。
新镜像：sha256:336dd7a99ead5464f929480a36bc4250f5d769a57e5e42d76831eb436487536d。
运行容器.Image与新tag的.Id一致；运行容器导入strategy-3/fast-2成功。strategy提示hash为69e997a86e9807727a112ff435f586725bba7980bc2cffdbaee0612d7f882fd4，与宿主一致。

## 验证结果
- 新镜像完整回归414 passed、1个既有Starlette弃用警告，16.89秒。
- 实际服务running/healthy，首页HTTP200；API paper/jev_fast_decision/deepseek-flash且非stub。
- 数据库只读核对前后：paper-t0-container、account version2、cash500、reserved_cash0、orders0、model_calls10（历史总数）。无新增模型请求/订单。
- 当前服务进程双模型capability仍false、executed0；没有启动交易循环。
- 数据目录仍/mnt/f/Project/TradeMind/data/runtime/container，账本未重置。

## 完成与限制
ISSUE-20261012-01 resolved：旧镜像缺新版提示的问题已关闭。镜像更新不修复独立晋级/回滚ISSUE-20261011-01，不代替盘中数据、真实模型语义、完整模拟闭环或五日验收。

