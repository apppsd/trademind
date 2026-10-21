# Task03 官方协议与费用收尾

现有官方 `/v1/systemone`、jev-1.13.0、instructions/criteria、三题型响应校验、概率/usage 的实现已接线。宿主/容器官方真实三题型 HTTP200、1015/423ms、usage367/61，证据见[连接报告](20261009-runtime-connectivity.md)。Task15运行入口提供既有宿主代理的临时容器参数；正常直连451限制未解除。

四参考费率、币种/汇率与当时来源见[费率报告](20261009-model-pricing.md)。预算前预约、usage后结算；没有usage的失败请求保持 pending。新增401/429/500/超时负例各仅一次HTTP、零订单、正预留；既有 tests/test_fast_decision.py 覆盖缺题/重复ID/异常类型/概率/usage。跨日预约/结算修复及测试见[收尾报告](20261010-engineering-completion.md)。

这些证据满足 Task03 的协议/认证/配置费用门禁范围；Task03 completed，Issue-20261007-12 和 Issue-20261008-02 resolved。未重复收费探针。费用是配置估值，不声称供应商实际扣款；真实行情/校准/完整交易/五日观察不随本任务完成。
