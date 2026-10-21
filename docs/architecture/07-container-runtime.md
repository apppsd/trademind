---
id: ARCH-007
type: architecture-change
title: Rancher Desktop本地容器运行
status: implemented
created: 2026-10-09
updated: 2026-10-09
related_tasks: [TASK-20261009-11]
recorded_retrospectively: false
---

# Rancher Desktop本地容器运行

2026-10-12部署Checkpoint：用户授权用既有Rancher VM Compose入口构建新镜像并只重建workbench，原挂载/账户/配置保留；414隔离容器测试及实际提示版本、健康/HTTP200核验通过。[证据](../research/20261012-image-update-verification.md)。部署边界未变，未启动paper或真实模型请求。

## 背景与目标
用户要求以Rancher Desktop运行现有系统。此前为Windows venv/SQLite本地启动；新增Linux Python3.12镜像及Compose入口，业务模型、Guard、paper OMS不改变。

## 变更与边界
源文件和固定requirements构建镜像，.dockerignore与COPY白名单排除凭据、local配置和运行账户。运行时只读挂载.env和显式容器配置；专属data/runtime/container目录挂载SQLite。工作台仅绑定宿主127.0.0.1端口；paper命令有界运行，无定时自启交易。运行同一数据库时只允许一个订单写入进程，工作台不调用交易动作。

## 非目标与风险
不新增实盘、不改变行情/费用/时段门禁；Linux依赖及网络须实测，容器启动不等于双模型交易验收。容器数据独立，原宿主账户不迁移。真实服务运行不能采用stub冒充模型通过。

## 验证与文档同步
证据与逐文件同步见TASK-20261009-11。实施完成后记录实际引擎、镜像和端口。

实现证据：Rancher Desktop Moby/Docker29.5.3、Compose5.3.1、trademind:local（Python3.12.14/UID1000），镜像无.env/local配置；最终Linux280测试通过。trademind-workbench-1 healthy，HTTP200、本机8321，ETF规则文件明确COPY。独立500账户、模型0/订单0。paper被未填费率拦截，分钟连接失败。implemented仅指部署，ARCH-006真实交易验收仍implementing。

## 发布与回滚
仅构建本地镜像，不推送registry；Compose人工启动，无restart/交易调度。`docker compose down`停止本项目容器，独立挂载runtime保留，可回到原Windows venv入口。不得删除其他项目容器或volume。
