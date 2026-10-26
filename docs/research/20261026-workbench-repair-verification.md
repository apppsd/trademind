# 工作台V2返修验证（2026-10-26）

关联：[Task26-16](../tasks/TASK-20261026-16-修复工作台V2独立复核缺陷.md)。依据[独立复核](20261026-workbench-independent-review.md)增量修复；没有覆盖既有未提交工作，没有commit/push、实盘、收费模型或真实行情请求。

## 修复结果

| 范围 | 实际修改与行为 | 回归证据 |
|---|---|---|
| 账户与只读接口 | request.app.state绑定显式cfg，未配置返回503；不再全局默认load_config/凭据回退。Context按portfolio.account_id过滤，日报移除父目录静态挂载并检查account_id | 两个独立应用无需patch加载器；GET不创建数据库；跨账户Context与文件URL不能读取 |
| 曲线 | 接入生产minute_bars.time、兼容bar_time；UTC瞬间比较as_of/available_at，过滤未来修订；统一信封包含generated_at；缺口/不完整桶显示null，成交量单位为份 | 真实生产字段形状、偏移时区、未来修订、全部周期；实际TS客户端接隔离HTTP成功接受1m/5m/15m/分时 |
| 财务 | 现金由全历史事件重建期初，包含当日截至查询时点的事件；正确读取settled_estimate/pending_reservation和reserved_cash；未知权益不画0 | 1000−100.10=899.90；0.197正确读出；未来入金不提前计入；null与真0分别处理 |
| 记录 | 统一时间/rowid复合游标，游标带类型与账户；Fill使用fill_id并补订单侧标的/方向；决策ID包含版本且兼容旧详情ID；历史events先筛范围后截断 | 随机ID分页不漏、真实Schema日期筛选、成交正确详情、超过200条新记录不挤掉历史范围事件 |
| observer | CLI真实分发；配置load_credentials=False；启动身份含账户/存储/地址/展示约束摘要及每次启动实例ID；收养后纳入状态与停止。实例停止文件不向磁盘恢复PID发信号；Windows自有子进程优先服务退出，必要时仅结束Popen仍持有句柄的进程树 | 实际CLI+HTTP+心跳；普通启动与收养两条停止路径；observer与持有写锁的真实paper Pipeline同账户并存，第二writer被拒绝；GET/停止不增加账本或模型预算、不解除halted |
| 刷新 | cancel递增世代、允许新请求立即开始；全部页面转发signal，卸载取消；超时覆盖headers和正文，已取消信号不发请求；按原始as_of自动变陈旧 | 挂载行情页A→B立即请求，迟到A不覆盖；scope卸载abort；悬挂正文timeout；成功重复读取旧快照仍为stale |
| 中文与阅读 | 策略使用统一中文名称，已知名称默认隐藏代码、详情保留复制；条件显示中文字段/单位/阈值/历史实际值与未知状态；当时值明确来自生成前最近留存Context，不冒充确证模型输入；状态检查缓存/替换/失效/过期，不能确认时显示未知 | 中文组件、未知退化、T+1排除、限额诊断、历史Context不被当前价格回填 |
| 能力与交互 | 不以接口成功宣称真实行情或正常交易；心跳年龄/PID决定实际进程活跃；资金诊断复用离线工具计费用/储备/敞口与在途单。补回到最新、秒级事件对应分钟桶、已有节点的记录时间线 | 心跳过期不因文件mtime新而洗新；资金上限200挡住一手300；首屏模式测试及事件缺口锚定测试 |
| 打包 | 静态包重建；补wheel的ETF白名单及源码/安装路径定位。market/profiles仅改资源定位，不改规则、费率或交易逻辑 | 离线uv构建，在独立临时venv安装wheel并在仓库外以-I运行：中文目录/ETF规则/页面与assets/只读API通过，不创建业务库 |

## 验证命令与结果

- 全量Python：`.venv/Scripts/python.exe -m pytest -q -o addopts='' --basetemp data/runtime/task26-16-release-final -o cache_dir=data/runtime/task26-16-cache`。最终744 passed / 0 failed，67.08秒；既有Starlette弃用提醒1条，不忽略失败。
- 新增后端回归：`tests/test_workbench_contracts.py`，18例（含普通/收养真实子进程两例），全部使用临时账户/库，模型与行情为测试替身。
- 前端：`cd frontend`后`npm test`，53 passed；`npm run build`含vue-tsc与生产构建通过。新增17例返修回归，路由拆成7个真实懒加载导航用例，单项仍保留原5000ms超时；没有提高超时或删除断言。
- 隔离HTTP：18326端口的合成验收账户，页面与10个只读端点200；实际TS client接入，88根1m、18根5m、6根15m与分时均接受，费用序列0.197正确。此为合成数据契约验证，不是市场或收益证据。临时服务已停止。
- wheel：主venv没有pip/build，使用既有`uv build --wheel --offline --out-dir data/runtime/task26-16-wheel`及独立`task26-16-installed`环境，不修改主环境依赖；仓库外wheel冒烟通过。最终构建/重装复核通过，静态清单精确一致（31文件），包含observer_identity与ETF白名单；SHA256见Task收尾。
- 文档和差异检查：check_docs 0 errors / 36 warnings（Owner列表变化、提醒条数不变）；git diff --check通过。

## 发现后进一步纠正的边界

初次全量出现旧测试以__new__构造部分Supervisor、没有account_id而终态持久化报错；现在记录显式null仍保留halt。真实子进程测试也确认Windows venv启动器PID与服务PID不同：既验证服务登记身份，也修复普通停止路径，避免只终止启动器留下服务。

前端首次按原一个测试顺序导入全部页面，重负载时仍出现5000ms累计超时；ChartPanel单独隔离不足以消除这个缺陷。因此改成逐路由独立导航验证，覆盖增加到7页，每条仍有原超时。主流程没有靠重试后偶然全绿收口。

## 完成边界与剩余验收

代码缺陷已修复，后端/请求层可按工程证据关闭。Task26-14及涉及真实视觉验收的任务继续in_progress，ARCH-027保持implementing；Issue26-04/05/09保留open，代码修复记录与仍未满足的关闭项分别写清。

本次浏览器工具返回apps=[]、browsers=[]，不能取得新Canvas截图或验证实际1920×1080交互/缩放/回退。组件挂载和HTTP契约不能冒充这些证据，原有错误截图不沿用。多标的比较、图片导出、CSV仍是既有可选延期；回到最新与已保存节点时间线已补代码。

现有8321服务未重启，Python后端不会自动加载本轮修复；必须在后续获授权的既有停启流程中切换，再验证本地千元账户与有数据页面。本轮未读取其凭据或改变真实账本/风控/模型配置。真实盘中来源、真实模型输出、真实买卖闭环、五日观察、盈利能力仍未验。
