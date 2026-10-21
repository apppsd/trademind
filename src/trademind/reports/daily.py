"""Daily CNY paper reports from a read-only SQLite snapshot; no market/model calls."""
import json
import re
import sqlite3
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from uuid import uuid4
import os

from ..market.base import BEIJING
from .locale import ACTIONS, STATUS, ROLES, explanation, condition_text, time_text


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite financial value')
    return result


def timestamp(value):
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError('timezone required')
    return at.astimezone(BEIJING)


def public_plan(plan):
    from ..execution.paper_run import sanitize_evidence
    keys = ('plan_id', 'instrument_id', 'model', 'prompt_version', 'created_at', 'available_at',
            'valid_until', 'trend', 'risk', 'critic', 'reason', 'allowed_actions',
            'entry_conditions', 'exit_conditions', 'invalidation_conditions', 'holding_horizon_seconds')
    def clean(value):
        if isinstance(value, str):
            return sanitize_evidence(value, 3000)
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value
    return {k: clean(plan[k]) for k in keys if k in plan}


def read_snapshot(database, account, window=None):
    """Read-only snapshot. With ``window=(start, stop)`` the model-call,
    context, intent and plan reads are bounded to rows near that Shanghai
    report day (TASK-20261013-16). Account events stay unbounded on purpose:
    opening books are replayed from the account's full event history and no
    verified boundary snapshot exists to truncate against."""
    path = Path(database).resolve()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table, query, args=()):
            return [dict(r) for r in conn.execute(query, args)] if table in names else []
        def bounds(column, days_back, days_fwd):
            if window is None:
                return '', ()
            start, stop = window
            lo = (start - timedelta(days=days_back)).astimezone(timezone.utc).isoformat()
            hi = (stop + timedelta(days=days_fwd)).astimezone(timezone.utc).isoformat()
            return f' AND {column}>=? AND {column}<=?', (lo, hi)
        # ±2-day margins absorb every stored offset form ('Z', '+08:00', …);
        # the exact Shanghai-day filtering stays in generate_report below.
        # Contexts keep a 7-day lookback so a position's latest-mark display
        # still resolves after a weekend-plus of no snapshots.
        calls_w = bounds('created_at', 2, 2)
        ctx_w = bounds('as_of', 7, 2)
        intent_w = bounds('created_at', 2, 2)
        if window is not None and 'created_at' not in {
                r[1] for r in conn.execute('PRAGMA table_info(intents)')}:
            intent_w = ('', ())  # legacy v1 file never migrated: read unbounded
        plan_w = bounds('available_at', 2, 2)
        return {
            'events': rows('account_events', 'SELECT payload_json FROM account_events WHERE account_id=? ORDER BY seq', (account,)),
            'calls': rows('model_calls', 'SELECT payload_json FROM model_calls WHERE 1=1'+calls_w[0], calls_w[1]),
            'budgets': rows('coalesce_state', 'SELECT key,meta_json FROM coalesce_state WHERE key=? OR substr(key,1,length(?))=?',
                            ('model-budget:'+account, 'model-budget:'+account+':history:', 'model-budget:'+account+':history:')),
            'contexts': rows('context_snapshots', 'SELECT payload_json FROM context_snapshots WHERE 1=1'+ctx_w[0], ctx_w[1]),
            'plans': rows('strategy_plan_history',
                          'SELECT payload_json FROM strategy_plan_history WHERE account_id=?'+plan_w[0]+' ORDER BY available_at',
                          (account,)+plan_w[1]),
            'cached': rows('coalesce_state', 'SELECT meta_json FROM coalesce_state WHERE substr(key,1,length(?))=?',
                           ('strategy-plan:'+account+':', 'strategy-plan:'+account+':')),
            'intents': rows('intents', 'SELECT payload_json FROM intents WHERE 1=1'+intent_w[0], intent_w[1]),
            'account_count': conn.execute('SELECT count(*) FROM account_state').fetchone()[0] if 'account_state' in names else 0,
        }
    finally:
        conn.close()


def generate_report(database, account, day=None, now=None):
    from ..execution.paper_run import sanitize_evidence
    now = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    day = date.fromisoformat(day) if isinstance(day, str) else (day or now.date())
    if day > now.date():
        raise ValueError('future report date')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}', account) or account in ('.', '..'):
        raise ValueError('unsafe account ID')
    start = datetime.combine(day, datetime.min.time(), BEIJING)
    stop = min(start + timedelta(days=1), now)
    data = read_snapshot(database, account, window=(start, stop))
    events = [json.loads(r['payload_json']) for r in data['events']]
    events = [e for e in events if timestamp(e['effective_at']) <= stop and
              (day == now.date() or timestamp(e['effective_at']) < stop)]
    warnings = []
    if not events:
        warnings.append('缺少账户事件历史，不能确认期初及盈亏。')
    supported = {'deposit', 'withdraw', 'reserve', 'release', 'fill_buy', 'fill_sell', 'rollover', 'fee'}
    if any(e['kind'] not in supported or e.get('currency', 'CNY') != 'CNY' for e in events):
        raise ValueError('unsupported account event or currency')
    opening = {'cash': Decimal(0), 'reserved': Decimal(0), 'positions': {}}
    state = deepcopy(opening)
    initial_id = None
    if events and not any(timestamp(e['effective_at']) < start for e in events) and events[0]['kind'] == 'deposit':
        initial_id = events[0]['seq']
    funding = realized = fees = Decimal(0)
    trades = []
    for event in events:
        at = timestamp(event['effective_at'])
        today = at >= start
        kind = event['kind']
        amount = decimal(event.get('amount') or 0)
        if kind == 'deposit':
            state['cash'] += amount
            if today and event['seq'] != initial_id:
                funding += amount
        elif kind == 'withdraw':
            state['cash'] -= amount
            if today:
                funding -= amount
        elif kind == 'reserve':
            state['reserved'] += amount
        elif kind == 'release':
            state['reserved'] -= amount
        elif kind == 'fee':
            state['cash'] -= amount
            if today:
                realized -= amount
                fees += amount
        elif kind in ('fill_buy', 'fill_sell'):
            parts = dict(p.split('=', 1) for p in event.get('note', '').split(';') if '=' in p)
            price, fee, qty = decimal(parts['price']), decimal(parts['fee']), decimal(event['quantity'])
            iid = event['instrument_id']
            lot = state['positions'].setdefault(iid, {'quantity': Decimal(0), 'avg_cost': Decimal(0)})
            if kind == 'fill_buy':
                state['cash'] -= price*qty + fee
                basis = lot['avg_cost']*lot['quantity'] + price*qty + fee
                lot['quantity'] += qty
                lot['avg_cost'] = (basis/lot['quantity']).quantize(Decimal('0.0001'))
            else:
                if qty > lot['quantity']:
                    raise ValueError('sell exceeds reconstructed position')
                profit = price*qty - fee - lot['avg_cost']*qty
                state['cash'] += price*qty - fee
                lot['quantity'] -= qty
                if today:
                    realized += profit
                if not lot['quantity']:
                    del state['positions'][iid]
            if today:
                fees += fee
                trades.append({'time': at.isoformat(), 'instrument_id': iid,
                               'side': 'BUY' if kind == 'fill_buy' else 'SELL',
                               'price': str(price), 'quantity': str(qty), 'notional': str(price*qty),
                               'fee': str(fee), 'cash_after_fill': str(state['cash']),
                               'order_id': event.get('order_id')})
        if at < start or event['seq'] == initial_id:
            opening = deepcopy(state)

    contexts = [json.loads(r['payload_json']) for r in data['contexts']]
    def valuation(snapshot, boundary, opening_mark=False):
        marks, positions = {}, []
        reference = min(boundary, boundary.replace(hour=15, minute=0, second=0, microsecond=0))
        for context in contexts:
            as_of = timestamp(context['as_of'])
            if as_of > boundary or (opening_mark and as_of >= boundary):
                continue
            iid = context['instrument_id']
            for bar in context.get('aux', {}).get('minute_bars', []):
                bt = timestamp(bar['time'])
                visible = timestamp(bar.get('available_at', context['as_of']))
                if bt > boundary or visible > boundary:
                    continue
                if iid not in marks or bt > marks[iid][0]:
                    marks[iid] = (bt, decimal(bar['close']))
        value = Decimal(0)
        complete = True
        for iid, lot in snapshot['positions'].items():
            mark = marks.get(iid)
            # Opening holdings require a verified prior-close snapshot: do not
            # silently substitute cost or an arbitrary older minute.
            usable = bool(mark and not opening_mark and mark[0].date() == day
                          and 0 <= (reference-mark[0]).total_seconds() <= 180)
            if not usable:
                complete = False
            market_value = lot['quantity']*mark[1] if usable else None
            if market_value is not None:
                value += market_value
            positions.append({'instrument_id': iid, 'quantity': str(lot['quantity']),
                              'avg_cost': str(lot['avg_cost']),
                              'last_price': str(mark[1]) if usable else None,
                              'price_time': mark[0].isoformat() if mark else None,
                              'market_value': str(market_value) if market_value is not None else None,
                              'unrealized_pnl': str(market_value-lot['avg_cost']*lot['quantity']) if usable else None})
        return {'cash': str(snapshot['cash']), 'reserved_cash': str(snapshot['reserved']),
                'available_cash': str(snapshot['cash']-snapshot['reserved']), 'positions': positions,
                'equity': str(snapshot['cash']+value) if complete and events else None,
                'valuation_complete': complete and bool(events)}
    beginning = valuation(opening, start, True)
    ending = valuation(state, stop)
    if not beginning['valuation_complete'] or not ending['valuation_complete']:
        warnings.append('缺少期初持仓估值或新鲜期末行情；总资产/当日总盈亏未确认，不能用成本价替代。')
    profit = decimal(ending['equity'])-decimal(beginning['equity'])-funding if beginning['equity'] is not None and ending['equity'] is not None else None

    calls = [json.loads(r['payload_json']) for r in data['calls']]
    calls = [c for c in calls if start <= timestamp(c['created_at']) <= stop and
             (day == now.date() or timestamp(c['created_at']) < stop)]
    budget = next((json.loads(r['meta_json']) for r in data['budgets']
                   if json.loads(r['meta_json']).get('day') == day.isoformat()), None)
    known = sum((decimal(c['cost_amount']) for c in calls if c.get('cost_amount') is not None and c.get('cost_currency') == 'CNY'), Decimal(0))
    missing = sum(c.get('cost_amount') is None or c.get('cost_currency') != 'CNY' for c in calls)
    scoped = data['account_count'] == 1
    settled = decimal(budget.get('cost_settled_cny', 0)) if budget else (known if scoped else None)
    pending = decimal(budget.get('cost_pending_cny', 0)) if budget else (Decimal(0) if scoped and not missing else None)
    def _reserved_attempts(c):
        if c.get('failure_category') == 'budget_exhausted':
            return 0
        if c.get('cost_amount') is None and c.get('status') == 'error' and any(
            k in str(c.get('error') or '') for k in ('budget', 'pricing', 'closing')
        ):
            return 0
        raw_att = c.get('attempts')
        return int(raw_att) if isinstance(raw_att, int) else 1

    audit_attempts = sum(_reserved_attempts(c) for c in calls)
    used = budget.get('used') if budget else (audit_attempts if scoped else None)
    if budget and scoped:
        if used != audit_attempts:
            warnings.append('预算请求次数与调用审计尝试总数不一致，费用以账户日预算为主。')
        elif used != len(calls):
            warnings.append(f'预算请求数（{used}）按HTTP计费请求累计，审计行数（{len(calls)}）按逻辑调用留档（含JSON修复重试，审计尝试总数 {audit_attempts}）。')
    if not scoped:
        warnings.append('数据库包含多个账户；旧调用表无account_id，逐模型明细只能作为数据库级参考。')
    model = {'currency': 'CNY', 'semantics': 'configured_estimates_not_provider_invoice',
             'count_semantics': 'requested_count=http_budget_requests;audit_rows=logical_calls;audit_attempts=sum_of_call_attempts',
             'source': 'account_daily_budget' if budget else 'single_account_call_records' if scoped else 'unattributed',
             'settled_estimate': str(settled) if settled is not None else None,
             'pending_reservation': str(pending) if pending is not None else None,
             'requested_count': used, 'audit_rows': len(calls), 'audit_attempts': audit_attempts,
             'audit_scope': 'account' if scoped else 'database_unattributed',
             'calls': [{k: sanitize_evidence(c.get(k), 200) if isinstance(c.get(k), str) else c.get(k) for k in ('created_at', 'role', 'provider', 'model', 'status',
                                             'attempts', 'failure_category', 'input_tokens', 'output_tokens', 'latency_ms', 'cost_amount', 'cost_currency')}
                       for c in calls], 'audit_unknown_cost_rows': missing}
    net = profit-settled if profit is not None and settled is not None else None
    if pending is None or pending > 0:
        warnings.append('模型费用仍有未知预留，扣模型费后的净盈亏仅为暂估。')
    plans = [json.loads(r['payload_json']) for r in data['plans']]
    history_ids = {p['plan_id'] for p in plans}
    for row in data['cached']:
        plan = json.loads(row['meta_json']).get('plan')
        if plan and plan.get('plan_id') not in history_ids:
            plans.append(plan)
    plans = [public_plan(p) for p in plans if start <= timestamp(p['available_at']) <= stop and
             (day == now.date() or timestamp(p['available_at']) < stop)]
    latest_intents = {}
    by_context = {c['context_id']: c for c in contexts if 'context_id' in c}
    for row in data['intents']:
        intent = json.loads(row['payload_json'])
        at = timestamp(intent['created_at'])
        context = by_context.get(intent.get('context_id'), {})
        if not scoped and context.get('portfolio', {}).get('account_id') != account:
            continue
        if start <= at <= stop and (day == now.date() or at < stop):
            ident = intent['decision_id']
            if ident not in latest_intents or intent.get('intent_version', 0) > latest_intents[ident].get('intent_version', 0):
                latest_intents[ident] = intent
    decisions = [{k: sanitize_evidence(i.get(k), 3000) if isinstance(i.get(k), str) else i.get(k)
                  for k in ('decision_id', 'created_at', 'instrument_id', 'action', 'reason', 'prompt_id')}
                 for i in latest_intents.values()]
    warnings.append('完整策略历史仅从新版接入后保留；旧缓存无法恢复被覆盖的计划。')
    translation_path = Path(database).parent/'daily-reports'/'translations.zh.json'
    try:
        translations=json.loads(translation_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        translations={}
    for plan in plans:
        plan['display_zh']={key:explanation(plan.get(key),translations) for key in ('trend','risk','critic','reason')}
    from ..execution.paper_run import read_intraday_health
    intraday_health = read_intraday_health(database, account, now=stop, session_open=(day == now.date() and 9 <= stop.hour < 15))
    return {'schema_version': 1, 'account_id': account, 'date': day.isoformat(), 'timezone': 'Asia/Shanghai',
            'generated_at': now.isoformat(), 'as_of': stop.isoformat(),
            'period_status': 'intraday' if day == now.date() and now.hour < 15 else 'end_of_day_snapshot',
            'intraday_health': intraday_health,
            'opening': beginning, 'ending': ending, 'net_external_cash_flow': str(funding),
            'opening_includes_initial_deposit': initial_id is not None,
            'trading_pnl': str(profit) if profit is not None else None,
            'realized_pnl': str(realized), 'trading_fees': str(fees),
            'net_after_settled_model_estimate': str(net) if net is not None else None,
            'net_after_model_reservation_bound': str(net-pending) if net is not None and pending is not None else None,
            'model_cost': model, 'trades': trades, 'strategies': plans, 'decisions': decisions, 'warnings': warnings,
            'scope': 'CNY_SQLite_paper_account_not_multiasset_inmemory_ledger'}


def render_markdown(report):
    def value(v):
        return '未确认' if v is None else escape(str(time_text(v))).replace('|', '&#124;').replace('\n', ' ')
    begin, end, model = report['opening'], report['ending'], report['model_cost']
    ih = report.get('intraday_health') or {}
    lines = [f"# {report['date']} 模拟交易日报", '', f"账户：{value(report['account_id'])}（上海日期）",
             f"统计截至：{value(report['as_of'])}；{'盘中暂报' if report['period_status']=='intraday' else '日终快照'}",
             f"盘中健康状态：{value(ih.get('health_status', '未评估'))}（{value(ih.get('status_message_zh', '无'))}）", '',
             '| 项目 | 金额（元） |', '|---|---|']
    rows = [('开始现金', begin['cash']), ('开始总资产', begin['equity']),
            ('交易后现金', end['cash']), ('冻结资金（已包含在现金中）', end['reserved_cash']),
            ('可用现金', end['available_cash']), ('交易后总资产', end['equity']),
            ('净入金/出金（不算收益）', report['net_external_cash_flow']),
            ('今天交易赚/亏（已含交易手续费）', report['trading_pnl']),
            ('今天已实现收益（可能含历史持仓收益）', report['realized_pnl']),
            ('今天交易手续费（不再重复扣）', report['trading_fees']),
            ('模型已结算估算费用', model['settled_estimate']),
            ('模型未知费用预留', model['pending_reservation']),
            ('扣已结算模型估值后净盈亏', report['net_after_settled_model_estimate'])]
    lines += [f'| {name} | {value(amount)} |' for name, amount in rows]
    lines += ['', '## 买卖成交', '', '| 时间 | 标的 | 方向 | 单价 | 数量 | 成交金额 | 手续费 | 成交后现金 |',
              '|---|---|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ACTIONS.get(t.get(k),'其他') if k=='side' else t.get(k)) for k in ('time','instrument_id','side','price','quantity','notional','fee','cash_after_fill'))+' |' for t in report['trades']]
    if not report['trades']:
        lines += ['当天没有成交，不代表程序没有运行。']
    lines += ['', '## 模型调用', '', f"预算请求数：{value(model['requested_count'])}；审计行数：{model['audit_rows']}（累计尝试数：{value(model.get('audit_attempts', model['audit_rows']))}）；费用是配置估算，不是供应商账单。",
              '', '| 时间 | 模型 | 角色 | 状态 | 输入词元 | 输出词元 | 费用估值 |', '|---|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ROLES.get(c.get(k),'其他调用') if k=='role' else STATUS.get(c.get(k),'未确认') if k=='status' else c.get(k)) for k in ('created_at','model','role','status','input_tokens','output_tokens','cost_amount'))+' |' for c in model['calls']]
    lines += ['', '## 策略计划', '']
    for p in report['strategies']:
        lines += [f"### {value(p['instrument_id'])} / {value(p['plan_id'])}",
                  f"版本：{value(p.get('prompt_version'))}；可用时间：{value(p.get('available_at'))}",
                  f"允许动作：{value('、'.join(ACTIONS.get(a,'其他') for a in p.get('allowed_actions',[])))}"]
        lines += [f"- {label}：{value(p.get('display_zh',{}).get(key,explanation(p.get(key))))}" for key,label in
                  [('trend','趋势'),('risk','风险'),('critic','反证'),('reason','原因')]]
        lines += [f"- {label}：{value(condition_text(p.get(key,[])))}" for key,label in
                  [('entry_conditions','入场'),('exit_conditions','退出'),('invalidation_conditions','失效')]]
    if not report['strategies']:
        lines += ['未找到当天已留档的有效策略计划。']
    lines += ['', '## 已留档决策（不等于成交）', '', '| 时间 | 标的 | 动作 | 原因 | 提示版本 |', '|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ACTIONS.get(d.get(k),'未确认') if k=='action' else explanation(d.get(k)) if k=='reason' else d.get(k)) for k in ('created_at','instrument_id','action','reason','prompt_id'))+' |' for d in report['decisions']]
    if not report['decisions']:
        lines += ['当天没有已留档的交易意图；预检拒绝或等待中的任务不一定产生意图记录。']
    lines += ['', '## 持仓', '', '| 标的 | 数量 | 成本 | 最新价 | 市值 | 浮动盈亏 |', '|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(p.get(k)) for k in ('instrument_id','quantity','avg_cost','last_price','market_value','unrealized_pnl'))+' |' for p in end['positions']]
    lines += ['', '## 口径与未确认内容', ''] + ['- '+value(w) for w in report['warnings']]
    return '\n'.join(lines)+'\n'


def write_report(report, directory):
    root = Path(directory)/report['account_id']
    root.mkdir(parents=True, exist_ok=True)
    base = root/report['date']
    markdown = render_markdown(report)
    sections, in_table = [], False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            if not in_table:
                sections.append('<table>')
                in_table = True
            sections.append('<tr>'+''.join('<td>'+cell.strip()+'</td>' for cell in line.strip('|').split('|'))+'</tr>')
            continue
        if in_table:
            sections.append('</table>')
            in_table = False
        if line.startswith('# '):
            sections.append('<h1>'+line[2:]+'</h1>')
        elif line.startswith('## '):
            sections.append('<h2>'+line[3:]+'</h2>')
        elif line.startswith('### '):
            sections.append('<h3>'+line[4:]+'</h3>')
        elif line:
            sections.append('<p>'+line+'</p>')
    if in_table:
        sections.append('</table>')
    # Every dynamic string has already been escaped by render_markdown.
    html = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TradeMind 每日交易报告</title><style>body{max-width:1200px;margin:24px auto;padding:24px;font:15px/1.7 sans-serif;background:#f6f8fa;color:#172033}h1{font-size:26px}h2{margin-top:32px;border-bottom:2px solid #d8e1ee}table{background:white;width:100%;border-collapse:collapse;display:block;overflow:auto}td{padding:10px;border:1px solid #d8e1ee;white-space:normal}tr:first-child{background:#e9f0fb;font-weight:bold}p{overflow-wrap:anywhere}</style>'+''.join(sections)+'</html>'
    for suffix, content in (('.json', json.dumps(report,ensure_ascii=False,indent=2)), ('.md', markdown), ('.html', html)):
        path = base.with_suffix(suffix)
        temp = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        try:
            temp.write_text(content,encoding='utf-8')
            os.replace(temp,path)
        finally:
            temp.unlink(missing_ok=True)
    return str(base.with_suffix('.html'))


def generate_for_config(cfg, day=None):
    if not cfg.storage.url.startswith('sqlite:///'):
        raise ValueError('SQLite report only')
    db = Path(cfg.storage.url.removeprefix('sqlite:///'))
    report = generate_report(db, cfg.account_id, day)
    output = write_report(report, db.parent/'daily-reports')
    return report, output

