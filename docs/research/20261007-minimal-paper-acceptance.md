# 最小可启动模拟盘验收报告 (TASK-58)

## 1. 启动与运行命令

### 1.1 命令行单步 / 有界循环运行
```bash
# 启动 T0 最小模拟盘单次决策与撮合轮询（标的：513050.SH，虚拟本金：500元）
python -m trademind.runner t0-paper --instrument 513050.SH --steps 1

# 检查当前系统与双模型状态
python -m trademind.runner status
```

### 1.2 Web 可视化工作台启动
```bash
# 启动本地 FastAPI 工作台（默认地址：http://127.0.0.1:8321）
python -m trademind.runner serve
```
启动后可访问：
- `GET /`：查看 Web 工作台页面（包含账户余额、持仓、订单状态、最近决策链以及 Kill Switch 操作）；
- `GET /api/status`：包含 `t0` 状态、`llm_model`（deepseek-flash）、`jev_provider`、`llm_budget`、`orchestrator`；
- `GET /api/account`：实时查询 `paper-t0-500` 账户权益、可用现金与持仓；
- `GET /api/orders`：查看最近委托及状态；
- `GET /api/decisions`：查看决策链。

---

## 2. 关键工程检查与双模型决策链闭环

1. **账户隔离与资金约束**：
   - 账户标识：`paper-t0-500`；
   - 初始资金：500.00 CNY；
   - 硬风控边界：最大单标的暴露 200.00 CNY，最大总持仓暴露 300.00 CNY，最低现金预留 100.00 CNY；
   - 止损硬线：单笔 0.8%，单日最大亏损 3%，单日最多 10 笔开仓，14:50 之后禁止新开仓；
   - 14:55 强制清仓退出。

2. **双模型决策链（deepseek-flash + TypeSafe JEV）**：
   - 候选分析：`deepseek-flash` 输入可见快照，输出 Trend / Risk / Critic 三维度分析及候选动作；
   - 短路保护：WAIT / HOLD 候选直接短路生成意图，不进入 JEV，不冻结资金；
   - JEV 审核：BUY / SELL 候选通过 TypeSafe JEV 官方协议（`POST /v1/systemone`）审查窄题目（`entry_suitable`, `adverse_move`, `evidence_sufficient`）；
   - 降级保护：若 JEV 报错、超时或不可用，强制降级为 WAIT，杜绝未经审核的风险进场；
   - 意图综合：JEV 通过后，`deepseek-flash` 结合候选与审查意见生成最终 `TradeIntent`。

3. **撮合与清算（OMS & PaperBroker）**：
   - 分钟行情保守撮合：仅在订单提交后可见的未来分钟报价撮合，禁止未来数据穿透；
   - T+0 当日可卖：标的 `513050.SH`（中概互联50ETF）日内买入即时可用，允许日内部分或全部平仓；
   - T+1 股票保护：普通 A 股（如 `600519.SH`）当日买入股份锁定，当日卖出被 Guard 严格否决；
   - 费用计算：ETF 零印花税，精确扣除券商佣金与过户费。

---

## 3. 边界与限制

- 仅运行在 `paper` 模拟模式，`live` 模式由 Guard 强制否决；
- 无外部真实充值与外部盈利承诺；
- API Key 严格从环境变量与本地 `.env` 读取，任何命令输出与日志均做脱敏处理。
