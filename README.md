# TradeMind

本地自动模拟监控（当前默认，无Docker）：`scripts/run_local.py start/status/stop`，Windows 本地 venv 直跑，账户 `paper-t0-local-1k`（1000 CNY，独立库）。前置：`config/t0-local.local.toml`、根目录 `.env` 双模型 Key、shell 导出宿主代理（`HTTPS_PROXY` 必需）。盘中自动三ETF模拟循环，午休/休市等待，程序计算数量/硬风控/模拟执行；改代码后 `stop`→核对→`start`（可编辑安装即改即生效）。详见 [本地运行操作](docs/project/operations.md#本地-windows-自动模拟监控2026-10-22-arch-024) 与 [TASK-20261022-01](docs/tasks/TASK-20261022-01-本地千元双模型自动模拟交易落地.md)。

容器自动模拟监控（兼容入口）：auto容器按上海交易日历等待开市，盘中自动运行原三ETF模拟循环，午休/休市等待；原行情、模型、资金及预算门禁保留。使用 `.venv/Scripts/python.exe scripts/run_auto.py start` 启动、`status`查看、`stop`停止（Rancher运行且既有宿主代理可用）。自动模式与旧workbench不能同时启用；新代码由独立只读observer提供V2页面，当前运行切换状态见[当前状态](docs/project/current-status.md)。[自动运行说明](docs/project/operations.md#容器自动模拟监控)。

每日交易日报：工作台首页“查看今天交易日报”或[报告页面](http://127.0.0.1:8321/reports/daily)。按上海日保存资金、入出金、成交/策略、盈亏及模型费用JSON/Markdown/HTML；页面和文字报告使用中文，新模型策略说明要求中文。运行工作台每分钟刷新，paper结束时生成；生成报告不调用模型。口径和补历史入口见[运行手册](docs/project/operations.md#每日交易日报)。

大屏工作台V2：[http://127.0.0.1:8321/workbench/](http://127.0.0.1:8321/workbench/)。中文大屏与只读接口的账户、曲线、财务、observer和刷新缺陷已完成代码返修，见[验证报告](docs/research/20261026-workbench-repair-verification.md)。现有后端进程未重启，修复需按既有停启流程加载；实际大屏视觉、交互与回退仍待验收，[ARCH-027](docs/architecture/27-chinese-large-screen-workbench.md)保持implementing。构建见[setup](docs/project/setup.md)，运行边界见[operations](docs/project/operations.md)。

已新增24例提示行为案例及零调用的保存响应评测工具，使用方式见[离线提示评测](docs/project/operations.md#离线提示评测)；工程验证和真实未验项见[本轮报告](docs/research/20261011-prompt-eval-verification.md)。

快速链提示词入口为 `src/trademind/agents/fast_prompts.py`，版本与验证/容器生效步骤见[运行手册](docs/project/operations.md#快速链提示编辑与生效)。新版已通过离线回归，真实交易完整验收仍按当前状态推进。

新增有界运行机器报告和真实共同档案采集工具；命令见[运行手册](docs/project/operations.md#有界运行与采集2026-10-10)，最新进度见[当前状态](docs/project/current-status.md)。官方JEV协议/费用验收已完成，真实盘中交易闭环和五日观察仍待验证。容器显式网络入口等历史实施命令与验证证据统一见[运行手册](docs/project/operations.md)与 docs/research/ 报告，本页不重复维护状态叙述。

## Rancher Desktop运行

复制 `config/t0-container.example.toml` 为 `config/t0-container.local.toml`，创建 `data/runtime/container` 后，在根目录运行：

```powershell
docker compose up -d --build workbench
docker compose --profile check run --rm check
```

工作台 http://127.0.0.1:8321 ，隔离模拟账户（初始资金以local配置为准，示例500、当前实际1000）。密钥从根目录.env只读挂载，未打入镜像。paper有界启动、费率配置和停止命令见 [容器运行操作](docs/project/operations.md#rancher-desktop容器入口)。容器启动不代表盘中双模型交易验收。

新快速链修复与验证见 [TASK-20261009-10](docs/tasks/TASK-20261009-10-修复快速决策架构问题.md)。`t0-paper` 的新 profile 使用异步模型与5秒检查轮次；`--steps` 是轮次数，单轮通常返回 `WAIT_MODEL_PENDING`，有未消费结果时输出 `incomplete` 并返回2。运行诊断包含数据年龄、期限、原因、预算和 Guard 拒绝原因；模型 worker 无下单权限。操作/回放输入见 [运行文档](docs/project/operations.md)。

新 profile（flash低频策略 + JEV快速动作 + 程序硬风控）离线工程已实施，官方JEV认证与参考费率已核验；真实行情、完整启动与交易闭环仍未验收。旧默认候选/审核/汇总链保留。[方案](docs/architecture/06-jev-fast-decision.md) · [契约](docs/requirements-jev-fast-decision.md)。

分钟行情已有免费新浪ETF主源和AKShare东财备用的质量回退与可选交叉观察（[ARCH-020](docs/architecture/20-free-minute-source-routing.md)，implemented——工程口径）。10-15独立复核发现的交叉观察四项缺口与缓存分支绕过当日全部修复并经独立复验（Issue15-01 resolved、Task14-04 completed、全量577+通过）；2026-10-16已有真实策略与JEV成功样本，下午续跑仍受探针额度阻断（见当前状态）。撮合侧同样受质量门禁约束（[ARCH-021](docs/architecture/21-t0-intraday-order-lifecycle.md)同期落地订单生命周期）。真实三ETF备用样本与盘中双源质量待Task09-16，本地local配置未切换。[配置说明](docs/project/setup.md)与[运行手册](docs/project/operations.md)。

## 新链入口

使用独立的 `config/t0-fast.local.toml`（已创建，参考费率已填写，见费率报告），先执行无凭据预检：

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-fast.local.toml t0-fast-check
```

真实启动、四项费率及离线对照见[操作说明](docs/project/operations.md#新链操作与门禁)。本次未启动模拟盘、未调用收费模型。

## 多市场行情入口（2026-10-08）

ETF、LOF、可转债、港股和境内期货已接入批量发现、按需分钟观察、购买条件筛选及工作台观察表。实测得到327只可转债与724个期货合约，共1051个观察标的；ETF/LOF/港股与可转债分钟连接仍受ProxyError影响。[验证报告](docs/research/20261008-multi-asset-verification.md)。

本地配置`config/market-access.local.toml`已创建，默认500 CNY、100预留、0 HKD，确认权限为空。新市场目前只观察，模拟执行仍只有原股票/核验ETF链路，后续按[Task-06～09](docs/tasks/index.md)适配。

```bat
.venv\Scripts\python -m trademind.runner --config config/market-access.local.toml market-data
.venv\Scripts\python -m trademind.runner --config config/market-access.local.toml market-bars 110072.SH --kind convertible_bond
```

报告位于`data/runtime/market-observation.json`，工作台已有观察表。账户/规则配置见[操作说明](docs/project/operations.md#多市场观察与购买条件筛选)。观察入口不调用模型或写订单。

以 AI 为决策核心的股票交易项目：大模型投研与交易决策，JEV 快速判断，程序负责事实计算、硬性风控、执行和评估。

## 当前状态

当前事实唯一入口：[docs/project/current-status.md](docs/project/current-status.md)（顶部为最新状态与未验清单）；实施进度与 Owner 见[任务索引](docs/tasks/index.md)，开放问题见[问题索引](docs/issues/index.md)。官方JEV认证与参考费率已核验完成；盘中真实行情质量、完整模拟交易闭环与五交易日观察仍未验收。不得把离线测试结果当作可连续运行或可盈利证据。

## 项目入口

- [当前任务入口](docs/tasks/index.md)
- [T0 影子测试需求（当前阶段）](docs/requirements-t0-shadow.md) · [V1 需求与交付范围（历史/兼容）](docs/requirements.md)
- [当前项目事实](docs/project/index.md)
- 架构记录：[ARCH-001 V1 方案](docs/architecture/01-ai-trading-system.md) · [ARCH-002 T0 双模型](docs/architecture/02-t0-etf-shadow.md) · [ARCH-003 运行安全门禁](docs/architecture/03-t0-runtime-safety.md) · [ARCH-004 行情池轮询](docs/architecture/04-t0-market-pool.md)
- [Agent 工作协议](AGENTS.md)

## 安装（Windows）

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest --ignore=tests\test_multi_asset.py   :: 481 项测试全部通过（2026-10-14 基线；multi_asset 因环境缺 pandas 排除）
```

## 配置密钥（真实能力仍须独立核验）

复制根目录 `.env.example` 为 `.env`，在本地填写：

```dotenv
TRADEMIND_DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
TRADEMIND_JEV_API_KEY=YOUR_JEV_API_KEY
# 可选：LLM 接口协议（默认 openai_chat；另支持 anthropic_messages /
# openai_responses / gemini_generate_content，经中转站时按线协议选择）
TRADEMIND_LLM_PROTOCOL=openai_chat
```

启动时自动读取项目根目录 `.env`，不修改进程环境。优先级：系统环境变量 > 管理页覆盖 > `.env` > TOML（两个 DeepSeek 变量同时存在时旧别名 `DEEPSEEK_API_KEY` 优先，建议只使用下面的项目变量）。`.env` 和 `config/trademind.toml` 均被 Git 忽略，不要把真实密钥填进示例文件。

也可以不手工编辑 `.env`：启动工作台后在「管理员设置」区块配置 DeepSeek/JEV 密钥、服务地址与 LLM 接口协议（需控制令牌，重启进程生效；密钥仅掩码回传），见[运行操作](docs/project/operations.md#管理员设置密钥与服务地址2026-10-21)与 [ARCH-024](docs/architecture/24-admin-runtime-settings.md)。

其他参数可复制 `config/trademind.example.toml` 为 `config/trademind.toml` 后修改。支持的环境变量：

- `TRADEMIND_DEEPSEEK_API_KEY`：DeepSeek 密钥（官方 OpenAI 兼容接口，模型名见配置）
- `TRADEMIND_LLM_PROTOCOL`：LLM 接口协议（`openai_chat` 默认 / `anthropic_messages` / `openai_responses` / `gemini_generate_content`，见 [ARCH-025](docs/architecture/25-llm-multi-protocol.md)；Base URL 留默认时按协议使用官方端点）
- `TRADEMIND_JEV_BASE_URL` / `TRADEMIND_JEV_API_KEY`：JEV 服务（未提供则显式 stub）
- `TRADEMIND_API_TOKEN`：管理端控制令牌（不设则控制接口禁用）
- 工作台「管理员设置」：上述 DeepSeek/JEV 密钥、服务地址与 Tushare Token 也可在页面配置（保存于 gitignored 的 `data/runtime/admin_settings.json`，重启生效；模型型号与管理令牌不可在页面修改）

JEV 官方TypeSafe协议适配与三题型认证已完成（TASK-41，[Issue-20261007-12](docs/issues/ISSUE-20261007-12.md) resolved）；填写 Key 不代表真实盘中交易链路已验收。replay 模式仍拒绝带凭据启动，离线回放时不要加载含真实 Key 的配置。

不配置任何密钥时系统以**显式 stub**离线运行（页面与状态均标注 stub，不冒充真实模型）。

T0 运行另需复制 `config/t0-shadow.example.toml` 为 `config/t0-paper.local.toml` 并填写已核验的模型 CNY 价格，见 [环境配置](docs/project/setup.md#t0模型预算配置2026-10-08)；所有 T0 运行/页面/恢复命令统一使用同一份显式 TOML，见 [运行操作](docs/project/operations.md#t0统一配置运行2026-10-08)。

## 使用（已验证命令）

```bat
.venv\Scripts\python -m trademind.runner status                     :: 运行状态
.venv\Scripts\python -m trademind.runner probe-llm                 :: DeepSeek 能力探针（脱敏输出）
.venv\Scripts\python -m trademind.runner demo-decision 600036.SH   :: 在线一次决策（旧 fixture 可能无可用新行情）
.venv\Scripts\python -m trademind.runner poll-fills                :: 轮询模拟成交
.venv\Scripts\python -m trademind.runner replay-demo               :: 离线回放评估
.venv\Scripts\python -m trademind.runner recover                   :: 崩溃恢复对账
.venv\Scripts\python -m trademind.runner serve                     :: 管理端+工作台 http://127.0.0.1:8321
```

T0 模拟盘命令（统一显式配置）：

```bat
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-data              :: 免费行情连通检查（不读密钥）
.venv\Scripts\python -m trademind.runner --config config/t0-paper.local.toml t0-paper --steps 1   :: 全池一轮模拟
```

## 独立验收状态

- 2026-10-07：V1 首版 8 组代码缺陷修复，模拟买入/减仓/清仓与故障恢复已验证，干净环境安装通过。证据：[复验报告](docs/research/20261007-code-audit.md#修复复验2026-10-07)。
- 2026-10-08：T0 双模型模拟盘独立检查发现阻塞（[检查报告](docs/research/20261008-t0-code-review.md)）；非JEV缺陷已修复（[复验报告](docs/research/20261008-non-jev-fix-verification.md)）；3 只 ETF 全池配置完成但真实连接受阻（[报告](docs/research/20261008-t0-pool-verification.md)）。**完整双模型启动验收（TASK-55）与五日观察（TASK-56）未进行。**

## 边界声明

首版为 A 股主板普通股票的 paper/replay 模拟闭环，T0 阶段为经核验 ETF 的独立 paper 实验账户；均无 Live 执行通道。模拟默认参数见配置文件（实盘前必须人工复核）；未建立任何盈利证据。详见 [当前状态](docs/project/current-status.md)。
