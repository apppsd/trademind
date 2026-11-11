"""Pieces of the daily report. Same arithmetic as the former single function."""
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..errors import classify_not_fresh_reason
from ..market.base import BEIJING
from .locale import explanation


def _parts():
    from .daily import decimal, public_plan, timestamp
    return decimal, public_plan, timestamp


_EVENT_KINDS = {'deposit', 'withdraw', 'reserve', 'release', 'fill_buy', 'fill_sell', 'rollover', 'fee'}


def report_clock(day, now):
    now = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    day = date.fromisoformat(day) if isinstance(day, str) else (day or now.date())
    if day > now.date():
        raise ValueError('future report date')
    start = datetime.combine(day, datetime.min.time(), BEIJING)
    stop = min(start + timedelta(days=1), now)
    return now, day, start, stop


def _in_window(at, start, stop, day, now):
    return start <= at <= stop and (day == now.date() or at < stop)


def replay_book(events, start, stop, day, now):
    decimal, _, timestamp = _parts()
    events = [e for e in events if timestamp(e['effective_at']) <= stop and
              (day == now.date() or timestamp(e['effective_at']) < stop)]
    warnings = []
    if not events:
        warnings.append('缺少账户事件历史，不能确认期初及盈亏。')
    if any(e['kind'] not in _EVENT_KINDS or e.get('currency', 'CNY') != 'CNY' for e in events):
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
                state['cash'] -= price * qty + fee
                basis = lot['avg_cost'] * lot['quantity'] + price * qty + fee
                lot['quantity'] += qty
                lot['avg_cost'] = (basis / lot['quantity']).quantize(Decimal('0.0001'))
            else:
                if qty > lot['quantity']:
                    raise ValueError('sell exceeds reconstructed position')
                profit = price * qty - fee - lot['avg_cost'] * qty
                state['cash'] += price * qty - fee
                lot['quantity'] -= qty
                if today:
                    realized += profit
                if not lot['quantity']:
                    del state['positions'][iid]
            if today:
                fees += fee
                trades.append({'time': at.isoformat(), 'instrument_id': iid,
                               'side': 'BUY' if kind == 'fill_buy' else 'SELL',
                               'price': str(price), 'quantity': str(qty), 'notional': str(price * qty),
                               'fee': str(fee), 'cash_after_fill': str(state['cash']),
                               'order_id': event.get('order_id')})
        if at < start or event['seq'] == initial_id:
            opening = deepcopy(state)
    return {
        'events': events, 'warnings': warnings, 'opening': opening, 'state': state,
        'initial_id': initial_id, 'funding': funding, 'realized': realized, 'fees': fees, 'trades': trades,
    }


def value_book(snapshot, boundary, contexts, day, events, opening_mark=False):
    decimal, _, timestamp = _parts()
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
                      and 0 <= (reference - mark[0]).total_seconds() <= 180)
        if not usable:
            complete = False
        market_value = lot['quantity'] * mark[1] if usable else None
        if market_value is not None:
            value += market_value
        positions.append({'instrument_id': iid, 'quantity': str(lot['quantity']),
                          'avg_cost': str(lot['avg_cost']),
                          'last_price': str(mark[1]) if usable else None,
                          'price_time': mark[0].isoformat() if mark else None,
                          'market_value': str(market_value) if market_value is not None else None,
                          'unrealized_pnl': str(market_value - lot['avg_cost'] * lot['quantity']) if usable else None})
    return {'cash': str(snapshot['cash']), 'reserved_cash': str(snapshot['reserved']),
            'available_cash': str(snapshot['cash'] - snapshot['reserved']), 'positions': positions,
            'equity': str(snapshot['cash'] + value) if complete and events else None,
            'valuation_complete': complete and bool(events)}


def _reserved_attempts(c):
    if c.get('failure_category') == 'budget_exhausted':
        return 0
    if c.get('cost_amount') is None and c.get('status') == 'error' and any(
        k in str(c.get('error') or '') for k in ('budget', 'pricing', 'closing')
    ):
        return 0
    raw_att = c.get('attempts')
    return int(raw_att) if isinstance(raw_att, int) else 1


def model_cost(data, calls_rows, start, stop, day, now, warnings):
    from ..execution.paper_run import sanitize_evidence
    decimal, _, timestamp = _parts()
    calls = [json.loads(r['payload_json']) for r in calls_rows]
    calls = [c for c in calls if _in_window(timestamp(c['created_at']), start, stop, day, now)]
    budget = next((json.loads(r['meta_json']) for r in data['budgets']
                   if json.loads(r['meta_json']).get('day') == day.isoformat()), None)
    known = sum((decimal(c['cost_amount']) for c in calls if c.get('cost_amount') is not None and c.get('cost_currency') == 'CNY'), Decimal(0))
    missing = sum(c.get('cost_amount') is None or c.get('cost_currency') != 'CNY' for c in calls)
    scoped = data['account_count'] == 1
    settled = decimal(budget.get('cost_settled_cny', 0)) if budget else (known if scoped else None)
    pending = decimal(budget.get('cost_pending_cny', 0)) if budget else (Decimal(0) if scoped and not missing else None)
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
             'calls': [{k: sanitize_evidence(c.get(k), 200) if isinstance(c.get(k), str) else c.get(k) for k in (
                 'created_at', 'role', 'provider', 'model', 'status', 'attempts', 'failure_category',
                 'input_tokens', 'output_tokens', 'latency_ms', 'cost_amount', 'cost_currency')}
                       for c in calls], 'audit_unknown_cost_rows': missing}
    return model, settled, pending, scoped


def collect_plans(data, start, stop, day, now):
    _, public_plan, timestamp = _parts()
    plans = [json.loads(r['payload_json']) for r in data['plans']]
    history_ids = {p['plan_id'] for p in plans}
    for row in data['cached']:
        plan = json.loads(row['meta_json']).get('plan')
        if plan and plan.get('plan_id') not in history_ids:
            plans.append(plan)
    return [public_plan(p) for p in plans if _in_window(timestamp(p['available_at']), start, stop, day, now)]


def _clean_gate(gate, sanitize_evidence):
    def clean(v):
        if isinstance(v, str):
            return sanitize_evidence(v, 160)
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    return {k: clean(v) for k, v in gate.items()} if isinstance(gate, dict) else gate


def collect_decisions(data, contexts, account, start, stop, day, now, scoped):
    from ..execution.paper_run import sanitize_evidence
    _, _, timestamp = _parts()
    latest_intents = {}
    by_context = {c['context_id']: c for c in contexts if 'context_id' in c}
    for row in data['intents']:
        intent = json.loads(row['payload_json'])
        at = timestamp(intent['created_at'])
        context = by_context.get(intent.get('context_id'), {})
        if not scoped and context.get('portfolio', {}).get('account_id') != account:
            continue
        if _in_window(at, start, stop, day, now):
            ident = intent['decision_id']
            if ident not in latest_intents or intent.get('intent_version', 0) > latest_intents[ident].get('intent_version', 0):
                latest_intents[ident] = intent
    decisions = [{k: sanitize_evidence(i.get(k), 3000) if isinstance(i.get(k), str) else i.get(k)
                  for k in ('decision_id', 'created_at', 'instrument_id', 'action', 'reason', 'prompt_id')}
                 for i in latest_intents.values()]
    # Additive no-actionable per-condition evidence (t279u item A): taken from
    # the decision audit's t0_chain rows when the fast pipeline recorded it.
    # Historical intents without a gate are never backfilled — the coverage
    # note below says so instead of inventing conditions.
    gate_by_decision = {}
    window_by_decision = {}
    for r in data['audit']:
        if r.get('action') != 't0_chain':
            continue
        try:
            payload = json.loads(r['payload_json'])
        except ValueError:
            continue
        diagnostics = (payload.get('meta') or {}).get('diagnostics') or {}
        did = payload.get('decision_id')
        if not did:
            continue
        gate = diagnostics.get('action_gate')
        if gate and str(did) not in gate_by_decision:
            gate_by_decision[str(did)] = gate
        # Per-decision global-window claim facts (rereview R3): only this
        # account's audited rounds are joined below, keyed by decision_id.
        window = diagnostics.get('model_window')
        if window and str(did) not in window_by_decision:
            window_by_decision[str(did)] = window
    for d in decisions:
        gate = gate_by_decision.get(d['decision_id'])
        if gate:
            d['action_gate'] = _clean_gate(gate, sanitize_evidence)
        window = window_by_decision.get(d['decision_id'])
        if window:
            d['model_window'] = _clean_gate(window, sanitize_evidence)
    no_actionable = [d for d in decisions if d.get('reason') == 'no actionable strategy conditions']
    note = None
    if no_actionable:
        carried = sum(1 for d in no_actionable if d.get('action_gate'))
        if carried < len(no_actionable):
            note = (f'无可行动条件的已留档意图共 {len(no_actionable)} 条，'
                    f'{carried} 条携带逐条件证据；其余为旧版本历史，仅有总原因，不补造。')
    return decisions, note


def decision_distribution(decisions, stop):
    # (a) Saved-intent distribution: the dedupe above (per decision_id, max
    # intent_version) is the whole denominator. Rounds that never produced an
    # intent — data rejects, async pending — are counted separately below and
    # are never added to this denominator.
    distribution = {
        'cutoff': stop.isoformat(),
        'denominator': len(decisions),
        'dedupe_rule': 'per decision_id keep max intent_version',
        'denominator_semantics': 'saved intents only; pre-decision data rejects and async-pending rounds are not intents and are counted in pre_decision_rounds',
        'by_action': {}, 'by_reason': {}}
    for d in decisions:
        action = d.get('action') or 'unknown'
        distribution['by_action'][action] = distribution['by_action'].get(action, 0) + 1
        reason = d.get('reason') or 'unknown'
        distribution['by_reason'][reason] = distribution['by_reason'].get(reason, 0) + 1
    return distribution


def pre_decision_rounds(data, account, start, stop, day, now):
    _, _, timestamp = _parts()
    # (b) Pre-decision rounds from the decision audit trail only; reporting
    # makes no market or model calls. Rounds before this audit's introduction
    # have no per-round record, so their denominators are unrecoverable.
    # Account scoping (review R1): every row is parsed and attributed before
    # any counting. Foreign-account rows never touch this account's
    # denominators; rows with a missing/blank account_id or an unparseable
    # payload are kept in their own unknown_account:* buckets instead of being
    # silently folded into the current account.
    pre_rounds = {}
    foreign_pre_rounds = 0
    for r in data['audit']:
        if r.get('action') not in ('data_reject', 'model_pending'):
            continue
        try:
            at = timestamp(r['at'])
        except (KeyError, ValueError, TypeError):
            continue
        if not _in_window(at, start, stop, day, now):
            continue
        try:
            payload = json.loads(r['payload_json'])
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            payload = {}
        row_account = payload.get('account_id')
        if isinstance(row_account, str) and row_account.strip():
            if row_account != account:
                foreign_pre_rounds += 1
                continue
            bucket = pre_rounds.setdefault(r['action'], {'count': 0, 'reasons': {}})
        else:
            bucket = pre_rounds.setdefault('unknown_account:' + r['action'],
                                           {'count': 0, 'reasons': {}})
        bucket['count'] += 1
        # Closed reason whitelist (review R2 round 3): audit rows are
        # untrusted input for reporting. Known constants and legacy stable
        # prefixes pass; a raw exception body collapses to 'unknown' in the
        # JSON, Markdown and HTML buckets alike.
        raw_reason = payload.get('reason')
        reason_key = (classify_not_fresh_reason(raw_reason)
                      if isinstance(raw_reason, str) and raw_reason else 'unknown')
        bucket['reasons'][reason_key] = bucket['reasons'].get(reason_key, 0) + 1
    coverage_extra = []
    unknown_rounds = sum(v['count'] for k, v in pre_rounds.items() if k.startswith('unknown_account:'))
    if unknown_rounds:
        coverage_extra.append(f'另有 {unknown_rounds} 条当日审计行缺少账户归属或记录无法解析，'
                              f'单列在 unknown_account 桶中，不计入本账户分母。')
    if foreign_pre_rounds:
        coverage_extra.append(f'另有 {foreign_pre_rounds} 条当日审计行属于其他账户，未计入本账户分母。')
    return {
        'cutoff': stop.isoformat(),
        'source': 'control_audit decision rows (data_reject / model_pending)',
        'dedupe_rule': 'one audit row per rejected or pending decision round; no dedupe',
        'denominator_semantics': ('independent of the saved-intent denominator; not additive with it; '
                                  'only rows attributed to this account count in its per-action buckets'),
        'buckets': pre_rounds,
        'coverage_note': '数据拒绝/异步等待轮次自本版本起逐轮写入审计；更早历史轮次没有逐轮留档，当日分母不可恢复。'
                         + (''.join(coverage_extra) if coverage_extra else ''),
    }


def attach_plan_text(plans, database):
    translation_path = Path(database).parent / 'daily-reports' / 'translations.zh.json'
    try:
        translations = json.loads(translation_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        translations = {}
    for plan in plans:
        plan['display_zh'] = {key: explanation(plan.get(key), translations) for key in ('trend', 'risk', 'critic', 'reason')}
