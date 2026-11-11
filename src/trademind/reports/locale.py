"""Chinese presentation without changing protocol enums or archived evidence."""
import hashlib
import re
from datetime import datetime
from ..market.base import BEIJING

ACTIONS = {'BUY':'买入','SELL':'卖出','HOLD':'持有','WAIT':'观望'}
STATUS = {'ok':'成功','repaired':'格式修复后成功','refused':'拒绝','error':'错误','timeout':'超时','stub':'模拟桩'}
ROLES = {'strategy':'低频策略','flash_probe':'快速模型连通检查','jev_probe':'JEV连通检查',
         'jev':'即时决策','trader':'交易决策','risk':'风险审议','portfolio':'组合管理'}
FIELDS = {'close':'收盘价','vwap':'成交量加权均价','atr_14':'十四期平均真实波幅',
          'ret_1m':'一分钟收益率','ret_5m':'五分钟收益率','ret_15m':'十五分钟收益率','volume_ratio':'量比'}
OPERATORS = {'gt':'大于','gte':'大于或等于','lt':'小于','lte':'小于或等于','eq':'等于'}
INTRADAY_HEALTH_STATUS = (
    '等待开市',
    '行情不足',
    '有效策略观望',
    '策略请求失败退避',
    '预算限制',
    '持仓风险处理',
    '需要人工核对',
)
# Additive pre-decision blocker buckets (Issue30-01); they refine the two
# coarse tiers 行情不足/有效策略观望 without changing the seven health_status
# values or their priority order.
BLOCKER_CLASS_TEXT = {
    'data_reject': '行情数据拒绝',
    'async_pending': '异步模型结果待消费',
    'global_cooldown': '全局模型冷却',
    'strategy_wait': '策略观望',
    'unknown': '未知原因',
}
PRE_DECISION_BUCKETS = {
    'data_reject': '行情数据拒绝轮次',
    'model_pending': '异步模型等待轮次',
    # Review R1: audit rows with a missing/blank account_id or an unparseable
    # payload are never folded into the current account's denominators.
    'unknown_account:data_reject': '无法归属账户的行情数据拒绝轮次',
    'unknown_account:model_pending': '无法归属账户的异步等待轮次',
}
# Additive no-actionable exclusion classes (t279u item A); slugs match
# fast_state.action_gate and never replace the protocol reasons.
EXCLUSION_CLASSES = {
    'plan_disallowed': '计划未允许',
    'conditions_unmet': '条件未满足',
    'pending_order': '存在未成交订单',
    'cash_reserve': '现金储备不足',
    'no_position_or_qty': '无持仓或可卖数量',
    'unknown': '未知',
}
FAILURE_CATEGORIES = {
    'none': '无失败',
    'conservative_wait': '策略保守观望',
    'guard_veto': '硬风控否决',
    'explicit_refusal': '模型明确拒绝',
    'model_refused': '模型明确拒绝',
    'empty_output': '模型返回空内容',
    'length_truncated': '输出达到词元上限被截断',
    'reasoning_only': '仅返回推理内容无最终输出',
    'invalid_json': 'JSON格式非法',
    'invalid_strategy_json': '策略JSON字段集合不符合Schema约定',
    'invalid_strategy_chinese_text': '策略说明未按中文约定生成',
    'invalid_strategy_contract': '策略条件或持仓期限不符合合约约束',
    'schema_invalid': '策略结构或字段校验失败',
    'format_error': 'JSON或结构校验失败',
    'protocol_error': '响应协议结构错误',
    'rate_limited': '接口限流（429）',
    'http_error': 'HTTP请求状态异常',
    'network_error': '网络连接异常',
    'timeout': '模型请求超时',
    'budget_exhausted': '模型日预算耗尽',
    'budget_exceeded': '计费词元超过预留上限',
    'execution_error': '执行阶段异常',
    'unknown': '未知原因',
}
REASONS = {
    'paper new-entry observation only':'当前仅观察：最小手可达性或挂单条件未满足，详见诊断',
    'no actionable strategy conditions':'当前策略未满足可执行条件',
    'global model evaluation cooldown':'模型判断处于全局冷却期',
    'minimum lawful lot unaffordable':'可用资金不足以买入最小合法单位',
    'real model probe capability unverified':'当前进程尚未完成真实模型能力验证',
    'fast decision not eligible':'当前即时决策不符合执行条件',
    'no lawful increase size':'无法计算合法的新增买入数量',
    'no valid answer':'尚无有效模型回答',
    'expired plan or context':'策略计划或行情快照已经过期',
    'uncertain JEV evidence or plan':'证据不足或策略适用性不明确',
    'entry evidence insufficient':'买入依据不足',
    'expired during JEV request':'模型请求完成时策略已经过期',
    'typed JEV action with valid strategy and evidence':'模型动作符合题型约定，策略与证据有效',
    'JEV provider or answer validation failed':'模型接口或回答校验失败',
    'invalid fast-decision state':'即时决策状态不符合约定',
    'strategy refresh cooldown':'策略更新处于冷却期',
    'strategy model failure backoff':'策略请求连续失败，处于有界退避期',
    'shared model budget exhausted':'账户当日模型预算已达上限',
    'T0 daily model count/cost budget exhausted':'T0当日模型次数或费用预算已达上限',
    'pending order; no new model decision':'存在未成交订单，等待成交轮询，不发起新模型决策',
    'new-entry session cutoff':'已过新开仓截止时点，不发起新开仓决策',
    'new-risk hard limit reached':'已触发当日新开仓硬风控上限',
    'invalid strategy Chinese text':'策略说明未按中文约定生成',
    'invalid strategy JSON':'策略JSON字段集合不符合Schema约定',
    'invalid strategy contract':'策略条件或持仓期限不符合合约约束',
    'explicit model refusal':'模型明确返回拒绝信息',
    'content filter refusal':'输出触发内容过滤策略',
    'empty completion content':'模型返回空文本内容（未见明确拒绝）',
    'empty completion (refusal?)':'历史空输出记录（旧版曾记为疑似拒绝，需结合词元与结束原因核对）',
    'completion truncated at max_tokens (finish_reason=length)':'模型输出达到最大词元上限被截断',
    'empty completion content with reasoning_content only':'模型仅返回推理过程而未输出最终策略内容',
    'invalid JSON completion':'模型返回文本不是合法JSON对象',
    'protocol structure error: missing choices or message':'模型响应缺少choices或message协议字段',
    'protocol structure error: invalid response JSON':'模型响应体不是合法JSON对象',
    'protocol structure error: non-string message content':'模型响应content字段类型非法',
    'rate limited (429)':'模型接口触发限流（HTTP 429）',
    'model request HTTP failure':'模型HTTP请求连接失败',
    'model request timeout':'模型请求超时',
    # T0 minute-data gate subtypes (Issue30-01 observability). The merged
    # 'missing or stale visible minute context' stays mapped for historical
    # audit rows; current rounds raise the two distinct subtypes (review R2).
    'missing or stale visible minute context':'可见分钟行情缺失或超过新鲜度门禁',
    'missing visible minute bars':'as_of 时点缺少可见分钟K线',
    'stale visible minute context':'可见分钟行情超过新鲜度门禁',
    'minute reference quote quality validation failed':'分钟参考报价质量校验未通过',
    # Closed NotFresh whitelist constants (review R2 round 3): the public
    # reason strings no longer embed instrument ids or wrapped errors, so
    # each constant gets a fixed Chinese label.
    'no fresh visible minute quote':'as_of 时点无可见的新鲜分钟报价',
    'trading calendar coverage insufficient':'交易日历覆盖不足，无法确认参考价格',
    'no reference price':'无可用参考价格',
    'context as_of is in the future':'决策时点晚于当前时钟',
    'account contains future events; historical decisions require a fresh ReplayRunner':'账户存在晚于决策时点的事件；历史决策需要全新的 ReplayRunner',
    'unknown':'未知子因（未分类原因，原文不入报告）',
    'incomplete minute features':'分钟特征不完整（连续1分钟K线根数不足）',
    'async model result expired or account changed':'异步模型结果已过期或账户状态已变化',
    'async strategy conditions changed before execution':'异步结果执行前策略条件已变化',
    'model worker pending':'模型工作线程有待消费结果',
}


def failure_category_text(category: str | None) -> str:
    if not category:
        return FAILURE_CATEGORIES['unknown']
    return FAILURE_CATEGORIES.get(str(category), FAILURE_CATEGORIES['unknown'])


def explanation(text, translations=None):
    if text is None:
        return '未记录'
    text=str(text)
    if text in REASONS:
        return REASONS[text]
    if text.startswith('model request HTTP ') and text[19:].isdigit():
        return f"模型HTTP请求返回状态码 {text[19:]}"
    # Dynamic minute-data gate subtypes carry instrument names or numbers;
    # translate the stable prefixes instead of dropping them into the
    # untranslated fallback.
    if text.startswith('no fresh visible minute quote for '):
        return f"{text[len('no fresh visible minute quote for '):]} 无可见的最新鲜分钟报价"
    if text.startswith('latest bar is stale by '):
        return f"最新分钟K线已超龄：{text[len('latest bar is stale by '):]}"
    if text == 'no visible minute bars at as_of':
        return 'as_of 时点没有任何可见分钟K线'
    m=re.match(r'^needs (\d+) contiguous 1m bars, have (\d+)$',text)
    if m:
        return f"需要连续1分钟K线 {m.group(1)} 根，实际只有 {m.group(2)} 根"
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    if translations and digest in translations:
        return translations[digest]
    if re.search(r'[\u3400-\u9fff]',text):
        return text
    return '历史说明尚未中文化，原文保留在原始数据中；未自动调用模型翻译。'


def condition_text(conditions):
    if not conditions:
        return '无'
    return '；'.join(f"{FIELDS.get(c['field'],'其他特征')}{OPERATORS.get(c['operator'],'满足条件')}{c['value']}"
                    for c in conditions)


def time_text(value):
    if isinstance(value,str) and re.match(r'^\d{4}-\d{2}-\d{2}T',value):
        try:
            return datetime.fromisoformat(value).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
    return value
