# 提示示例与离线评测实施验证

2026-10-11；关联TASK-20261011-04/05/06、ARCH-010。当前宿主main，未提交推送。只做离线工程，新增模型调用/订单/交易为0；未读取.env、未部署或启动容器。

## 实际变化

- fast_prompts.py增加完整WAIT及有可卖仓位/亏损退出的配对输入/九字段输出示例；显式标为合成且不得复制示例价格。strategy-3生效于新宿主进程；JEV仍fast-2，四题与旧链不变。
- tests/fixtures/prompt_eval/cases.json：prompt-cases-1、24例、12类；标签区分允许/禁止建议与程序动作/最大订单数，含BUY/SELL正例。可选数据缺失与必需缺失分开；默认阈值与T0ExperimentConfig核对。
- evaluation/prompt_eval.py及scripts/evaluate_prompt_cases.py：只消费保存响应/程序观测，不初始化provider或账本；生产StrategyPlan校验、四题概率/BUY准入检查；分项JSON/Markdown报告、A/B输入/模型/配置一致性检查、原子单文件输出、输入覆盖保护。

报告区分模型危险建议与Guard实际放行；程序结果是输入的已保存观测，不是本工具重放OMS。固定案例的数据/标签版本和输入指纹必须匹配。响应一例一记录；重复传输尝试需分别形成评测批次，不能伪称请求账单总计。CLI不执行案例内任意断言脚本。

## 验证

已执行提示/数据/评测针对测试31项通过（包含原11项）；之后补CLI退出码/保护与覆盖统计修正，完整回归结果在下方最终检查记录。

CLI合成冒烟：

```text
.venv\Scripts\python.exe scripts/evaluate_prompt_cases.py --cases tests/fixtures/prompt_eval/cases.json --responses data/runtime/prompt-eval/synthetic-responses.json --output data/runtime/prompt-eval/synthetic-report.json
```

返回pass。生成器使用测试辅助bundle，响应/费用/token/程序观测全部为合成测试数据，不能当真实模型或OMS成绩。产物data/runtime/prompt-eval/synthetic-report.json及.md；修改报告字段后须重新生成。

关键负例：全WAIT不能通过BUY/SELL正例；危险SELL即使被程序拦住也记录建议失败；缺响应incomplete、请求失败fail、JSON错/四题缺失/越界概率fail；未知费用不记零；缺失响应纳入正例覆盖分母；重复订单、指纹错配被检出；网络调用陷阱验证无HTTP；输入不得被报告覆盖。单份JSON/Markdown文件各自原子替换，文件对不是跨文件事务。

## 真实证据边界

只读检查data/runtime/fast-archives现有两个采集bundle及一份comparison：全部incomplete_archives，contexts均0。archives包含旧/新组占位不等于有真实样本。Task17/原Task08依赖未满足，07/08继续pending；09条件优化继续后置。原盘中质量/模拟闭环/五日任务状态不变。

真实提示效果、增加示例后的实际token/费用/延迟、真实模型失败类别和时间留出结果均未验证。新容器镜像未重建，不能称容器运行strategy-3。

## 最终检查

完整回归：`.venv/Scripts/python.exe -m pytest -o addopts='' -q`，374 passed、1个既有Starlette弃用警告，19.91秒。CLI重新生成合成报告pass；不新增收费调用。文档当前事实/任务/架构已同步，setup无依赖部署变化、Release无发布不适用。
