---
id: RESEARCH-20261007-02
type: research
title: 技术栈与供应商核验（TASK-05 / TASK-11）
status: verified-partial
created: 2026-10-07
updated: 2026-10-07
related_tasks: [TASK-20261007-05, TASK-20261007-11]
related_issues: [ISSUE-20261007-02]
---

# 技术栈与供应商核验（2026-10-07 夜间实施）

核验方式说明：官方文档 / PyPI 页面抓取（核验日期 2026-10-07），本机安装实测。
未核验项如实标注，未把 SDK 免费等同于数据接口免费。

## 已核验事实

| 项 | 结果 | 来源 | 核验方式 |
|---|---|---|---|
| Python | 3.12.10 本机可用，项目 requires-python >=3.12 | 本机 | 实测安装 venv |
| pydantic | 2.12.x（2.x API） | PyPI | pip 安装实测 |
| FastAPI / uvicorn | 0.123 / 0.40，Windows 可用 | PyPI | pip 安装实测 + TestClient 测试 |
| requests / httpx / pytest | 2.32+ / 0.28 / 8+，全部可用 | PyPI | pip 安装实测 |
| DeepSeek API | OpenAI 兼容；base `https://api.deepseek.com`（另有 /anthropic 格式）；当前模型名 **deepseek-flash**、**deepseek-v4-pro**；旧名 deepseek-v4-flash(-vision-exp) 已退役但仍被接受并按 Flash 计费 | api-docs.deepseek.com 官方首页 | WebFetch 官方文档 |
| akshare | 1.19.1，MIT，Python >=3.11 64位 | PyPI | WebFetch；SDK 本机可安装（pip 实测） |
| baostock | 0.9.4，BSD，A 股历史数据 | PyPI | WebFetch；SDK 本机可安装 |
| tushare | 1.4.29，BSD，需 token（免费分级权限） | PyPI | WebFetch；SDK 本机可安装 |

## 关键决策（V1 实施）

1. 语言/版本：Python 3.12 + pip + venv；`requirements.txt` 为锁文件（pip freeze 重生成），pyproject 声明直接依赖。
2. 存储：**SQLite（stdlib sqlite3, WAL）**。ARCH-001 建议 PostgreSQL 是否采纳→V1 否：单用户本地、单写者账本、事件溯源可重建；仓储层已按表+唯一键隔离，迁移到 PG 不改契约。此决策记入 ARCH-001 实施记录。
3. API/前端：FastAPI + 无构建静态单页（src/trademind/api/static/index.html）；不引入 Node 工具链。
4. LLM 接入：DeepSeek OpenAI 兼容 /chat/completions，默认 model=deepseek-flash（配置可覆盖）；`/models` 探针命令 `python -m trademind.runner probe-llm` 供用户回填密钥后复核。**capability_verified 仅在探针成功后置位。**
5. JEV：官方 endpoint/认证/题型无公开文档（旧对话描述不可核验）→ 只保留显式 stub 与 HttpJevProvider 脚手架（约定 POST /v1/judgments，标注 unverified）。用户填写 base_url+key 后仍须最小探针验证。
6. 数据路由：fixture 为离线主源（确定性生成脚本）；akshare→baostock→tushare 为可选实数据源，按 DatasetRouter 优先级降级，全部带 provider/backfill 标记。

## 未核验 / 限制

- DeepSeek 具体计费、限流阈值、上下文窗口数值：未核验（需密钥后实测）；代码按 429/超时/预算处理，不依赖具体数值。
- akshare/baostock/tushare 实际数据接口可用性与频次：未实测拉数（夜间实施未做真实行情调用）；TASK-08 的样本验证结论因此保持"fixture 主源"，真实接入结果与替身结果以 provider 字段区分。
- JEV 完全未核验（无来源）。
- 参考仓库（TradingAgents/FinMem/NautilusTrader 等）：本次未逐一 clone 固定 commit；V1 未复用其代码，仅保留需求文档中的借鉴边界（不拼接多仓库）。后续启动扩展任务时再核验。
