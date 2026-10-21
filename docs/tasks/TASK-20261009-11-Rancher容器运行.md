---
id: TASK-20261009-11
type: task
title: Rancher Desktop容器运行与有界模拟预检
status: completed
priority: high
created: 2026-10-09
updated: 2026-10-09
owner: Codex
related_issues: []
related_architecture: [ARCH-007]
---

# Rancher Desktop容器运行

## 任务信息
Owner Codex；main / F:/Project/TradeMind。范围Dockerfile、.dockerignore、compose.yaml、独立容器配置及文档；不覆盖原应用/账户。当前Rancher Desktop已安装但未运行，Docker daemon不可连接。

## 需求与非目标
用户明确授权通过Rancher Desktop运行容器。启动现有引擎，构建Python3.12镜像，提供本机工作台和有界paper命令。凭据只在运行时只读挂载；镜像不含.env、local配置或runtime。无实盘、发布、推送、不修改既有全局网络。费率/交易时段/行情门禁照常执行，不伪造费率或强行交易。

## 验收标准
Rancher引擎连接、镜像构建、容器启动与HTTP工作台可访问；container内离线验证及真实免费行情预检有证据，paper有界试跑给出结果/阻塞原因。账户/runtime与宿主现有盘隔离；费用未配置时不调用付费模型。

## 计划
- [x] 启动引擎并添加容器定义。
- [x] 构建、容器验证与有界试跑。
- [x] 文档同步。

## 文档同步契约
README、docs/project/setup.md、operations.md：构建/启动/停止/挂载；system-overview、code-map、flows、current-status、index：实际容器边界/证据；docs/architecture/07-container-runtime.md：新增部署历史；docs/tasks/index.md：任务；Research：本任务记录实际输出；Issue：仅新观察故障时创建；roadmap：目标不变不适用；Release不适用。

## 验证与未验证
真实行情/完整双模型闭环未通过，容器启动不能替代该验收。

## 实际结果（2026-10-09 21:46～21:48北京时间）

- Rancher Desktop Moby/Docker29.5.3、Compose5.3.1；镜像trademind:local，Linux Python3.12.14，非root UID1000。host venv/依赖文件无修改。
- 构建命令 `docker compose build --build-arg PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple` 成功。原PyPI约27KB/s、TUNA包下载403、Ali源仍慢，最终本次build使用华为镜像（约10～17MB/s），不改global代理/pip配置。Linux额外解析akracer0.0.14、py-mini-racer0.6.0；requirements固定版本保留。
- 首次容器测试75失败/205通过，根因COPY漏data/etf_whitelist.json导致ETF规则缺失。已补入Dockerfile/.dockerignore白名单及公共规则示例；业务代码无修改。
- 最终 `docker run --rm --entrypoint python trademind:local -m pytest -o addopts='' -q`：280 passed、1项既有Starlette/httpx弃用警告，41.12秒。
- 无挂载镜像检查：.env/local配置不存在，UID1000。凭据仅运行时只读挂载，未写入镜像或日志。
- `docker compose up -d workbench`：trademind-workbench-1 running/healthy，HTTP根页面200；/api/status为paper / paper-t0-container / deepseek-flash / stub=false / live_available=false。仅127.0.0.1:8321。留工作台运行，无常驻交易任务。
- 容器check报告 `data/runtime/container/container-startup.json`：四费率未填、非连续交易时段、三ETF分钟ConnectionError，credentials_loaded=false/model_calls0/orders0。既有行情Issue-20261008-04保持open。
- 停workbench后 `docker compose --profile paper run --rm paper` 有界启动失败（ProviderError费用门禁），预算used0/费用0；随后重开workbench。容器账户cash500/reserved0/positions0/open_orders0/model_calls0。

完成的是容器入口及工作台，不是盘中双模型成交验收。Task-03/08/09及五日观察不变；后续先填核实费率、解决分钟连接并在盘中验证。

Checkpoint：已用rdctl后台启动现有Rancher Desktop（未切换引擎/重置/改网络）；Docker29.5.3、Compose5.3.1、OperatingSystem=Rancher Desktop WSL Distribution。Compose配置验证通过；Dockerfile构建上下文约749KB，已有requirements固定版本安装中。独立容器配置已创建（无假设费率），host凭据无载入预检确认configured_prices=false、session_open=false、模型0/订单0。已有其他项目容器不改动。

## Documentation Sync Check
已同步README、setup、operations、system-overview、code-map、flows、current-status、index、Task索引、ARCH-007与行情Issue。Compose语法/文档链接/git diff --check通过；部署implemented，真实交易未完成。roadmap目标不变/Release不适用。无commit/push，不修改其他项目容器或卷。
