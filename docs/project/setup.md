# 环境与依赖

自动模式新增compose auto profile/service，以同一8321端口和runtime/.env只读配置挂载替代workbench；不并行启用。入口scripts/run_auto.py继承既有代理，容器supervisor执行无需Docker socket或新依赖。健康检查验证Linux监督进程及30秒心跳，restart unless-stopped、stop_grace_period150秒；详见[操作](operations.md#容器自动模拟监控)。停止会话或睡眠不会替代常开宿主。[ARCH-013](../architecture/13-auto-paper-supervisor.md)。

每日报告新增strategy_plan_history：现有SQLite初始化IF NOT EXISTS追加，原账户数据不重置，无新依赖。2026-10-12已重建并部署镜像340babf97299…，430容器回归通过，中文日报/strategy-4实际生效；配置及runtime挂载保留。报表落在runtime/daily-reports，当前SQLite主CNY账户适用，不涵盖独立内存港股/期货账本。[验证](../research/20261012-daily-report-verification.md)。

2026-10-12部署镜像更新为336dd7a99ead…，使用现有Dockerfile/依赖/Compose；strategy-3/fast-2与宿主hash一致，414容器测试通过，原挂载/账户保留。未升级宿主环境。[证据](../research/20261012-image-update-verification.md)。

2026-10-15镜像更新为cfa3888eda83…（跨源观察修复+配置资金适配），容器/工作区源码SHA256 86/86一致，镜像内隔离回归47 passed，halted保持；构建用华为PyPI镜像源（官方源本机实测不可用，方式同Task11）。[证据](../research/20261015-image-update-verification.md)。

2026-10-10新增运行报告/时限、共同档案采集入口，命令与限制统一见[operations](operations.md#有界运行与采集2026-10-10)。Python/依赖/Schema/.env配置未升级，四参考费率沿用已核验来源。Windows Docker管道异常时可用run_container.py的显式--via-rancher-vm入口，不重启其他项目、不更改全局代理。

Task18启动入口已加免费门禁。显式`paper --steps N --probe-models`闭市会blocked并退出非零，0收费/订单；无需修改配置。最新镜像重建、工作台恢复healthy/HTTP200，实际命令/预算/限制见[Task18报告](../research/20261009-task18-verification.md)。完整盘中启动尚未验收。

Task15显式容器入口完成；Task16窗口工程已验证308测试，真实盘中验收仍待执行。源码变更已触发镜像重建；工作台网络仍不自动继承入口参数。详见[Task16报告](../research/20261009-task16-verification.md)。

容器显式网络入口已实施：`scripts/run_container.py check` 或 `paper --steps N`，宿主venv运行；读取既有宿主代理，仅传子进程，不修改compose/.env网络。paper先停workbench并最终恢复，默认不收费探针。工作台本身不继承此参数。操作/验证与限制见[Task15报告](../research/20261009-task15-verification.md)。

后续变更按[Task15～18及原验收队列](../tasks/index.md#后续实施入口2026-10-09)实施；本手册只写已验证命令，规划中的新入口完成后由执行者补命令及结果。

## 显式ETF分钟源（2026-10-09）

[ARCH-008](../architecture/08-etf-minute-source.md) implemented：market/adapters/sina_etf.py及DatasetRouter显式sina_etf，核验ETF限定、1m、8秒、量额单位/字段校验、未闭合分钟过滤、fetch可见时点。container示例及container/fast local已选择该源，不调用股票SDK回退。三ETF数据能读取，夜间正确stale；源完整性/盘中新鲜度待验收。更新Key后JEV经现有代理三题型HTTP200，直连451/正常启动网络条件与盘中质量仍待验收。完整证据与下一步统一见[运行连接报告](../research/20261009-runtime-connectivity.md)。

## 免费分钟质量路由（2026-10-14）

[ARCH-020](../architecture/20-free-minute-source-routing.md) implementing（2026-10-15复核更正）：`[data]` 新增10个可选策略字段，当前默认值如下，未配置时使用默认值——`minute_source_timeout_seconds=10.0`（单源有界等待，覆盖akshare内部不可配15s超时）、`minute_max_sources=3`、`minute_round_timeout_seconds=30.0`、`minute_failure_backoff_seconds=60.0`、`minute_max_backoff_seconds=900.0`、`minute_daily_request_limit=4000`（按北京日、逐源计数）、`minute_cross_check_enabled=false`（默认关闭零额外请求）、`minute_cross_check_interval_seconds=900.0`、`minute_cross_check_max_daily=24`、`minute_cross_check_tolerance_ratio="0.005"`。主备经 `provider_pref`（示例 `["sina_etf","akshare"]`）声明；东财备用依赖可选包 akshare（已在本仓库锁文件内，无新依赖）。fixture/付费源不作为真实paper分钟回退，必盈未核验保持排除。本地local配置未切换；切换由操作者显式修改local后重启。无凭据写入任何配置。 2026-10-15复核发现的交叉观察工程缺口（[Issue15-01](../issues/ISSUE-20261015-01-跨源观察一致性与时间预算门禁不完整.md)）当日修复并全量回归（570 passed）：差异为instrument级持续拒绝直至合格一致观察，观察共用整轮预算并沿用as_of接受策略；首次观察不再依赖进程运行时长。配置字段与默认值不变。真实盘中双源质量仍归Task09-16验收。 最新独立复验补充：[Task15-02](../tasks/TASK-20261015-02-复验跨源观察四项修复.md)本轮570项全量通过，但发现已有缓存命中未检查证券级持续拒绝，仍可返回ok；Issue15-01重新open、Task14-04继续修复，完整缓存拒绝门禁未验收。配置字段/启动三态不等于此剩余边界已经通过。

## 模型参考费率（2026-10-09）

container公共示例及container/fast local已填写：Flash输入2、输出8 CNY/百万token；JEV输入0.282786、输出0。Flash按高峰缓存未命中上限，JEV采用当日参考汇率；运行预算为估算，非供应商账单。来源、优惠时段及真实结果统一见[费率报告](../research/20261009-model-pricing.md)。价格变更需重新核验；修改local后重启workbench。当前JEV容器451、免费分钟连接与时段仍阻塞交易。

容器实测基线：Rancher Desktop1.24.0客户端、Docker29.5.3、Compose5.3.1、Python3.12.14/UID1000；requirements不变，Linux另解析akracer0.0.14及py-mini-racer0.6.0。初次构建用华为PyPI镜像传输，默认build参数仍为官方PyPI，不改宿主环境；Linux280测试通过。见TASK-20261009-11。

容器入口新增：Dockerfile使用Python3.12 Linux slim与现有requirements.txt固定依赖，Rancher Desktop Moby/Docker负责本地执行；无需Kubernetes。Compose构建白名单不包含.env/local配置/runtime，运行时只读挂载.env和独立容器配置，数据库绑定 `data/runtime/container`。配置/命令见 [operations](operations.md#rancher-desktop容器入口)，实际验证见 [TASK-11](../tasks/TASK-20261009-11-Rancher容器运行.md)。宿主venv不升级。

2026-10-09：[ARCH-006](../architecture/06-jev-fast-decision.md) 新 profile 的离线工程已实施（02、04～07 completed）；03官方认证与参考费率其后已完成，08真实对照校准、09真实启动仍待真实条件。旧默认链保留，新profile须显式选择。见 [验证报告](../research/20261009-fast-decision-verification.md)。

## 新 profile 配置

新增 config/t0-fast.example.toml 与忽略的 config/t0-fast.local.toml（首次创建，未覆盖用户已有文件），独立 paper-t0-fast 账户及 data/runtime/t0-fast-paper.db。固定deepseek-flash及jev-1.13.0；两个Key仍通过已有.env加载，不写进示例。

需核验配置 t0.model_input_cny_per_million、model_output_cny_per_million、jev_input_cny_per_million、jev_output_cny_per_million。JEV官方输出免费可填0，输入美元费率先核验CNY换算；缺价格阻止请求，不假设0。模型份额30/60/10且全角色总100及5 CNY限制；常规参数在需求契约唯一维护。

环境依赖未变：Python3.12.10，现有pydantic2/requests/pytest。无安装/升级操作。

## 多市场观察配置

复用AKShare可选依赖，无新增依赖/密钥。market-access.local.toml已创建，CNY500/HKD0、确认权限空；config/*.local.json与local.toml均忽略Git。参数与规则见[操作说明](operations.md#多市场观察与购买条件筛选)。

## 前端构建环境（2026-10-26，Task26-03/14）

Node 便携版 v22.23.4：`D:\tools\nodejs`（免管理员、不改系统 PATH；bash 需 `export PATH="/d/tools/nodejs:$PATH"`）。前端工程 frontend/（Vite 7 + Vue 3.5 + TypeScript 5.9 + ECharts 6，依赖已装入 node_modules）：

- 构建：`cd frontend && npm run build` → 产物写入 src/trademind/api/static/workbench/（pyproject package-data 已包含，wheel 可带出）
- 类型检查：`npx vue-tsc --noEmit`（0 错基线）
- 单元测试：`npx vitest run`（30 passed 基线）

Node 仅构建用；运行时（serve/observer/paper）不依赖 Node。

## 当前工作环境

两个独立工作副本，事实分别维护：

- **D:\trademind**（2026-10-22 起，TASK-20261022-01 本地运行使用）：Windows + Git Bash/cmd.exe（禁止 powershell.exe）。无系统 Python，用 uv 安装 Python 3.12.15 并创建标准 `.venv`；依赖按 requirements.txt 精确安装（含 `-e .` 可编辑安装），改代码重启即生效。本地代理 `http://127.0.0.1:7890`（JEV 需经代理，直连 451；启动本地监督时导出 HTTPS_PROXY/HTTP_PROXY，NO_PROXY 含 `localhost,127.0.0.1`）。Docker Desktop 可用但本项目已转本地运行；Docker Hub 需经 DaoCloud 加速器（`docker.m.daocloud.io`）拉基础镜像。
- F:\Project\TradeMind（历史副本，见下方历史记录）：Python 3.12.10，工作目录 F:\Project\TradeMind；`.venv` 与校验脚本在该副本运行。D:\trademind 无 F 盘链接目标，`check_docs.py` 的 F 盘 file:// 历史错误在 D 盘副本恒存在。

账户写租约（[ARCH-017](../architecture/17-writer-lease.md)）要求运行数据库目录可写；Windows与Linux容器（python:3.12-slim，fcntl.flock，真实子进程）均已验证：相对storage.url与绝对拼写指向同一实际库得到同一租约，第二写者被拒。运行日历为`data/fixtures/calendar_xshg.csv`（当前覆盖至2026-12-31）+同名`.meta.json`出处旁证（含csv_sha256，由程序加载时校验）；更新方式见[operations](operations.md#容器自动模拟监控)，程序侧只读、永不联网更新日历。数据库结构升级在每个`Database()`打开时先以只读immutable连接预检Schema版本（更高版本拒绝且文件字节不变），再按`schema_migrations`顺序自动进行（当前v1→v2，附加性）；备份与恢复核验见[operations](operations.md)（verify_database含逐账户资金对账）（[ARCH-019](../architecture/19-db-upgrade-backup.md)）。

## 语言与依赖

- Python >=3.12；venv 位于 `.venv/`。
- 直接依赖：pydantic 2.x、requests、python-dotenv；API：fastapi+uvicorn；数据（可选）：akshare/baostock/tushare；开发：pytest、httpx。
- 锁文件 `requirements.txt` 固定环境依赖并使用本地 `-e .`；更新时保留本地安装入口，不写回远程旧提交自依赖。

## 环境创建与构建（已验证命令）

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\generate_fixtures.py   :: 确定性测试数据（仓库已含生成物）
.venv\Scripts\python -m pytest --ignore=tests/test_multi_asset.py   :: 481 项测试应全部通过（2026-10-14 基线；test_multi_asset 因环境缺 pandas 持续排除）
```

## 外部服务、数据库与系统依赖

密钥配置步骤见 [README](../../README.md#配置密钥真实能力仍须独立核验)。配置加载自动读取项目根目录 `.env`，不向进程环境写入值；同名变量优先级为系统环境 > 管理页覆盖 > `.env` > TOML（[ARCH-024](../architecture/24-admin-runtime-settings.md)）。不搜索其他父目录的 `.env`。DeepSeek 旧别名 `DEEPSEEK_API_KEY` 在两个名字同时设置时优先，建议仅设置项目变量。两份本地配置均已 Git 忽略。JEV官方TypeSafe adapter已实现并通过三题型认证（[Issue-20261007-12](../issues/ISSUE-20261007-12.md) resolved）；未配置密钥时仍以显式stub离线运行。

- 存储：SQLite（data/runtime/trademind.db，自动创建，WAL）。备份恢复与Schema升级见[operations](operations.md#部署与进程)；打开旧库自动按版本顺序迁移，不做降级。
- DeepSeek/LLM：env `TRADEMIND_DEEPSEEK_API_KEY`（或 config [llm].api_key）；线协议 `TRADEMIND_LLM_PROTOCOL`（或 config [llm].protocol，默认 `openai_chat`；另支持 `anthropic_messages` / `openai_responses` / `gemini_generate_content`，[ARCH-025](../architecture/25-llm-multi-protocol.md)）。base_url 未设或仍为 DeepSeek 默认时按协议取官方端点，自定义中转 URL 原样生效；协议与型号锁定正交，不构成换型号。
- JEV：env `TRADEMIND_JEV_BASE_URL` / `TRADEMIND_JEV_API_KEY`（无则 stub）。
- 管理端控制令牌：env `TRADEMIND_API_TOKEN`。
- 管理页运行时设置：工作台「管理员设置」可替代手工编辑配置 DeepSeek/JEV 密钥、服务地址、LLM 接口协议（枚举校验）与 Tushare Token；保存至 gitignored 的 `data/runtime/admin_settings.json`，重启 serve/auto 生效，接口仅回传掩码值。管理令牌自身与模型型号不可在页面修改。
- replay 模式拒绝加载凭据（启动校验）；管理页覆盖仅在加载凭据路径生效，隔离模式不受影响。

## 版本控制

Git 主分支 main，远程 origin https://github.com/apppsd/trademind.git。

## 独立安装校验限制（2026-10-07）

已在新建的临时 venv 按 requirements.txt 实际安装并运行测试；随后构建/安装 wheel，在仓库外确认 Schema、网页、fixture 日历/数据与 replay-demo 可用。命令：`.venv/Scripts/python.exe scripts/verify_install.py`。测试依赖下载需要网络；模拟回放不出网。详细结果见 [复验报告](../research/20261007-code-audit.md#修复复验2026-10-07)。

## T0模型预算配置（2026-10-08）

安全起点为config/t0-shadow.example.toml；复制为独立config/t0-paper.local.toml后按实际环境设置，不覆盖已有本地配置。该示例513050.SH、500paper、独立SQLite和端口8322，行情仅akshare。JEV段本次未修改，真实适配/服务必须后续独立核验。

[t0]新增model_input_cny_per_million、model_output_cny_per_million（当前已核验的CNY每百万token价格；不填写则收费DeepSeek请求拒绝）、model_max_output_tokens（默认1024）。费用口径及未知JEV成本见[复验报告](../research/20261008-non-jev-fix-verification.md#费用口径)。TRADEMIND_STORAGE_URL可覆盖数据库路径。没有新增依赖、升级或安装方式变化。

不要用示例占位文本充当数字价格。Key继续只放已有本地.env；config/*.local.toml也已Git忽略。测试实际197项通过，真实服务本轮未调用。

## 2026-10-08 T0已登记全池更新

config/t0-paper.local.toml已创建，不含Key并被Git忽略；universe/whitelist为3只T0，AKShare免费分钟源。model_input_cny_per_million=2、model_output_cny_per_million=8采用已核验高峰人民币价格，使用前核对是否调价。无新增依赖，现有AKShare1.19.1已安装；实际连接ProxyError待解决。


工作台V2验收边界（2026-10-26）：Node与构建命令的环境事实不变；构建/类型检查不能替代运行验收，observer入口与数据契约仍在返修。详见[独立复核](../research/20261026-workbench-independent-review.md)。本轮未安装、升级或修改环境。


工作台V2返修环境与打包（2026-10-26）：主Python/Node依赖未升级。主venv没有pip/build，验证使用既有uv的离线缓存：`uv build --wheel --offline --out-dir data/runtime/task26-16-wheel`；独立临时venv安装wheel后在仓库外验证。wheel包含static/workbench与share/trademind/etf_whitelist.json；目录/规则支持源码与安装位置，规则本身不变。构建、安装冒烟和未验视觉边界见[返修报告](../research/20261026-workbench-repair-verification.md)。此前“仍有入口/契约缺陷”描述为修复前记录，当前代码已修复而运行未切换。
