# TradeMind 项目工作协议

## 基本要求

务实、清晰、可靠；任务清楚就执行，保留原意，不增加无关内容。输出简洁、可直接使用。完成后说明结果和位置。

Windows Shell 必须使用 cmd.exe 或 pwsh.exe -NoProfile，禁止 powershell.exe。

## 入口与事实来源

先检查 Git 状态（无仓库时明确记录），读取本文件；存在 active Handoff 时先恢复。随后读 README.md、docs/project/index.md、docs/tasks/index.md、docs/issues/index.md，找到当前 Task，再按需读相关代码和文档。不要默认通读历史。

实际代码、测试和可复现行为优先，其次是配置、Schema、依赖与部署文件、用户确认信息、已实现架构记录、当前事实文档、已验证 Issue、研究与计划。冲突时先确认行为，再判断修改代码还是文档。

## 文档职责与完整标准

[完整治理标准](docs/governance-standard.md)保留用户提供的规则和模板；本文件是项目操作入口。其他 Agent 入口只跳转本文件，不复制维护规则。

- docs/project/：当前事实；system-overview.md 是当前逻辑架构主入口。
- docs/tasks/：具体工作、进度、验证和任务交接。
- docs/issues/：已观察问题、根因与解决知识。
- docs/architecture/：架构变化历史；00 为初始基线。
- docs/roadmap.md：阶段目标与完成标准。

同一事实只在一处完整维护，其他文档引用路径或 ID。按需增长，不提前创建空目录或假设架构。Research、Release、Worklog、Handoff 按标准触发后创建；默认不维护 next-steps.md。

## 开始任务

非极小修改先创建或认领 TASK-YYYYMMDD-NN-简短名称.md，使用标准第 13 章模板。填写负责人、范围、状态、分支 / Worktree、需求、非目标、可验证验收标准、计划和具体到文件的文档同步契约，再实施。用简单语言说明改什么、影响和不确定性。

判断是否有 Issue、是否改变核心模块、职责、边界、依赖、重要接口、数据流、数据所有权、持久化、Queue / Event / Scheduler、进程、部署或安全边界。Task 与 Issue 职责分开；修复实施记录进 Task，根因进 Issue。

## 架构规则

变化需要历史解释时创建顺序编号记录，使用标准第 19 章模板。状态为 planning、implementing、implemented、cancelled、superseded。同一次实施中修改原记录；已实现后的独立变化新增记录，引用替代关系，不重写历史。

implemented 必须有代码、测试或实际行为证据，先检查并同步受影响的 system-overview、code-map、flows、setup。缺失历史按标准第 22 章补录，标明 recorded_retrospectively: true，不编造讨论。

## Checkpoint 与完成门禁

- Level 1：步骤完成、范围变化、阻塞、暂停、切换 Agent 或上下文压缩时更新 Task，必要时更新 Issue。
- Level 2：设计、边界、接口、数据流或重要决策变化时更新 Task 和 Architecture。
- Level 3：可运行阶段完成、架构落地、重要验证通过或宣布完成前，同步受影响的当前事实文档。
- 长任务约两小时无自然里程碑时执行 Checkpoint，不创建后台监控。

Task 完成前执行 Documentation Sync Check：确认实际行为、记录已执行验证与结果，明确未验证 / 无法验证及原因；处理相关 Issue、架构影响与替代关系、所有文档同步契约。全部处理后才能标记 completed，并更新 Task 索引。环境变化同步 setup，运行操作变化同步 operations，使用方式变化同步 README，阶段变化同步 roadmap。

## 安全与协作

不覆盖用户或其他 Task 的工作；重叠范围先停下并重新划分，所有权写进 Task。未经明确授权不 commit、push、merge、rebase、删分支、创建 PR、发布或部署。

不打印、保存或复述密码、密钥、Token、Cookie、私钥及敏感个人信息；发现疑似敏感内容提醒先脱敏，示例使用安全占位值。不要将真实凭据写进日志、测试或文档。

Python 项目先识别版本、依赖、锁文件、环境、布局、测试、质量工具和 CI；文档初始化不触发升级、依赖工具替换或包布局重构。

## TradeMind 开发入口与当前状态

最新实施入口：docs/tasks/index.md（当前批次、Owner 与实施状态）；当前事实唯一入口：docs/project/current-status.md。逐日运行状态、探针计数、测试数量与认证结论不在本文件维护——历史证据在 docs/research/ 与各 Task/Issue，当前结论只读 current-status，避免同一事实多处复制后过期。

只读文档一致性校验：`.venv\Scripts\python scripts\check_docs.py`（本地链接、重复ID、索引状态与正文frontmatter一致性、depends_on缺目标/循环、Owner重叠提醒；只报告不自动改历史）。

### 已确认目标：flash低频策略 + JEV快速决策

用户确认新分工：deepseek-flash低频生成StrategyPlan，外部TypeSafe JEV读取原始行情/账户及有效计划输出即时四动作，程序计算数量/硬风控/模拟执行。详见 docs/requirements-jev-fast-decision.md 与 docs/architecture/06-jev-fast-decision.md；旧默认链（flash候选→JEV审核→flash汇总）保留对照。不得静默换DeepSeek型号、JEV disabled/stub或单模型通过新仓门禁；程序风险退出仍经Guard/OMS。官方JEV认证与费用已核验完成；真实闭环与五日观察未验收，能力边界以 current-status 与开放 Issue 为准，不得以离线测试通过替代真实验收。

V1 股票 paper/replay 链路保留兼容（ARCH-001 implementing，TASK-23 in_progress 做前瞻观察），新工作不改变其行为。

执行规则：实施对话认领 Task 时先读 docs/requirements-t0-shadow.md、docs/requirements-jev-fast-decision.md、docs/architecture/06-jev-fast-decision.md 最新方案、docs/tasks/index.md，按 depends_on 确认依赖；认领后填写 Owner、确切代码范围、分支 / Worktree 和逐文件文档同步契约。未经明确授权不 commit/push 或实盘；文档管理会话不改代码、不读密钥、不调用收费 API。

第一版为 M0～M4 的股票模拟闭环；M5 / M6 为可选条件阶段。首期已确认 A 股，运行参数及实际模型能力未确认前不擅自接券商或认定旧对话 API 可用。AI 产生交易意图，JEV 不是订单授权；代码仅负责事实计算、合法执行与硬性否决。

保持 planning / implementing / implemented 与真实状态对应；ARCH-001 是方案，system-overview 只写实际实现。执行对话完成任务时必须回写验证证据、Issue、架构变化、当前事实与索引，使下一个会话能独立继续。

## Handoff（会话交接）

`docs/handoff/` 存放会话交接文档：会话过长、上下文即将耗尽、准备中断或用户要求交接时生成，供新会话接手。

**何时写**：用户明确要求交接；或上下文即将压缩 / 会话即将中断且工作未完成。写之前先做一次 Task Checkpoint，该进 Task / Issue 的先进正式文档，Handoff 只写尚未收敛的临时状态。

**怎么写**：文件名 `HANDOFF-YYYYMMDD-NN-简短名称.md`（同一天多份 NN 递增），frontmatter 含 `status: active`，正文必须包含：

1. **这次会话在做什么**——关联 Task / Issue 与一句话目标；
2. **已完成**——改了什么文件、验证到什么程度；
3. **中断点**——正在做什么、做到哪一步、涉及文件当前状态、运行中的服务与临时环境；
4. **尚未进正式文档的关键信息**——结论、根因、约束、踩坑；
5. **用户的关键指示**——逐条转述影响后续工作的要求与偏好；
6. **下一步**——按优先级，每步具体到"打开哪个文件做什么、怎么算完成"；
7. **未决问题与风险**；
8. **验证状态**——已验证 / 未验证及原因。

规则：引用 Task / Issue 的 ID 与路径，不复制内容，不粘贴大段代码，不写敏感信息。

**怎么接**：新会话发现 `docs/handoff/` 存在 `status: active` 的文档时，先读它（多份取最新），按其指引读关联文档，用 `git status` / `git diff` 核对与代码一致性，有出入以代码为准。恢复后把有长期价值的内容沉淀进正式文档，将该 Handoff 改为 `status: consumed` 并删除。

Handoff 是过渡载体，不是项目事实来源；消费后即删，不长期保留。

## 个人知识库（Personal Knowledge Vault）

用户维护一个跨项目的个人知识库，路径：`F:\Project\Knowledge`

它记录用户的个人视角：每天做了什么、每个项目进行到哪、踩过的坑、学到的知识。
**它与本项目 `docs/` 文档体系完全独立，两者不得混淆。**

### 唯一触发规则

**仅当用户消息中明确提到「个人知识库」时，才操作它。**
未提到时，一律只按本项目 V2 文档体系工作，不触碰知识库。

- 更新意图（"更新一下个人知识库" / "收工，更新个人知识库"）：
  先读 `F:\Project\Knowledge\AGENTS.md`，按其更新流程执行——
  新建事件文件 `Projects/TradeMind/YYYY-MM-DD_<事件>.md`（详细记录），
  更新枢纽 `Projects/TradeMind/TradeMind.md`（Resume Here 必更新、进展索引加一行链接，按需更新踩坑、决策、速查区）
  和 `Daily/<今天>.md`，必要时在 `Knowledge/` 建笔记。
  完成后向用户汇报更新了哪些文件。
- 查询意图（"个人知识库里这个项目做到哪了"）：读取并汇报，不写入。

### 对应位置

本项目在知识库中对应：`Projects/TradeMind/`（文件夹，枢纽 + 事件文件两层）
（不存在时按知识库 `Templates/project.md` 建枢纽、`Templates/event.md` 建事件文件）

### 边界

- 项目事实唯一来源仍是本项目 `docs/`；知识库只写个人视角摘要，引用项目内路径，不复制全文
- 禁止写入密码、API Key、Token 等敏感信息
- 只记录有学习/恢复上下文价值的内容，不写流水账

## 夜间开发分工（Codex 指挥 + Claude 执行，工具包 codex-claude-night-kit）
### 当 Codex 打开本仓库时
- 你是项目经理与独立评审，不是实现者；不得亲自编辑代码或文档，不得通过 shell 直接调用 Claude。
- 过夜开发通过 MCP 工具 start_night_task_chain（任务链模式）或 start_night_development（单任务模式）委托：从 docs/tasks/index.md 选择依赖已满足、可一夜自主完成并验证的 Task，写明需求、非目标与可验证验收标准。
- 用 get_night_development_status 查询进度；未取得最终评审结论不得宣称成功。
- 夜间产物只在独立 worktree（分支 night/<job_id>），调度器在 worktree 内提交；未经用户明确指示不 push、不 merge、不部署。
### 当 Claude Code 在夜间工作树中打开本仓库时
- 你是唯一实现者，只修改当前 worktree 内文件；不 commit、不 push、不部署、不改密钥、不碰生产资源。
- 按 Task 范围做最小可靠修改并同步 docs/tasks、docs/issues 记录；运行测试并如实记录命令与结果。
