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
