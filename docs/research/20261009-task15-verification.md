# Task15 容器显式运行入口验证

2026-10-09北京时间22:53，选择Task验收允许的显式启动命令替代路径，不写被拒绝的持久代理配置。新增scripts/run_container.py，运行时从宿主已有HTTP(S)代理读取，限定无认证loopback HTTP代理，映射容器宿主网关，只传子进程环境，命令不含代理URL。无可用代理拒绝，未改compose/.env/系统环境；镜像仍不含真实代理地址或Key。

命令：`.venv/Scripts/python.exe scripts/run_container.py check` 免费检查；`... paper --steps 1` 有限paper默认无收费探针；明确加--probe-models才允许原双模型探针。入口限定1～720轮。paper先停workbench，结束/报错finally恢复；恢复失败返回失败。workbench本身仍是原服务，不宣称自动继承代理。

10个入口测试通过（0.05秒）；代理认证/非loopback/非法scheme拒绝、未写os.environ、命令不含地址、只读check不停止工作台、paper失败恢复及恢复失败不误报成功。真实check三ETF476/356/476行，source=sina_etf、末条15:00，夜间stale。有限paper正确NotFresh、无新增模型/订单，预算仍used10/probe10、现金500、持仓0；workbench恢复healthy。

同一容器代理条件JEV三题型HTTP200/423ms/usage367,61的既有证据见[运行连接报告](20261009-runtime-connectivity.md)。今日probe满，不重复请求；未来能力仍需真实进程探针。交付的是明确可重复运行入口，不是持久代理配置或盘中交易完成。Issue-20261009-03保留直连451及相关限制，不伪称已解决全部网络条件。
