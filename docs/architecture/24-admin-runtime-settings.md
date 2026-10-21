---
id: ARCH-024
type: architecture-change
title: 工作台管理员运行时设置（密钥/服务地址）
status: implemented
created: 2026-10-21
updated: 2026-10-21
related_tasks: [TASK-20261021-01]
recorded_retrospectively: false
---

# 工作台管理员运行时设置（密钥/服务地址）

## 动机

密钥等运行参数此前只能手工编辑 `.env`（或环境变量）。用户要求在工作台前端提供管理员页面直接配置，减少手工文件操作与出错面。

## 方案

- 新增白名单式运行时设置存储：`data/runtime/admin_settings.json`（gitignored；容器内位于可写 bind 挂载）。原子写入，`schema_version=1`。
- 设置项白名单（TASK-20261021-01 冻结）：`deepseek_api_key`、`llm_base_url`、`jev_api_key`、`jev_base_url`、`tushare_token`。**不含**模型型号（型号锁定约束）与管理令牌自身（避免自锁）。
- 凭据来源优先级：显式进程环境变量 > 管理页覆盖 > `.env` > TOML。管理页覆盖仅在 `load_config(load_credentials=True)` 时应用，replay/shadow 隔离路径凭据保持为空。
- API：`GET/PUT/DELETE /api/admin/settings[/{key}]`，全部沿用 `require_token`（无令牌 403 禁用、错令牌 401）；密钥仅以掩码形式返回（`****`+末4位），完整值不回传、不落日志。
- 生效时机：保存后需重启 serve/auto 进程（v1 不做热重载）；前端明确提示。

## 影响面

- 持久化新增一个运行时 JSON 文件（不建新表，不动账本）。
- `config.py` 新增覆盖应用逻辑（`apply_admin_overrides`）；`api/app.py` 新增 3 端点；工作台新增管理区块。
- 安全边界：新增的写入口复用既有控制令牌门禁；无令牌配置时整体禁用，与既有控制端点行为一致。

## 实施证据（2026-10-21，同日实施）

- `config.py`：`ADMIN_SETTING_SPECS` 白名单、`admin_settings_path()`（支持 `TRADEMIND_ADMIN_SETTINGS_PATH` 覆盖）、原子读写（tmp+replace）、`load/save/clear_admin_override`、`mask_setting`、`apply_admin_overrides`（仅 load_credentials 路径调用，进程环境变量遮蔽时跳过）、`describe_admin_settings`（待生效视图）。
- `api/app.py`：`GET/PUT/DELETE /api/admin/settings[/{key}]`，全部 `Depends(require_token)`；损坏的设置文件降级为空不阻塞启动。
- 工作台：`static/index.html` 新增「管理员设置」区块（保存/清除、来源中文标注、重启生效提示）。
- 测试：`tests/test_admin_settings.py` 8 项（403/401 门禁、掩码且完整密钥不出现在响应体、白名单/空值/控制符拒绝、覆盖应用与进程环境优先、load_config 隔离模式不应用、删除清除、掩码规则、损坏文件降级）全过；全量 **644 passed / 0 failed**（此前基线 636 + 新增 8）。
- 保存的覆盖值在列表中即时展示为待生效掩码值；进程环境变量遮蔽时来源如实标注为 env。
