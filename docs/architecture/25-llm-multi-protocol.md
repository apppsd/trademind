---
id: ARCH-025
type: architecture-change
title: LLM 接入层多协议支持（Anthropic Messages / OpenAI Chat / OpenAI Responses / Gemini generateContent）
status: implemented
created: 2026-10-21
updated: 2026-10-21
related_tasks: [TASK-20261021-02]
recorded_retrospectively: false
---

# LLM 接入层多协议支持

## 动机

LLM 接入此前仅支持 DeepSeek 官方的 OpenAI 兼容 chat completions 线协议。用户经中转站使用模型时，同一模型可能以 Anthropic Messages、OpenAI Responses、Gemini generateContent 等线协议暴露，需要按协议适配。

## 方案

- 新增 `models/protocols.py` 纯适配层：每协议提供请求构造（URL / 认证头 / payload）与响应解析，输出归一的 `ProtocolReply`（text / finish_reason / refusal / usage tokens / 诊断字段 / 结构错误）。共享管线（熔断、预算 reserve/reconcile、一次 JSON 修复轮、拒绝/截断诊断、审计）不变。
- 支持协议（`llm.protocol`，默认 `openai_chat` = 原行为）：
  - `openai_chat`：`{base}/chat/completions`，Bearer；DeepSeek 专有字段（thinking / response_format）仅此协议发送。
  - `anthropic_messages`：`{base}/v1/messages`，`x-api-key` + `anthropic-version: 2023-06-01`；`max_tokens` 必填（无预算上界时默认 4096）；`stop_reason` 映射（end_turn→stop、max_tokens→length、refusal→content_filter）。
  - `openai_responses`：`{base}/responses`，Bearer；`max_output_tokens`；`output[].content[type=output_text]` 取文本；status incomplete/failed 归一。
  - `gemini_generate_content`：`{base}/v1beta/models/{model}:generateContent`，`x-goog-api-key`；`systemInstruction`/`generationConfig`；finishReason 映射（MAX_TOKENS→length、SAFETY/BLOCKLIST/PROHIBITED_CONTENT/SPII/RECITATION→content_filter）。
- base_url 解析：未设置或仍为 DeepSeek 默认时按协议取官方默认端点；显式自定义（中转）URL 原样保留。
- 配置面：`[llm] protocol` + env `TRADEMIND_LLM_PROTOCOL` + 管理页 `llm_protocol`（枚举校验，沿用 ARCH-024 机制与优先级）。
- provider 类泛化为 `MultiProtocolLLMProvider`，`DeepSeekProvider` 保留为别名（既有 isinstance 与调用不受影响）；审计 provider 枚举不变，线协议记入 `call.model_params.protocol`。
- 模型锁定不变：T0 仍强制 `llm.model='deepseek-flash'`；协议与型号正交，不构成静默换型号。JEV TypeSafe 官方协议不动。

## 影响面

- `config.py`：新字段与校验；`models/protocols.py` 新模块；`models/deepseek.py` 内部重构（对外接口兼容）；factory/runner 传参；管理页说明；诊断白名单不扩。
- 安全边界：无新写入口；管理页新增项走既有令牌门禁；协议名非密钥、明文回显。
- 全部验证离线 mock，不产生真实付费调用。

## 实施证据（2026-10-21，同日实施）

- `models/protocols.py`：`VALID_LLM_PROTOCOLS`（config.py 定义）、`PROTOCOL_DEFAULT_BASE_URL`、`effective_base_url`（DeepSeek 默认/空 → 协议官方端点；自定义 URL 原样）、`session_headers`（Bearer / x-api-key+anthropic-version 2023-06-01 / x-goog-api-key）、`build_request`（openai_chat 保留 thinking/response_format；anthropic max_tokens 必填缺省 4096；responses 用 instructions/max_output_tokens；gemini 用 systemInstruction/generationConfig、assistant→model 角色映射）、`parse_response` → `ProtocolReply`（text/finish_reason/refusal/usage tokens 归一；anthropic stop_reason、responses incomplete/failed、gemini finishReason 与 promptFeedback.blockReason 均映射；诊断键不扩 SAFE_DIAGNOSTIC_KEYS）、`probe_request`（gemini 剥 `models/` 前缀）。
- `models/deepseek.py`：类泛化为 `MultiProtocolLLMProvider`（`DeepSeekProvider = MultiProtocolLLMProvider` 别名，runner/prompt_experiment isinstance 与既有调用不受影响）；共享熔断/预算 reserve/reconcile/一次修复轮/拒绝与截断诊断/审计管线不变；`call.model_params` 增记 `protocol`；probe 按协议取模型列表。
- `config.py`：`LLMConfig.protocol`（默认 openai_chat）、`_validate_llm_protocol`（在 env/管理覆盖之后校验）、env `TRADEMIND_LLM_PROTOCOL`、管理白名单新增 `llm_protocol`（enum 校验，400 拒绝非枚举值）、`describe_admin_settings` 返回 choices；`provider_factory` / `runner` 探针传 protocol。
- 工作台：管理员设置说明补充四协议与 base_url 解析规则。
- 测试：`tests/test_llm_protocols.py` 27 项（四协议 ok/usage 归一、请求形状与认证头、14 组失败分类参数化、anthropic 修复轮 history、base_url 解析、gemini probe、config/env/factory）+ `test_admin_settings.py` 新增 1 项（llm_protocol 枚举与应用）全过；全量 **672 passed / 0 failed**（基线 644 + 新增 28）；openai_chat 既有全部测试（含 DailyModelBudget 路径与失败诊断断言）不改一行通过。
- 边界：四协议均为离线 mock HTTP 验证；真实官方端点或中转站调用未做（不经授权不调用收费 API）。
