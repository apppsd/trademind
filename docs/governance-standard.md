# AI 大型项目文档与架构治理标准

## 0. 核心模型

本体系用于长期、大型、多人或多智能体参与的软件项目。

文档体系只回答五类问题：

| 问题               | 唯一主要入口               |
| ---------------- | -------------------- |
| 项目现在是什么样？        | `docs/project/`      |
| 现在正在做什么？         | `docs/tasks/`        |
| 出了什么问题，为什么，怎么解决？ | `docs/issues/`       |
| 架构为什么变成现在这样？     | `docs/architecture/` |
| 项目未来往哪里走？        | `docs/roadmap.md`    |

必须始终区分：

> **当前事实、工作过程、问题知识、架构历史、未来计划。**

禁止把这些内容混写在同一个文档中。

---

# 1. 基本原则

## 1.1 真实项目优先

确认项目事实时，优先级如下：

1. 实际代码、测试和可复现行为。

2. 配置、Schema、迁移、依赖和部署文件。

3. 用户明确确认的信息。

4. 已实现的最新架构记录。

5. `docs/project/` 当前事实文档。

6. 已验证的 Issue。

7. Research、Task 计划、Roadmap 和 Worklog。


文档与代码不一致时：

> **代码不是自动正确，但代码代表实际行为。**

必须先确认实际行为，再决定修代码还是修文档。

不得为了让代码符合旧文档而盲目修改实现。

---

## 1.2 单一事实来源

同一事实只在一个地方完整维护。

其他文档只能：

- 摘要；

- 链接；

- 引用 ID；

- 描述与自身职责相关的部分。


例如：

- Issue 保存根因，不复制到 Task。

- Task 保存实施过程，不复制到 Architecture。

- Architecture 保存架构变化，不复制完整当前架构。

- `docs/project/` 保存当前结果，不保存完整变更历史。


---

## 1.3 文档服务开发

文档必须帮助开发者和 Agent：

- 快速理解项目；

- 恢复上下文；

- 判断当前任务；

- 理解架构；

- 避免重复排障；

- 完成交接。


不得为了“文档完整”维护大量没人使用的文档。

---

## 1.4 按需增长

不得提前创建大量空目录、空文档或占位架构。

项目复杂度增长后再拆分。

原则：

> 先简单，出现真实复杂度后再结构化。

---

## 1.5 长任务必须收敛

长任务不能依赖聊天上下文保存状态。

重要事实必须进入：

- Task；

- Issue；

- Architecture；

- `docs/project/`。


聊天不是项目事实来源。

---

# 2. 默认目录结构

```text
ProjectRoot/
├── AGENTS.md
├── README.md
│
├── pyproject.toml / package.json / ...
│
├── docs/
│   ├── project/
│   │   ├── index.md
│   │   ├── current-status.md
│   │   ├── system-overview.md
│   │   ├── code-map.md
│   │   ├── setup.md
│   │   ├── operations.md
│   │   └── flows.md
│   │
│   ├── roadmap.md
│   │
│   ├── tasks/
│   │   ├── index.md
│   │   └── TASK-*.md
│   │
│   ├── issues/
│   │   ├── index.md
│   │   └── ISSUE-*.md
│   │
│   ├── architecture/
│   │   ├── 00-initial-architecture.md
│   │   ├── 01-*.md
│   │   └── ...
│   │
│   ├── research/
│   ├── releases/
│   ├── worklogs/
│   └── handoff/
│       └── HANDOFF-*.md
│
├── src/
├── tests/
└── ...
```

已有合理项目结构时沿用现状。

不得仅为了符合本规范而：

- 移动代码；

- 修改包布局；

- 更换依赖工具；

- 修改 Git 工作流；

- 创建无意义目录。


---

# 3. 文档职责边界

## 3.1 `AGENTS.md`

Agent 的统一项目操作协议。

回答：

> 在这个项目里应该怎么工作？

包含：

- 项目入口；

- 项目事实来源；

- 开始工作流程；

- Task 规则；

- Issue 规则；

- Architecture 规则；

- Checkpoint；

- 验证；

- 文档同步；

- Git 安全；

- 交接与 Handoff（会话交接）规则。


---

## 3.2 `README.md`

面向使用者和开发者。

回答：

> 这个项目是什么，如何安装、运行和使用？

不保存：

- 当前任务；

- 排障流水；

- 架构历史；

- 开发计划。


---

## 3.3 `docs/project/`

**当前项目事实层。**

回答：

> 如果今天第一次进入项目，现在系统到底是什么样？

这里的内容必须随项目演进保持当前有效。

不得保存完整历史。

---

## 3.4 `docs/tasks/`

**执行层。**

回答：

> 当前具体在做什么，做到哪里，怎么验证？

Task 是一次可交付工作的生命周期记录。

---

## 3.5 `docs/issues/`

**问题知识层。**

回答：

> 什么问题发生了？为什么？最终怎么解决？

Issue 是问题知识库，不是任务计划。

---

## 3.6 `docs/architecture/`

**架构变化历史层。**

回答：

> 系统为什么从以前变成现在这样？

这里不是当前系统架构的唯一入口。

当前系统架构写入：

```text
docs/project/system-overview.md
```

---

## 3.7 `docs/roadmap.md`

**未来阶段层。**

回答：

> 项目接下来几个阶段准备达到什么状态？

Roadmap 不保存 Task 执行细节。

---

# 4. `docs/project/` 当前事实体系

## 4.1 index.md

唯一作用：

> 提供当前项目文档入口和极简摘要。

```markdown
# 项目状态索引

## 当前摘要

- 当前阶段：
- 已具备的核心能力：
- 当前主要限制：

## 当前事实

- 系统概览：`system-overview.md`
- 当前状态：`current-status.md`
- 代码地图：`code-map.md`
- 环境与依赖：`setup.md`
- 运行操作：`operations.md`
- 运行流程：`flows.md`

## 工作入口

- Roadmap：`../roadmap.md`
- Tasks：`../tasks/index.md`
- Issues：`../issues/index.md`
```

禁止在 index 中复制大量详细内容。

---

# 5. `current-status.md`

只记录项目当前状态。

```markdown
# 当前状态

## 项目目标

## 当前阶段

## 已实现能力

## 正在形成的能力

只描述能力状态。

具体实施工作链接 Task。

## 当前系统级限制

只保存当前仍成立的限制。

已解决限制应删除，并通过 Issue / Architecture 保留历史。

## 当前风险

只记录项目级风险。
```

不得放：

- 详细任务步骤；

- 历史变更流水；

- 排障过程；

- 大量未来功能。


---

# 6. `system-overview.md`

这是 V2 的核心新增文档。

它是：

> **当前逻辑架构的唯一主入口。**

````markdown
# 系统概览

## 系统目标

## 系统边界

系统负责什么。

系统明确不负责什么。

## 核心组件

### Component A

- 职责：
- 输入：
- 输出：
- 依赖：

### Component B

...

## 组件关系

```text
Client
  ↓
API
  ↓
Application
  ↓
Domain
  ↓
Storage
````

## 数据所有权

|数据|所有者|持久化位置|
|---|---|---|

## 外部系统

## 关键接口

这里只描述重要系统边界。

详细接口以代码、Schema 或 API 定义为准。

## 关键约束

## 部署 / 运行边界

## 相关架构记录

- `../architecture/07-xxx.md`

- `../architecture/12-xxx.md`


````

原则：

> `system-overview.md` 写“现在是什么”。

> `architecture/NN-*` 写“为什么变成这样”。

---

# 7. `code-map.md`

回答：

> 如果我要修改某个能力，代码去哪里找？

```markdown
# 代码地图

## 程序入口

## 核心目录

| 路径 | 职责 |
|---|---|

## 核心模块

| 模块 | 职责 | 主要入口 |
|---|---|---|

## 测试

## 配置

## 数据 / Schema / Migration

## 常用定位关系

- 用户认证 → `...`
- Agent Runtime → `...`
- 数据持久化 → `...`
````

禁止把整个目录树机械复制进来。

只记录具有导航价值的路径。

---

# 8. `setup.md`

回答：

> 项目真实运行环境是什么？

包含：

- 语言版本；

- 依赖管理工具；

- 锁文件；

- 环境创建；

- 构建方式；

- 外部服务；

- 数据库；

- 系统依赖；

- 已确认的平台限制。


所有命令必须来自真实项目。

---

# 9. `flows.md`

回答：

> 主要业务或系统流程如何运行？

初始使用单文件：

```text
docs/project/flows.md
```

例如：

```markdown
# 主要运行流程

## Agent 请求

Client
→ API
→ Scheduler
→ Agent Runtime
→ Tool
→ Response

## 文件处理

...

## Memory 写入

...
```

出现以下任一情况时允许拆分：

- 存在多个独立长期演进流程；

- 单文件明显难以导航；

- 不同 Agent 经常只需要其中一个流程；

- 文件接近数百行且仍持续增长。


升级为：

```text
docs/project/flows/
├── index.md
├── agent-request.md
├── memory-write.md
├── file-processing.md
└── task-execution.md
```

禁止为了预想中的未来复杂度提前拆分。

---

# 10. Roadmap

大型项目存在明确阶段时使用：

```text
docs/roadmap.md
```

```markdown
# Roadmap

## 当前阶段

### M3 Agent Runtime 重构

- 状态：进行中
- 目标：
- 完成标准：
- 关联 Task：
  - TASK-...
  - TASK-...

## 下一阶段

### M4 ...
```

Roadmap 只维护：

- 阶段；

- 目标；

- 状态；

- 完成标准；

- Task 链接。


不得维护：

- 实施步骤；

- 文件修改；

- 调试过程；

- 详细技术设计。


---

# 11. 不再默认维护 `next-steps.md`

V2 默认取消：

```text
docs/project/next-steps.md
```

原因：

- 当前工作属于 Task；

- 阶段方向属于 Roadmap；

- 当前状态属于 current-status。


只有小型项目没有 Roadmap，同时确实存在未形成 Task 的近期方向时，才允许使用 `next-steps.md`。

Roadmap 与 next-steps 不应同时承担同一职责。

---

# 12. Task 生命周期

Task 表示：

> 一件计划执行并产生交付结果的工作。

适用：

- 新功能；

- 改进；

- 重构；

- Bug 修复实施；

- 文档专项；

- 性能优化；

- 架构实施。


---

## 12.1 命名

```text
TASK-YYYYMMDD-NN-简短名称.md
```

例如：

```text
TASK-20260817-03-重构Agent调度器.md
```

Task ID：

```text
TASK-20260817-03
```

---

# 13. Task 模板

```markdown
---
id: TASK-20260817-03
type: task
title: 重构 Agent 调度器
status: pending
priority: medium
created: 2026-08-17
updated: 2026-08-17
owner:
related_issues: []
related_architecture: []
---

# TASK-20260817-03 重构 Agent 调度器

## 任务信息

- 负责人 / Agent：
- 状态：
- 修改范围：
- 分支 / Worktree：

## 背景

为什么要做。

## 需求

## 不包含

明确 Scope Boundary。

## 验收标准

必须能够验证。

## 执行计划

- [ ] 步骤一
- [ ] 步骤二
- [ ] 步骤三

## 文档同步契约

### 当前事实

- `README.md`：更新 / 不适用
- `docs/project/current-status.md`：更新 / 不适用
- `docs/project/system-overview.md`：更新 / 不适用
- `docs/project/code-map.md`：更新 / 不适用
- `docs/project/setup.md`：更新 / 不适用
- `docs/project/flows.md`：更新 / 不适用
- `docs/roadmap.md`：更新 / 不适用

### 历史与知识

- Architecture：新建 / 更新 / 不适用
  - 文件：
- Issue：新建 / 更新 / 不适用
  - 文件：
- Research：新建 / 更新 / 不适用
- Release：发版阶段处理 / 不适用

## 实施记录

### 已完成

### 实际修改

### 与原计划的差异

设计或范围发生变化时立即记录。

### 当前阻塞

## 验证

- 命令：
- 结果：

## 未验证内容

## Documentation Sync Check

- [ ] 当前代码行为已经确认
- [ ] `docs/project/` 与当前实现一致
- [ ] Architecture 记录反映最终设计
- [ ] Issue 已记录需要保留的根因知识
- [ ] 文档同步契约中的项目均已处理

## 交接

- 当前状态：
- 已修改文件：
- 已验证内容：
- 剩余工作：
- 风险：
- 下一步：
```

---

# 14. Task 索引

`docs/tasks/index.md` 只记录活动任务和少量最近完成项。

```markdown
# Tasks

## 活动任务

| Task | 名称 | Owner | 状态 | 范围 | 更新时间 |
|---|---|---|---|---|---|

## 最近完成

| Task | 名称 | 完成日期 | 结果 |
|---|---|---|---|
```

不得复制 Task 文件内容。

---

# 15. 文档同步契约

Task 创建时必须确定可能受影响的文档。

禁止只写：

```text
docs/project/: 更新
```

必须具体到：

```text
docs/project/system-overview.md
docs/project/flows.md
```

Task 开始时的判断允许之后改变。

发生变化时：

1. 更新 Task 的文档同步契约；

2. 写明变化原因；

3. 再继续实施。


---

# 16. Architecture 的定义

`docs/architecture/` 保存的是：

> **Architecture Change Record，架构变化记录。**

它不是一套不断覆盖更新的“总架构说明书”。

当前总架构在：

```text
docs/project/system-overview.md
```

---

# 17. 什么属于架构变化

以下情况默认属于架构影响：

- 增加、删除或拆分核心模块；

- 改变模块职责；

- 改变模块边界；

- 改变模块调用关系；

- 改变核心依赖方向；

- 改变重要 API / Protocol / Message Contract；

- 改变主要数据流；

- 改变数据所有权；

- 改变持久化方式；

- 引入或改变 Queue / Event / Scheduler；

- 改变进程边界；

- 改变部署拓扑；

- 改变核心安全边界；

- 引入重要基础设施；

- 重要技术方案替换。


以下通常不需要 Architecture：

- 普通 Bug 修复；

- 局部内部重构；

- 函数改名；

- 样式和文案；

- 测试补充；

- 不影响外部行为的小型性能优化；

- 普通依赖 patch 升级。


不确定时判断：

> 如果三个月后有人需要知道“为什么系统会这样设计”，则应该有 Architecture 记录。

---

# 18. Architecture 文件生命周期

命名：

```text
00-initial-architecture.md
01-add-agent-runtime.md
02-split-memory-service.md
...
```

`00-` 是初始化基线。

之后按架构变化顺序增加。

---

## 18.1 同一次变化

同一个需求从：

```text
规划
→ 开发
→ 设计调整
→ 实施
→ 验证
```

始终更新同一个 Architecture 文件。

例如：

```text
07-agent-scheduler.md
```

最初设计：

```text
API → Queue → Worker
```

开发中变成：

```text
API → Scheduler → Queue → Worker Pool
```

仍然修改：

```text
07-agent-scheduler.md
```

不得因此创建：

```text
08-change-agent-scheduler-again.md
```

---

## 18.2 后续独立变化

当 `07` 已经成为系统现实后，未来再次修改该架构，则：

```text
08-xxx.md
```

并引用：

```text
Supersedes:
- ARCH-007
```

历史不得重写。

---

# 19. Architecture 模板

```markdown
---
id: ARCH-007
type: architecture-change
title: Agent Scheduler
status: planning
created: 2026-08-17
updated: 2026-08-17
related_tasks:
  - TASK-20260817-03
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# ARCH-007 Agent Scheduler

## 状态

规划中 / 实施中 / 已实现 / 已取消 / 已替代

## 背景

为什么需要变化。

## 目标

## 非目标

## 变更前

描述真正相关的旧架构。

## 方案

## 核心设计决策

## 模块影响

## 接口影响

## 数据流影响

## 数据所有权影响

## 部署 / 运行影响

## 代码范围

## 测试范围

## 实施中的设计变化

记录：

- 原方案：
- 新方案：
- 原因：

## 最终实施结果

只在实际实现后填写。

## 验证

## 当前事实同步

- `docs/project/system-overview.md`：已同步 / 不适用
- `docs/project/code-map.md`：已同步 / 不适用
- `docs/project/flows.md`：已同步 / 不适用
- `docs/project/setup.md`：已同步 / 不适用

## 关联内容

- Task：
- Issue：
- Research：
- Previous Architecture：
- Next Architecture：
```

---

# 20. Architecture 状态

允许：

```text
planning
implementing
implemented
cancelled
superseded
```

含义：

### planning

设计已形成，但代码尚未实际落地。

### implementing

正在实现，设计仍允许演进。

### implemented

已经被代码、测试或实际运行确认。

### cancelled

方案没有实施。

文件保留。

### superseded

曾经实现，但后来被新的 Architecture 替代。

不得删除。

---

# 21. 当前架构同步规则

Architecture 文件成为：

```text
implemented
```

之前必须检查：

```text
docs/project/system-overview.md
docs/project/code-map.md
docs/project/flows.md
docs/project/setup.md
```

只更新真正受影响的文件。

原则：

> Architecture 记录变化。

> Project 记录变化完成后的当前事实。

---

# 22. 架构漂移修复协议

这是大型长期项目必须具备的机制。

如果发现：

> 代码已经变了，但是架构文档没有同步。

不得直接假装历史不存在。

执行：

## 情况 A：对应 Architecture 仍为 implementing

继续更新原 Architecture。

记录：

```markdown
## 实施中的设计变化

原方案：

实际方案：

调整原因：
```

然后同步当前 Task。

---

## 情况 B：Architecture 已 implemented，但后续代码又发生独立架构变化

创建新的：

```text
NN-new-change.md
```

不得覆盖旧 Architecture。

---

## 情况 C：变化已经实现，但从未创建 Architecture

创建当前最新编号 Architecture。

状态可直接标记：

```text
implemented
```

并填写：

```yaml
recorded_retrospectively: true
```

正文必须说明：

```text
该记录根据已经存在的代码、测试和配置进行补录。
```

不得伪造当时不存在的设计讨论。

---

## 情况 D：只有 `docs/project/` 过期

如果架构变化历史已经正确，只是当前事实文档没同步：

直接修正：

```text
system-overview.md
flows.md
code-map.md
...
```

不额外创建 Architecture。

---

# 23. Issue

Issue 表示：

> 已经观察到的异常、缺陷、风险或未知问题。

适用：

- Bug；

- 异常行为；

- 数据错误；

- 性能异常；

- 环境问题；

- 不确定根因的问题；

- 需要后续跟踪的风险。


---

# 24. Task 与 Issue

判断：

> 是一件“要去做的工作”，还是一个“已经发生的问题”？

|场景|处理|
|---|---|
|新功能|Task|
|重构|Task|
|已发现问题但暂不处理|Issue|
|修复 Bug|Issue + Task|
|架构问题整改|Issue + Task + Architecture（如果结构变化）|

Task：

> 怎么完成修复。

Issue：

> 为什么会发生。

不得重复。

---

# 25. Issue 模板

```markdown
---
id: ISSUE-20260817-01
type: issue
title:
status: open
priority: medium
created: 2026-08-17
updated: 2026-08-17
resolved:
related_tasks: []
related_architecture: []
---

# ISSUE-20260817-01

## 问题表现

## 影响

## 环境

## 复现方式

## 原因分析

## 尝试过的方法

只记录对未来有知识价值的尝试。

## 最终解决方法

## 验证结果

## 剩余风险

## 关联内容

- Task：
- Architecture：
- 代码：
- 测试：
- Release：
```

---

# 26. Research

Research 表示：

> 尚未成为项目事实的调查结果。

必须区分：

- 外部事实；

- 分析；

- 推测；

- 推荐方案；

- 已验证结论。


Research 被项目采用后：

- 实施进入 Task；

- 架构变化进入 Architecture；

- 当前结果进入 `docs/project/`。


Research 本身不自动代表项目决策。

---

# 27. Release

只有存在正式发布流程时使用。

Release 保存：

> 一个版本实际交付了什么。

不保存：

- 完整 Task；

- Issue 根因；

- Architecture 详细设计。


Release 只链接它们。

---

# 28. Worklog

Worklog 供用户个人总结使用。

Agent 默认不读取。

只有用户明确要求：

- 今日总结；

- 下班总结；

- 今日踩坑；

- 今日学习；


才创建：

```text
docs/worklogs/YYYY-MM-DD-工作内容.md
```

内容：

```markdown
# YYYY-MM-DD 工作总结

## 今日完成

## 主要修改

## 验证

## 遇到的问题

## 解决方式

## 可以学习到什么

## 待办
```

Worklog 是历史，不随以后项目变化回写。

---

# 29. Agent 开始工作流程

每次新会话或 Agent 接手项目：

1. 检查 Git 状态。

2. 阅读 `AGENTS.md`。

3. 检查 `docs/handoff/`：存在未消费（`status: active`）的交接文档时，先按第 56 章接手流程处理。

4. 阅读 `README.md`。

5. 阅读 `docs/project/index.md`。

6. 阅读 `docs/tasks/index.md`。

7. 阅读 `docs/issues/index.md`。

8. 找到当前任务。

9. 阅读当前 Task。

10. 按任务需要读取：

    - system-overview；

    - flows；

    - code-map；

    - Architecture；

    - Issue；

    - Research。

11. 检查实际代码和配置。


不得默认读取全部：

```text
architecture/
research/
releases/
worklogs/
```

---

# 30. Plan Before Code

任何非极小修改开始前：

1. 创建或认领 Task；

2. 写 Scope；

3. 写验收标准；

4. 写执行计划；

5. 填写文档同步契约；

6. 判断是否存在 Issue；

7. 判断是否存在 Architecture 影响；

8. 需要 Architecture 时立即创建或认领；

9. 再开始编码。


简单任务计划可以很短。

但不能完全依赖聊天计划。

---

# 30.1 计划必须对用户说人话

做计划时，除了写进 Task 的正式计划，还必须**用最简单的话向用户解释清楚接下来要做什么**，让用户不用读代码、不用懂术语也能搞明白你在干嘛。

要求：

- **先做一段"人话说明"**：在给出正式计划前，用 2~3 句大白话告诉用户"我接下来要改什么、为什么这么改、改完会有什么效果"。
- **不用术语、不堆缩写**：不写"重构 AST 解析层的 visitor 模式"，而写"我要把解析代码那块的逻辑换个更清楚的写法，方便以后改 bug"。
- **讲清楚影响面**：告诉用户这次改动会影响哪些功能/文件，用户用起来的体验会不会变。
- **讲清楚风险**：如果有不确定、可能要来回试的地方，先说明，别等出了问题再解释。
- **让用户能确认**：说明完后给用户一个明确的确认点，比如"这样改可以吗？"或"如果方向不对告诉我"。

原则：用户只看你的解释就能判断方向对不对，不需要打开代码。

---

# 31. Architecture Impact Check

Task 开始前必须回答：

```text
是否改变：

[ ] 核心模块
[ ] 模块职责
[ ] 模块边界
[ ] 依赖方向
[ ] 重要接口
[ ] 数据流
[ ] 数据所有权
[ ] 持久化
[ ] Queue/Event/Scheduler
[ ] 进程边界
[ ] 部署拓扑
[ ] 安全边界
```

全部为否：

通常无需 Architecture。

任一为是：

检查已有相关 Architecture。

---

# 32. 三级 Checkpoint

## Level 1：Task Checkpoint

触发：

- 完成一个计划步骤；

- 范围变化；

- 遇到阻塞；

- 准备暂停；

- 准备切换 Agent；

- 上下文即将压缩。


更新：

```text
Task
```

必要时更新 Issue。

触发原因为「上下文即将压缩」「准备切换 Agent」或会话即将中断时，同时生成 Handoff 交接文档（见第 56 章）。

---

## Level 2：Architecture Checkpoint

触发：

- 设计改变；

- 模块边界改变；

- 接口改变；

- 数据流改变；

- 原架构方案失效；

- 新的重要技术决策产生。


更新：

```text
Task
+
Architecture
```

如果发现新 Issue：

```text
+
Issue
```

---

## Level 3：Milestone Checkpoint

触发：

- 一个可运行阶段完成；

- Architecture 实际落地；

- 重要能力验证通过；

- 准备宣布任务完成。


更新：

```text
Task
+
Architecture
+
受影响的 docs/project/*
```

---

# 33. 时间兜底规则

如果长任务没有自然里程碑：

连续工作约两小时后执行一次 Checkpoint。

时间规则只是兜底。

优先级：

```text
真实事件触发
>
时间触发
```

不得因此自行创建：

- 操作系统后台任务；

- 无限循环；

- 常驻监控进程。


---

# 34. Context Recovery

发生以下情况：

- 会话中断；

- Context Compact；

- 更换模型；

- 更换 Agent；

- 第二天继续；

- Worktree 切换；


恢复时不得依赖聊天记忆。

重新读取：

```text
AGENTS.md
最新 Handoff（docs/handoff/ 最新一份，如存在）
当前 Task
相关 Issue
相关 Architecture
实际 Git Diff
相关代码
验证结果
```

然后判断：

> 文档描述的状态是否仍与代码一致？

---

# 35. 多 Agent 协作

每个活动 Task 必须声明：

- Owner / Agent；

- 修改范围；

- 分支或 Worktree；

- 当前状态。


多个 Agent 修改范围发生重叠时：

1. 不覆盖对方修改；

2. 停止重叠范围；

3. 重新划分 Scope；

4. 更新 Task；

5. 再继续。


不得通过聊天记住“谁负责什么”。

任务所有权必须进入文件。

---

# 36. 多 Agent 文档写权限

默认：

### Task

Agent 只更新自己负责的 Task。

### Issue

处理相关问题的 Agent 可更新。

### Architecture

处理对应架构变化的 Task Owner 更新。

### Project Current State

完成真实实现并验证后更新。

### Roadmap

只有任务确实改变阶段状态时更新。

避免多个 Agent 同时频繁改同一个全局文件。

---

# 37. 验证原则

不得写：

> 测试通过。

除非实际运行过测试。

Task 必须区分：

```text
已验证
未验证
无法验证
```

无法验证时说明：

- 原因；

- 风险；

- 未覆盖范围。


---

# 38. Python 项目

必须先识别：

- Python 版本；

- `pyproject.toml`；

- 锁文件；

- 虚拟环境；

- 实际包布局；

- 测试工具；

- Ruff / mypy 等已有工具；

- CI；

- 构建方式。


不得因为文档初始化：

- 升级 Python；

- 更换 Poetry / uv / pip；

- 重构包目录；

- 引入新的质量工具；

- 修改锁文件策略。


依赖改变时同步：

```text
依赖声明
+
锁文件
+
必要的 setup.md
```

---

# 39. Git 安全

未经明确授权：

不得：

- commit；

- push；

- merge；

- rebase；

- 删除分支；

- 创建 PR；

- 发布；

- 部署。


不得撤销用户未要求撤销的修改。

不得覆盖其他 Task 的工作。

---

# 40. 敏感信息

禁止进入：

- Task；

- Issue；

- Architecture；

- Worklog；

- Git；

- 测试输出；


的内容包括：

- Password；

- API Key；

- Token；

- Cookie；

- 私钥；

- 真实敏感个人信息。


`.env.example` 只保存：

- 变量名；

- 说明；

- 安全占位值。


---

# 41. 文档触发矩阵

|实际变化|更新位置|
|---|---|
|当前工作进度|Task|
|工作范围变化|Task|
|验证结果|Task|
|发现异常|Issue|
|根因确认|Issue|
|Bug 修复实施|Task + Issue|
|模块结构变化|Architecture|
|接口边界变化|Architecture|
|数据流变化|Architecture|
|当前逻辑架构变化|system-overview|
|代码入口变化|code-map|
|当前运行流程变化|flows|
|环境或依赖变化|setup|
|使用方式变化|README|
|项目阶段变化|roadmap|
|外部技术调查|research|
|正式版本发布|releases|
|用户要求今日总结|worklogs|
|会话中断 / 上下文即将耗尽 / 用户要求交接|handoff|
|服务器 / 部署方式 / 常用命令 / 配置文件 / 关键路径变化|operations|

---

# 42. 单次修改的同步算法

每完成一段实际修改后判断：

```text
代码变化
   │
   ├─ 是否只是局部实现？
   │      └─ 是 → Task
   │
   ├─ 是否发现问题？
   │      └─ 是 → Issue
   │
   ├─ 是否改变架构？
   │      ├─ 当前变化已有 Architecture
   │      │      └─ 更新原文件
   │      └─ 独立的新架构变化
   │             └─ 创建下一编号
   │
   └─ 当前事实是否已经变化并验证？
          └─ 是 → 更新 docs/project/
```

---

# 43. Task 完成门禁

Task 不允许仅因为：

> “代码写完了”

就标记完成。

必须执行：

## Completion Gate

### 实现

-  需求已完成

-  Scope 内工作已处理


### 验证

-  已运行相关验证

-  未执行验证已明确记录


### Issue

-  相关 Issue 已更新

-  根因没有只留在 Task 或聊天


### Architecture

-  已完成架构影响判断

-  Architecture 反映最终方案

-  被替代架构关系已更新


### Current Project

-  `system-overview` 与代码一致

-  `code-map` 与代码一致

-  `flows` 与代码一致

-  `setup` 与真实环境一致


### 文档契约

-  Task 中所有文档影响已经处理


全部处理后才能：

```yaml
status: completed
```

---

# 44. 不要求所有项都必须修改

Completion Gate 的含义不是：

> 每次 Task 都改全部文档。

而是：

> 每个文档都必须经过“是否受影响”的判断。

大量 Task 最终可能只修改：

```text
Task
+
代码
+
测试
```

这是正常的。

---

# 45. 文档规模升级

## tasks/

单目录约超过 200 个 Task 时：

```text
tasks/
├── index.md
├── 2026/
├── 2027/
└── ...
```

---

## issues/

同样允许按年份拆分。

---

## flows

从：

```text
flows.md
```

升级：

```text
flows/
```

---

## Architecture

原则上继续保持编号序列。

数量很多时可以增加：

```text
architecture/index.md
```

只做导航：

```markdown
# Architecture Index

## Current Relevant

## Recently Implemented

## Superseded

## By Domain
```

但 Architecture 文件本身保持稳定，不移动、不重命名。

---

# 46. 文档禁止事项

不得：

- 每次修改都创建 Architecture；

- 每个想法都创建 Task；

- 每次调试命令都写 Issue；

- 把 Git Log 复制成 Worklog；

- 把源代码全部复制进文档；

- 把完整 API 定义手工复制到多个地方；

- 在 Roadmap 重复 Task；

- 在 current-status 重复 Task 状态；

- 在 Architecture 重复当前系统完整说明；

- 在 Release 重复 Issue 根因；

- 把 Handoff 当作 Task 或项目事实的长期载体；

- 长期堆积已消费或已失效的 Handoff。


---

# 47. 初始化流程

新项目或老项目首次引入本体系：

1. 检查 Git 状态。

2. 检查现有项目结构。

3. 检查 README。

4. 检查语言和依赖配置。

5. 检查测试和 CI。

6. 识别程序入口。

7. 识别核心模块。

8. 识别主要运行流程。

9. 读取现有文档。

10. 不覆盖已有有效内容。

11. 创建或完善 `AGENTS.md`。

12. 创建 `docs/project/` 当前事实文档。

13. 创建 Task / Issue 索引。

14. 创建初始化 Task。

15. 创建 `00-initial-architecture.md`。

16. 建立 Roadmap（仅大型项目需要）。

17. 验证文档中的路径和命令。

18. 检查敏感信息。

19. 检查文档重复。

20. 完成初始化 Task。


---

# 48. `00-initial-architecture.md`

`00` 不是未来永久维护的当前架构文档。

它表示：

> 引入本体系时确认的架构基线。

以后系统变化：

```text
01
02
03
...
```

当前最终状态持续反映在：

```text
docs/project/system-overview.md
```

因此：

```text
00 = 当时
system-overview = 现在
```

---

# 49. 从旧体系迁移到 V2

已有项目不需要重写全部历史。

执行一次迁移 Task。

## Step 1

创建：

```text
docs/project/system-overview.md
```

根据当前实际代码生成当前架构。

---

## Step 2

检查：

```text
current-status.md
code-map.md
setup.md
flows.md
```

只修明显过期内容。

---

## Step 3

检查历史 Architecture。

不要为了统一格式重写所有旧文件。

旧记录保持原样。

未来新 Architecture 使用 V2 模板。

---

## Step 4

检查现有活动 Task。

将：

```text
docs/project/: 更新
```

逐步改成具体文件级别。

只修改活动 Task，不批量重写历史 Task。

---

## Step 5

如果存在已经实现但从未记录的重大架构变化：

按“架构漂移修复协议”补录。

---

## Step 6

将 `next-steps.md` 内容分类：

属于阶段方向：

```text
→ roadmap.md
```

属于具体工作：

```text
→ Task
```

属于当前事实：

```text
→ current-status.md
```

处理完成后删除或停止维护 next-steps。

---

# 50. 多智能体入口

根目录：

```text
AGENTS.md
```

是唯一完整规则。

其他入口只做跳转。

例如：

### CLAUDE.md

```markdown
请先阅读并遵守项目根目录的 `AGENTS.md`。

随后读取：

1. `README.md`
2. `docs/project/index.md`
3. `docs/tasks/index.md`
4. `docs/issues/index.md`

再根据当前 Task 读取相关代码、Issue、Architecture 和 Project 文档。
```

Cursor / Copilot 等同理。

禁止复制维护多套不同规则。

---

# 51. Agent 工作的标准闭环

整个体系最终形成：

```text
Roadmap
   │
   ▼
 Task
   │
   ├───────────────┐
   ▼               ▼
 Issue        Architecture
   │               │
   └──────┬────────┘
          ▼
         Code
          │
       Verification
          │
          ▼
    docs/project/
     当前项目事实
```

对应含义：

```text
Roadmap      → 为什么下一阶段要做
Task         → 这一次具体做什么
Issue        → 出了什么问题
Architecture → 为什么系统设计发生变化
Code         → 实际系统
Project      → 当前系统是什么
```

---

# 52. 最终判断口诀

遇到不知道写哪里时：

### “现在是什么？”

写：

```text
docs/project/
```

### “这次在做什么？”

写：

```text
docs/tasks/
```

### “为什么坏了？”

写：

```text
docs/issues/
```

### “为什么架构变了？”

写：

```text
docs/architecture/
```

### “以后准备做什么？”

写：

```text
docs/roadmap.md
```

### “会话要中断了，工作状态还在聊天里”

写：

```text
docs/handoff/
```

---

# 53. 最终原则

项目文档的目的不是完整记录一切。

而是保证：

> 即使历史聊天全部消失，一个新的开发者或 Agent 依靠代码和项目文件，仍然能够确认当前系统、找到正在进行的工作、理解关键架构决策、复用历史排障知识，并安全地继续开发。

长期保持：

1. **代码是现实。**

2. **Project 是当前事实。**

3. **Task 是当前工作。**

4. **Issue 是问题知识。**

5. **Architecture 是架构历史。**

6. **Roadmap 是未来阶段。**

7. **重要变化必须经过 Checkpoint 收敛。**

8. **Task 完成前必须完成 Documentation Sync Check。**

9. **历史不重写，当前事实必须更新。**

10. **文档必须降低认知成本，而不是制造新的维护负担。**

---

# 54. 个人知识库集成

## 54.1 定义

用户（项目所有者）维护一个跨项目的**个人知识库**，路径：`F:\Project\Knowledge`。

它记录用户的个人视角：每天做了什么、每个项目进行到哪、踩过的坑、学到的知识。

个人知识库与本标准管理的项目文档体系**完全独立**：

| 体系 | 回答的问题 | 归属 |
|---|---|---|
| 项目文档（本标准） | 系统的事实 | 项目仓库，可交接 |
| 个人知识库 | 用户个人的记忆与学习 | 用户个人，跨项目 |

两者不得互相替代，不得互相复制，不得混淆触发。

## 54.2 初始化职责

初始化或重构项目文档体系（生成 `AGENTS.md`）时，在 `AGENTS.md` 末尾追加
54.3 的标准片段，并将 `<项目名>` 替换为当前项目的 git remote 仓库名或根目录名。

个人知识库的完整操作规则（写入格式、目录结构、模板、边界）只维护在
`F:\Project\Knowledge\AGENTS.md` 中。项目 `AGENTS.md` 不复制完整规则，只保留入口。

## 54.3 项目 AGENTS.md 标准片段

将以下内容追加到项目 `AGENTS.md` 末尾：

````markdown
## 个人知识库（Personal Knowledge Vault）

用户维护一个跨项目的个人知识库，路径：`F:\Project\Knowledge`

它记录用户的个人视角：每天做了什么、每个项目进行到哪、踩过的坑、学到的知识。
**它与本项目 `docs/` 文档体系完全独立，两者不得混淆。**

### 唯一触发规则

**仅当用户消息中明确提到「个人知识库」时，才操作它。**
未提到时，一律只按本项目 V2 文档体系工作，不触碰知识库。

- 更新意图（"更新一下个人知识库" / "收工，更新个人知识库"）：
  先读 `F:\Project\Knowledge\AGENTS.md`，按其更新流程执行——
  新建事件文件 `Projects/<项目名>/YYYY-MM-DD_<事件>.md`（详细记录），
  更新枢纽 `Projects/<项目名>/<项目名>.md`（Resume Here 必更新、进展索引加一行链接，按需更新踩坑、决策、速查区）
  和 `Daily/<今天>.md`，必要时在 `Knowledge/` 建笔记。
  完成后向用户汇报更新了哪些文件。
- 查询意图（"个人知识库里这个项目做到哪了"）：读取并汇报，不写入。

### 对应位置

本项目在知识库中对应：`Projects/<项目名>/`（文件夹，枢纽 + 事件文件两层）
（不存在时按知识库 `Templates/project.md` 建枢纽、`Templates/event.md` 建事件文件）

### 边界

- 项目事实唯一来源仍是本项目 `docs/`；知识库只写个人视角摘要，引用项目内路径，不复制全文
- 禁止写入密码、API Key、Token 等敏感信息
- 只记录有学习/恢复上下文价值的内容，不写流水账
````

## 54.4 触发隔离

个人知识库的唯一触发条件是用户消息中明确提到「个人知识库」。

项目文档操作（Task / Issue / Architecture 更新等）**不触发**知识库；
知识库操作也**不代替**项目文档义务——两者各自该完成的同步各自完成。

## 54.5 单一事实来源延伸

- 项目事实唯一来源仍是项目 `docs/`。
- 个人知识库不得作为项目事实来源，不得影响项目交接。
- 个人知识库只保存：摘要、链接、个人学习视角。
- 知识库的完整操作规则只维护在知识库自身的 `AGENTS.md` 中；项目侧不复制。

## 54.6 边界

- 个人知识库内容不得包含敏感信息（同第 40 条）。
- 个人知识库丢失或不可用，不影响项目完整性和交接。

---

# 55. `operations.md`（运行操作手册）

## 55.1 定位

`docs/project/operations.md` 属于当前事实层，回答：

> 这个项目实际怎么运行、怎么操作、东西都放在哪？

它为以下场景服务：

- 新 Agent / 新开发者进入项目后快速上手操作；
- 隔一段时间回来后回忆部署、运维方式；
- 复盘时还原"当时是怎么跑的"。

`setup.md` 回答"环境怎么搭"（装什么、什么版本）；
`operations.md` 回答"系统怎么跑"（服务器、部署、常用命令、配置、路径）。
两者内容不得重复。

## 55.2 内容范围

记录**当前真实有效**的操作事实：

- 服务器 / 部署目标（地址、用途、连接方式）；
- 部署方式与进程管理（Docker / systemd / pm2 / CI 流水线）；
- 常用命令（Docker、Linux 运维、项目脚本），每条命令附用途；
- 配置文件（位置、作用、关键键名，**不复制文件全文**）；
- 关键路径与目录（代码、数据、日志在哪）；
- 端口与网络。

规则：

- 只记录当前有效的内容；命令、路径、服务器失效时**直接删除**，历史通过 Issue / Architecture 保留；
- 所有命令必须真实验证过，禁止凭记忆或猜测编写；
- 不记录密码、密钥（只写"见 `.env` 的 XXX 键"或密钥管理方式）；
- 不复制完整配置文件，只记录位置与关键键。

## 55.3 模板

````markdown
# 运行操作手册

## 服务器与部署目标

| 名称 | 地址 | 用途 | 连接方式 | 备注 |
|---|---|---|---|---|

## 部署与进程

- 部署方式：
- 进程管理：
- 常用操作：

```bash
# 启动 / 停止 / 重启 / 查看状态
```

## 关键路径

| 路径 | 内容 |
|---|---|

## 配置文件

| 文件 | 作用 | 关键键 | 备注 |
|---|---|---|---|

## 常用命令

### Docker

```bash
# 命令 — 用途
```

### Linux / 运维

```bash
# 命令 — 用途
```

### 项目脚本

```bash
# 命令 — 用途
```

## 端口与网络

| 端口 | 服务 | 备注 |
|---|---|---|
````

## 55.4 同步触发

以下变化发生时必须更新 `operations.md`（见第 41 章触发矩阵）：

- 新增 / 更换服务器或部署目标；
- 部署方式、进程管理方式变化；
- 常用命令变化（新增运维手段、脚本入口变化）；
- 配置文件位置或关键键变化；
- 关键路径 / 端口变化。

与个人知识库的关系：项目 `operations.md` 是**完整事实版**（可交接）；
个人知识库 `Projects/<项目名>/<项目名>.md` 的「速查」区是**个人速查版**（只记用户常用、常忘的），
引用本项目路径，不复制全文。

---

# 56. Handoff（会话交接文档）

## 56.1 定义与定位

Handoff 表示：

> 一次会话结束前，向下一次会话移交的**临时工作状态快照**。

它回答：

> 会话中断了，还有哪些没进正式文档的工作状态？下一个会话从哪里接？

定位：

- Handoff 是**会话之间的过渡载体**，不属于第 0 章五类长期文档中的任何一类；
- 它只保存"目前仍只存在于聊天上下文、尚未收敛进 Task / Issue / Architecture / `docs/project/` 的工作状态"；
- 消费后删除，不长期保留，不参与沉淀。

与 Task「交接」区的关系：

- Task 的「交接」区是任务级交接，随 Task 长期保留；
- Handoff 是会话级交接，消费后删除；
- Handoff 引用 Task / Issue / Architecture 的 ID 与路径，不复制其内容。

## 56.2 何时生成

触发条件（任一满足）：

- 用户明确要求交接（"生成交接文档" / "写个 handoff" / "准备换窗口，整理一下上下文"）；
- 会话过长，上下文即将压缩或耗尽，且当前工作未到可停点；
- 用户准备关闭会话 / 更换模型 / 更换 Agent，且工作未完成。

生成前必须先执行一次 Level 1 Task Checkpoint（第 32 章）：

> 该进 Task / Issue 的内容先进正式文档，剩下的临时状态才写进 Handoff。

禁止把本该写进 Task 的执行记录、验证结果只留在 Handoff 里。

## 56.3 存放与命名

```text
docs/handoff/HANDOFF-YYYYMMDD-NN-简短名称.md
```

例如：

```text
docs/handoff/HANDOFF-20260917-01-Agent调度器重构中断.md
```

同一天多份，NN 依次递增。

目录在第一份交接文档生成时创建，不预先创建空目录（见 1.4）。

## 56.4 模板

````markdown
---
id: HANDOFF-20260917-01
type: handoff
title: Agent 调度器重构中断
status: active
created: 2026-09-17 17:40
related_task: TASK-20260917-02
---

# HANDOFF-20260917-01 Agent 调度器重构中断

## 这次会话在做什么

- 关联 Task / Issue / Architecture：
- 任务目标（一句话）：

## 已完成

按时间顺序列出：改了什么文件、行为变化、验证到什么程度。

## 中断点（正在进行的）

- 中断时正在做什么：
- 做到哪一步：
- 涉及文件的当前状态（可运行 / 半成品 / 已回滚）：
- 运行中的服务与临时环境（dev server、容器、调试进程，如何停止 / 重启）：

## 尚未进正式文档的关键信息

本会话得出但还没写进 Task / Issue / Architecture 的结论、根因、约束、踩坑。

## 用户的关键指示

逐条转述本会话中影响后续工作的用户要求与偏好。

## 下一步

按优先级列出，每步写到"打开哪个文件、做什么、怎么算完成"。

## 未决问题与风险

## 验证状态

- 已验证（命令与结果）：
- 未验证（内容与原因）：

## 接手会话先做什么

1. 读 `AGENTS.md` 与关联 Task；
2. `git status` / `git diff` 核对本文件与实际代码是否一致；
3. 有出入以代码为准（见 1.1），先更新本文件再继续。
````

## 56.5 接手流程

新会话按以下顺序恢复：

1. 读 `AGENTS.md`；
2. 读 `docs/handoff/` 中最新且 `status: active` 的 Handoff；存在多份 active 时以编号最新的一份为准，其余检查后清理；
3. 按 Handoff 指引读取关联 Task / Issue / Architecture；
4. 用 `git status`、`git diff` 和实际代码核对 Handoff 描述与代码是否一致；
5. 有出入时以代码为准，先修正 Handoff 再继续；
6. 执行 Handoff 的「下一步」。

恢复完成后的第一件文档工作：

> 把 Handoff 中具有长期价值的内容沉淀进正式文档（Task / Issue / Architecture / `docs/project/`），然后把该 Handoff 改为 `status: consumed` 并删除文件。

## 56.6 生命周期

```text
生成 → 被新会话接手 → 沉淀有价值内容 → 删除
```

- 正常情况下 `docs/handoff/` 同时最多存在 1~2 份 active；
- 超过一周仍未消费的 Handoff：内容已失效的直接删除，仍有价值的先沉淀再删除；
- 删除即可，Git 历史自然留痕，无需归档；
- 不维护 Handoff 索引文件，直接看文件名。

## 56.7 边界与禁止

- Handoff 不是项目事实来源：与代码或正式文档冲突时以后者为准；
- 不复制大段源码，只写文件路径与符号名；
- 不写敏感信息（同第 40 条）；
- 不用 Handoff 替代 Task Checkpoint 与文档同步义务；
- 会话正常结束且工作已收敛时，不生成 Handoff。

## 56.8 项目 AGENTS.md 标准片段

按第 47 章初始化项目、生成或完善 `AGENTS.md` 时，必须把以下片段写入 `AGENTS.md`（放在交接 / 工作流程相关章节附近）：

````markdown
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
````
