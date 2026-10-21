# 2026-10-19 结果标签候选门禁独立复验

关联：Task19-08；实施Owner：Task19-02 / Claude Code。结果：**工程复验未通过，复用Issue19-01，不新建重复缺陷**。

## 受测范围与方法
相对审查游标仅src/trademind/memory/memory.py与tests/test_outcome_maturity.py业务版本变化。使用临时CSV、内存SQLite、实际FixtureAdapter/DatasetRouter、合成已确认交易日历与可注入时钟。登记10-12 10:00+08决策，N=5，终点10-19收110，次日收200，基价100、成本0.0015。禁止socket连接与.env/local凭据读取，未构造生产运行或真实模型请求。

## 实际候选结果（所有时刻Asia/Shanghai）
| 时刻 | maturity_candidates结果 | 解释 |
|---|---|---|
| 10-19 08:00 | 空 | 盘前原反例已修 |
| 10-19 09:30 | 含该决策 | 仍早于终点日收盘，未满足前次明确的收盘成熟边界 |
| 10-19 14:56 | 含该决策 | 同上 |
| 10-19 14:59:59.999999 | 空 | session gate已关闭 |
| 10-19 15:00 | 空 | 到收盘时刻反而排除 |
| 10-19 15:30 | 空 | 终点bar已发布，仍无法通过候选发现 |
| 10-20 08:00 | 空 | 过期未完成项次日盘前也排除 |

以上候选观察均在final写入前完成。另以同一登记独立调用mature：15:29:59.999999返回None并保持censored；15:30返回final=0.098500，未取次日200，说明发布/终点取值修复仍有效。候选入口与实际成熟能力出现时间错位。

根因：horizon仍是终点日07:00；新增is_session_open(now)把候选发现绑定了交易执行时段。交易开市不代表终点日收盘已发生；收盘/发布后不再开市也不代表结果不能成熟。现有新增测试恰好把09:30有候选、14:57后无候选写成预期，不能作为原验收通过证据。src未发现自动候选成熟调用方，不据此声称生产已漏标或当前交易受影响。

## 回归与版本
独立通过33项：tests/test_outcome_maturity.py、tests/test_reflection_boundaries.py、tests/test_memory_visibility.py、tests/test_etf_minute_data.py。通过受保护Python入口调用pytest.main，参数-q、临时--basetemp、-p no:cacheprovider；没有重复全量637或其他会话计数。

SHA256（测试前后稳定）：
- memory.py：33349b5e5d9015f5f27690beb22f4e037ae537b9d82a8ec9e4c44f95cd01ca8b
- test_outcome_maturity.py：069f79baec532c16988540f475a1dbff2fadae0977419828cbf0323dce02164f
- context/features.py：04152b6900156f388b10c4ee808b87628dda4765b772d7f17f5bc618096c2e16

## 相邻主题与监听登记
分钟特征实际入口context/features.py→compute_minute_features；调用方先过滤可见bars。纯内存四组控制：午休11:30→13:01、隔夜、缺1分钟均不产生ret_1m，regime=UNKNOWN；隔夜VWAP仅取新日100，不混旧日200；新连续6根100..105恢复ret_5m=0.050000。相关测试实际位于tests/test_etf_minute_data.py。无新缺陷证据，不创建实施任务。下一入口：OutcomeService读取日线是否把as_of传到修订归并前，先与Issue19-02范围比对，避免重复立项；本轮未验证此链。

Task19-07已认领并记录每小时:53/session-only，但本Task仍登记旧ID09d5605a，current-status却声明旧ID已删除、新IDac3ce4a0。只有首次12:53检查文字，尚无后续实际触发记录或本聊天可访问的执行端调度器结果。因此维持in_progress、Issue19-03 open，要求执行端统一有效ID、带偏移时间与后续触发证据；本聊天不擅自设置或联系该会话。审查端每小时调度保持原设置。

## 下一步与验收
Task19-02保留Claude Code/in_progress，Issue19-01重新open。限定原memory.py::OutcomeService与test_outcome_maturity.py：终点候选不早于收盘；已到期项收盘后/次日仍可发现，保持数据发布可见性及缺失不替代语义；补收盘前1微秒、收盘、发布前1微秒/发布及次日恢复控制，并同步原Task逐文件契约/版本兼容。不要用session open代替结果成熟边界。Task19-07登记问题由原Owner继续，不认定监听失效或虚构平台故障。

未验：真实执行端定时器、部署、真实库/日线、自动反馈链、完整模拟交易及五日观察。无业务实现、运行配置、预算或交易变更。
