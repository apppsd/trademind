---
id: ARCH-009
type: architecture-change
title: 模拟运行就绪门禁与真实对照档案
status: implementing
created: 2026-10-09
updated: 2026-10-09
related_tasks: [TASK-20261009-15, TASK-20261009-16, TASK-20261009-17, TASK-20261009-18, TASK-20261009-08, TASK-20261009-09]
supersedes: []
superseded_by: []
recorded_retrospectively: false
---

# 模拟运行就绪门禁与真实对照档案

## 状态与背景
implementing，Task15显式运行入口完成，Task16窗口工程推进；盘中与后续档案门禁尚未完成。双模型真实探针和ETF分钟连接已通过，容器网络尚依赖临时参数，免费数据盘中质量待验，真实共同档案未采集，付费探针目前先于市场门禁。Task09依赖Task08，而Task08缺档案；单独采集Task17解除该执行依赖问题。

## 目标与非目标
补齐可重复容器网络条件、先免费门禁再收费模型、真实档案采集及既有回放/校准。保留ARCH-006三方职责和单写账本；不重写核心交易引擎、不实盘、不采购数据、不承诺收益、不放宽资金/时效门禁。

## 变更前
compose只读挂载.env/local配置；JEV直连451，现有宿主代理临时命令成功。sina_etf独立source已实施。runner付费双模型探针在盘中/行情校验之前；compare_bundle_file只消费归档意图，旧新真实共同档案尚无。

## 方案与核心决策
15显式本地可选网络→16真实盘中质量→18复用runner就绪/有界入口；03按官方协议与费用验收收尾；17在共同真实行情采样上隔离前瞻采集旧新链→08按时间切分校准→09完整paper启动验收→原56五日观察。依赖只在Task frontmatter与任务索引维护。

免费启动门禁先检查配置/预算/时段/行情，再显式探针。capability只对实际进程有效，历史HTTP200不能当当前能力。网络配置遇审批拒绝记录原因，不换等效命令绕过；明确可用的运行参数与持久化剩余限制。

档案版本化，保存共同facts_hash及各组真实账户前态、计划/动作、开始/完成/available/valid_until、模型/Prompt/题目/规则、t0/risk/paper配置与成本语义。不保存完整AppConfig、Key、认证头；离线重放不联网补生成过去模型输出。输出缺档/未知费用/无交易，不改成成功。

## 模块与接口影响
compose/config网络入口、market adapter/quality/router、runner/check_fast_startup、evaluation/fast_comparison及必要审计导出；实际文件由任务认领确认。既有Schema优先复用；需要Schema变化先Checkpoint，明确迁移、所有权、恢复及兼容性。

## 数据流与所有权
只读行情→质量/时段/预算→本进程双模型→策略/动作→主线程Guard/OMS→独立paper账本；公共行情事实供隔离旧新链→脱敏档案→离线对照/校准。模型仍无订单权限，旧新账户/预算不互相污染。

## 部署、测试与风险
本机Rancher、单写账本、只读密钥挂载、有限运行时长及停止恢复。测试过期/未闭合/单位变更、配额不足禁止HTTP、异步未消费incomplete、导出脱敏、档案版本/完成时刻/账户隔离和网络失败。盘中完整性、缺少合法交易、小样本与费用估值均可能阻塞真实完成。

## 最终实施结果与当前事实同步
Task15显式入口、Task16窗口及Task18安全工程已实施。implemented需任务工程及真实相应证据、受影响system-overview/code-map/flows/setup同步；已实现的ARCH-007/008不回写规划为历史事实。ARCH-006全链真实验收仍由原Task09负责。

## 关联
[任务索引](../tasks/index.md) · [当前事实](../project/current-status.md) · [既有真实探针与分钟证据](../research/20261009-runtime-connectivity.md)

## Task15设计Checkpoint

运行入口选择scripts/run_container.py读取既有宿主代理、只用已允许的docker compose run显式参数，不写compose/.env网络配置。check只读；paper共用账本停workbench再启动并finally恢复。不宣称正常workbench服务继承代理。新入口代码/免费验证不绕过已满模型probe预算。其余16/17/18尚未实施。

## Task16工程Checkpoint

短周期窗口改为最新连续1m片段，跨日期/午休/缺分钟重新预热；VWAP限最新上海日期可见量额，缺成交额不伪造。分钟版本独立更新，日线保持兼容。308测试通过，盘中数据验收未完成，ARCH仍implementing。Task18/17未实施。

## Task18安全工程Checkpoint

已实现：免费费率/时段/组合探针预算/分钟特征门禁→同进程单次双探针→既有异步决策。逐次Pipeline缺指标禁止worker，但确定性风险退出仍优先。两次预算检查只读，provider仍持久预留；以既有单writer边界为前提，不新增队列/数据所有权或跨进程锁。启动ready不代表交易验收。321宿主回归、容器夜间零新增调用拒绝已验证；ARCH保持implementing，真实档案/盘中/完整收尾未完成。

## Task18收尾设计

运行结束先关闭本进程预算预约入口，允许已发送请求完成费用审计，拒绝随后策略重试/JEV新HTTP；等待至多35秒，未排空明确worker_active/incomplete。文件报告使用白名单字段和原子替换，禁止全量配置/请求头。容器外层设置总时限，超时或中断停止本次随机命名容器再恢复唯一workbench，禁止后台worker与工作台同时写账本。

## 跨日预算修复设计（2026-10-10）

预算每日计数/当日额度按上海日期重置，但替换当前coalesce行前将前日状态写入model-budget:{account}:history:{day}。Decimal预约值附预约日，结算回写该日当前/历史记录，不能把前日失败pending清零或将跨零点usage记到新日。只读snapshot不迁移或删除记录，不改变每日资金/调用上限。

## 2026-10-10工程Checkpoint

报告、停止收尾、跨日费用归档与共享采集工程已落地；旧/新账户独立预算/数据库，公共分钟冻结至采样可见时点，保留真实组账户上下文及原始行情哈希。完整证据见[收尾报告](../research/20261010-engineering-completion.md)。真实盘中质量及非空共同档案未验，状态保持implementing；不是已实现后的独立架构变化，不新增编号。

## Rancher VM显式运输路径

Windows桥接Hyper-V socket持续失败而VM引擎可用，本次Task18同一运行入口增加--via-rancher-vm，用rdctl shell docker-compose和/mnt盘符路径执行；代理只经子进程WSLENV传递，不持久配置。停止/恢复仍唯一账本writer规则。新Issue-20261010-02保持open记录原桥接未修复，替代路径真实闭市零新增调用与恢复工作台已验证。
