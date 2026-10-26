"""Read-only T0 tradability diagnostics over an explicit snapshot (TASK-20261025-04).

Answers, per instrument: is fresh price observable, and does one lawful lot fit
inside the frozen cash caps (min_cash_reserve / max_single_exposure /
max_total_exposure)? Pure functions over caller-supplied data — no env, no
network, no pipeline, no production writes. Statuses:

- 资金可达: fresh price, verified rules, at least one lot fits all three caps.
- 仅观察: fresh price and rules known, but a frozen cap (or non-T0 settlement)
  blocks the first lot. Frozen caps are never relaxed here.
- 数据不足: missing/invalid/stale price, missing price visibility time, or no
  verified rule profile — never reported as "当前可以买".

Affordable is a cash/limit fact only; it does not authorize an order — model,
session, data-quality and Guard gates still apply downstream. Snapshot
`reserved_cash` is the ledger's authoritative freeze (already includes open
order freezes); `open_orders` are echoed for auditability and never
double-subtracted.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal

from ..domain.enums import OrderSide
from ..portfolio.ledger import compute_fees

SNAPSHOT_VERSION = 1
TOOL_VERSION = 2

_STATUS_ORDER = ('资金可达', '仅观察', '数据不足')


def _dec(value, field):
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{field} must be a number')
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        raise ValueError(f'{field} must be a finite number') from None
    if not result.is_finite():
        raise ValueError(f'{field} must be a finite number')
    return result


def _moment(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be an ISO-8601 string')
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{field} is not a valid ISO-8601 time') from None
    if at.utcoffset() is None:
        raise ValueError(f'{field} must carry a timezone offset')
    return at


def parse_snapshot(data):
    """Validate the explicit snapshot contract; returns Decimals and datetimes."""
    if not isinstance(data, dict):
        raise ValueError('snapshot must be a JSON object')
    if data.get('snapshot_version') != SNAPSHOT_VERSION:
        raise ValueError(f'unsupported snapshot_version (need {SNAPSHOT_VERSION})')
    if not isinstance(data.get('account_id'), str) or not data['account_id']:
        raise ValueError('account_id required')
    as_of = _moment(data.get('as_of'), 'as_of')
    cash = _dec(data.get('cash'), 'cash')
    if cash < 0:
        raise ValueError('cash must be nonnegative')
    reserved = _dec(data.get('reserved_cash', 0), 'reserved_cash')
    if reserved < 0:
        raise ValueError('reserved_cash must be nonnegative')
    if reserved > cash:
        raise ValueError('reserved_cash exceeds cash')
    positions = {}
    for entry in data.get('positions') or []:
        iid = entry.get('instrument_id')
        if not isinstance(iid, str) or not iid:
            raise ValueError('position instrument_id required')
        quantity = _dec(entry.get('quantity'), f'position {iid} quantity')
        if quantity < 0:
            raise ValueError(f'position {iid} quantity must be nonnegative')
        positions[iid] = positions.get(iid, Decimal(0)) + quantity
    open_orders = []
    for entry in data.get('open_orders') or []:
        if not isinstance(entry, dict) or not entry.get('instrument_id'):
            raise ValueError('open order instrument_id required')
        open_orders.append(entry)
    limits_raw = data.get('limits')
    if not isinstance(limits_raw, dict):
        raise ValueError('limits object required')
    limits = {key: _dec(limits_raw.get(key), f'limits.{key}') for key in
              ('max_single_exposure', 'max_total_exposure', 'min_cash_reserve')}
    if any(value < 0 for value in limits.values()):
        raise ValueError('limits must be nonnegative')
    entries = data.get('instruments')
    if not isinstance(entries, list) or not entries:
        raise ValueError('instruments list required')
    instruments, seen = [], set()
    for entry in entries:
        iid = entry.get('instrument_id')
        if not isinstance(iid, str) or not iid:
            raise ValueError('instrument_id required')
        if iid in seen:
            raise ValueError(f'duplicate instrument {iid}')
        seen.add(iid)
        price_raw = entry.get('price')
        price = None if price_raw is None else _dec(price_raw, f'{iid} price')
        price_time = (_moment(entry['price_time'], f'{iid} price_time')
                      if entry.get('price_time') else None)
        instruments.append({'instrument_id': iid, 'price': price,
                            'price_source': entry.get('price_source'),
                            'price_time': price_time})
    return {'snapshot_version': SNAPSHOT_VERSION, 'account_id': data['account_id'],
            'as_of': as_of, 'cash': cash, 'reserved_cash': reserved,
            'positions': positions, 'open_orders': open_orders,
            'limits': limits, 'instruments': instruments}


def input_fingerprint(raw_data):
    """Stable sha256 over the parsed snapshot content (ordering-insensitive)."""
    return hashlib.sha256(json.dumps(raw_data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _max_lots(notional_per_lot, price, lot, fees, budget, single_room, total_room):
    """Largest whole-lot count fitting all three frozen caps (0 when none)."""
    low, high = 0, max(0, int(min(budget, single_room, total_room) // notional_per_lot))
    while low < high:
        count = (low + high + 1) // 2
        if notional_per_lot * count + compute_fees(OrderSide.BUY, lot * count, price, fees) <= budget:
            low = count
        else:
            high = count - 1
    return low


def evaluate_instrument(entry, snapshot, profile, max_price_age_seconds, price_map,
                        valuation_complete=True):
    """Evaluate one instrument entry against the frozen caps.

    No affirmative capacity conclusion is made with incomplete valuation or
    pending-order commitments. Cash freezes and exposure commitments are separate.
    """
    iid = entry['instrument_id']
    as_of, limits = snapshot['as_of'], snapshot['limits']
    report = {'instrument_id': iid, 'price': str(entry['price']) if entry['price'] is not None else None,
              'price_source': entry['price_source'],
              'price_time': entry['price_time'].isoformat() if entry['price_time'] else None,
              'price_age_seconds': None, 'status': '数据不足', 'reasons': [],
              'lot_size': None, 'settlement_rule': None,
              'one_lot_notional': None, 'one_lot_fees': None, 'one_lot_cost': None,
              'max_affordable_lots': None}
    if profile is None:
        report['reasons'].append('无核验规则（规则库未覆盖该标的）')
        return report
    report['lot_size'] = profile.lot_size
    report['settlement_rule'] = str(profile.settlement_rule)
    report['rule_version'] = profile.profile_id
    report['fee_schedule'] = {key: str(getattr(profile.fees, key)) for key in
                             ('commission_rate', 'commission_min', 'stamp_duty_rate_sell', 'transfer_fee_rate')}
    if entry['price'] is None:
        report['reasons'].append('行情缺失（快照未提供价格）')
        return report
    if entry['price'] <= 0:
        report['reasons'].append('行情无效（零或负价格）')
        return report
    if entry['price_time'] is None:
        report['reasons'].append('缺少价格可见时间（不输出"当前可以买"）')
        return report
    age = (as_of - entry['price_time']).total_seconds()
    report['price_age_seconds'] = max(0, int(age))
    if age < 0 or age > max_price_age_seconds:
        report['reasons'].append(f'行情陈旧（可见时间早于快照超过 {max_price_age_seconds} 秒）')
        return report
    if str(profile.settlement_rule) != 'T+0':
        report['reasons'].append('非T0标的（观察池仅纳入T+0品种）')
        report['status'] = '仅观察'
        return report

    if not valuation_complete:
        report['reasons'].append('持仓估值或挂单承诺不完整，不能判定资金可达')
        return report

    price, lot = entry['price'], Decimal(profile.lot_size)
    notional = price * lot
    fees = compute_fees(OrderSide.BUY, lot, price, profile.fees)
    cost = notional + fees
    report.update({'one_lot_notional': str(notional), 'one_lot_fees': str(fees),
                   'one_lot_cost': str(cost)})
    budget = snapshot['cash'] - snapshot['reserved_cash'] - limits['min_cash_reserve']
    own_value = snapshot['positions'].get(iid, Decimal(0)) * price_map.get(iid, Decimal(0))
    total_value = sum((quantity * price_map.get(key, Decimal(0))
                       for key, quantity in snapshot['positions'].items()), Decimal(0))
    commitments = snapshot.get('buy_commitments', {})
    single_room = limits['max_single_exposure'] - own_value - commitments.get(iid, Decimal(0))
    total_room = limits['max_total_exposure'] - total_value - sum(commitments.values(), Decimal(0))
    report.update(cash_budget=str(budget), single_exposure_room=str(single_room),
                  total_exposure_room=str(total_room), pending_buy_notional=str(sum(commitments.values(), Decimal(0))))
    if cost > budget:
        report['reasons'].append('现金不足（本金+费用超出 可用现金-预留最低现金）')
    if notional > single_room:
        report['reasons'].append('单标的上限（一手名义价值超出 max_single_exposure 余量）')
    if notional > total_room:
        report['reasons'].append('总敞口上限（一手名义价值超出 max_total_exposure 余量）')
    report['status'] = '仅观察' if report['reasons'] else '资金可达'
    if not report['reasons']:
        report['max_affordable_lots'] = _max_lots(notional, price, lot, profile.fees,
                                                  budget, single_room, total_room)
    return report


def evaluate(snapshot, profiles, max_price_age_seconds=300):
    """Evaluate a parsed snapshot against {instrument_id: MarketProfile-like}."""
    if max_price_age_seconds < 0:
        raise ValueError('max_price_age_seconds must be nonnegative')
    price_map = {entry['instrument_id']: entry['price'] for entry in snapshot['instruments']
                 if entry['price'] is not None and entry['price'] > 0 and entry['price_time'] is not None
                 and 0 <= (snapshot['as_of'] - entry['price_time']).total_seconds() <= max_price_age_seconds}
    unpriced_positions = sorted(key for key, qty in snapshot['positions'].items() if qty > 0 and key not in price_map)
    commitments, order_gaps = {}, []
    for order in snapshot['open_orders']:
        if order.get('status') == 'UNKNOWN':
            order_gaps.append(order['instrument_id'])
            continue
        if order.get('status') in ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED'):
            continue
        if order.get('side') not in ('BUY', 'SELL'):
            order_gaps.append(order['instrument_id'])
            continue
        if order['side'] == 'SELL':
            continue
        try:
            remaining = (_dec(order['remaining_quantity'], 'remaining_quantity') if 'remaining_quantity' in order
                         else _dec(order['quantity'], 'quantity') - _dec(order.get('filled_quantity', 0), 'filled_quantity'))
            price = _dec(order['limit_price'], 'limit_price')
            if remaining < 0 or price <= 0:
                raise ValueError('invalid pending order')
        except (KeyError, ValueError):
            order_gaps.append(order['instrument_id'])
            continue
        iid = order['instrument_id']
        commitments[iid] = commitments.get(iid, Decimal(0)) + remaining * price
    snapshot = {**snapshot, 'buy_commitments': commitments}
    instruments = [evaluate_instrument(entry, snapshot, profiles.get(entry['instrument_id']),
                                       max_price_age_seconds, price_map,
                                       valuation_complete=not unpriced_positions and not order_gaps)
                   for entry in snapshot['instruments']]
    counts = {status: sum(1 for item in instruments if item['status'] == status)
              for status in _STATUS_ORDER}
    return {'tool': 'check_t0_tradability', 'tool_version': TOOL_VERSION,
            'snapshot_version': SNAPSHOT_VERSION,
            'account_id': snapshot['account_id'], 'as_of': snapshot['as_of'].isoformat(),
            'max_price_age_seconds': max_price_age_seconds,
            'input_sha256': None,  # filled by evaluate_from_raw
            'limits': {key: str(value) for key, value in snapshot['limits'].items()},
            'cash': str(snapshot['cash']), 'reserved_cash': str(snapshot['reserved_cash']),
            'open_orders_count': len(snapshot['open_orders']),
            'pending_buy_notional': str(sum(commitments.values(), Decimal(0))),
            'rules_sha256': input_fingerprint([{'instrument_id': item['instrument_id'],
                                               'rule_version': item.get('rule_version'),
                                               'fees': item.get('fee_schedule'), 'lot_size': item.get('lot_size')}
                                              for item in instruments]),
            'order_commitment_gaps': order_gaps,
            'position_valuation_note': (f'持仓 {", ".join(unpriced_positions)} 无新鲜可见价格，'
                                        '估值不完整，资金结论保持数据不足'
                                        if unpriced_positions else None),
            'summary': {**counts, 'total': len(instruments)}, 'instruments': instruments}


def evaluate_from_raw(raw_data, profile_resolver, max_price_age_seconds=300):
    """Convenience entry: parse raw JSON dict, resolve profiles, attach fingerprint.

    profile_resolver(instrument_id) -> MarketProfile-like or None (caller owns
    rule-source errors; returning None marks the instrument 无核验规则).
    """
    snapshot = parse_snapshot(raw_data)
    profiles = {entry['instrument_id']: profile_resolver(entry['instrument_id'])
                for entry in snapshot['instruments']}
    report = evaluate(snapshot, profiles, max_price_age_seconds)
    report['input_sha256'] = input_fingerprint(raw_data)
    return report


_REASON_NOTE = {
    '行情': '快照行情不满足新鲜可见要求，不判定资金结论',
    '无核验规则': '该标的未入核验规则库，仅回显快照内容',
    '非T0': '观察池约束：仅T+0品种可开仓',
    '现金不足': '冻结约束：可用现金扣除最低预留后不足一手本金+费用',
    '单标的': '冻结约束：max_single_exposure 余量不足一手名义价值',
    '总敞口': '冻结约束：max_total_exposure 余量不足一手名义价值',
}


def _reason_note(reason):
    for prefix, note in _REASON_NOTE.items():
        if reason.startswith(prefix):
            return note
    return ''


def render_markdown(report):
    """Deterministic Chinese Markdown rendering of an evaluate report."""
    lines = [f"# T0 资金可达性诊断（{report['account_id']}）", '',
             f"- 快照 as_of：`{report['as_of']}`；输入指纹：`{report['input_sha256']}`",
             f"- 冻结限额：min_cash_reserve={report['limits']['min_cash_reserve']}，"
             f"max_single_exposure={report['limits']['max_single_exposure']}，"
             f"max_total_exposure={report['limits']['max_total_exposure']}；"
             f"现金 {report['cash']}，预留 {report['reserved_cash']}",
             f"- 新鲜度阈值：价格可见时间距 as_of 不超过 {report['max_price_age_seconds']} 秒",
             '- 结论口径：资金可达 ≠ 下单授权；模型、时段、数据质量与 Guard 门禁仍全部生效',
             '']
    if report.get('position_valuation_note'):
        lines += [f"> ⚠ {report['position_valuation_note']}", '']
    lines += ['| 标的 | 状态 | 价格(可见时间) | 一手名义 | 一手费用 | 一手成本 | 资金可达手数 | 原因 |',
             '|---|---|---|---|---|---|---|---|']
    for item in report['instruments']:
        price = item['price'] or '—'
        if item['price_time']:
            price += f" @ {item['price_time']}"
            if item['price_age_seconds'] is not None:
                price += f"（{item['price_age_seconds']}s）"
        lines.append('| {iid} | {status} | {price} | {notional} | {fees} | {cost} | {lots} | {reasons} |'.format(
            iid=item['instrument_id'], status=item['status'], price=price,
            notional=item['one_lot_notional'] or '—', fees=item['one_lot_fees'] or '—',
            cost=item['one_lot_cost'] or '—',
            lots=item['max_affordable_lots'] if item['max_affordable_lots'] is not None else '—',
            reasons='；'.join(item['reasons']) or '—'))
    lines += ['', '## 结论说明', '']
    for item in report['instruments']:
        if item['status'] == '资金可达':
            lines.append(f"- {item['instrument_id']}：一手成本 {item['one_lot_cost']} 元落在全部冻结限额内，"
                         f"资金上限约 {item['max_affordable_lots']} 手（受最紧的一项限额约束）。")
        else:
            for reason in item['reasons']:
                note = _reason_note(reason)
                lines.append(f"- {item['instrument_id']}：{reason}。{note}" if note
                             else f"- {item['instrument_id']}：{reason}。")
    lines.append('')
    summary = report['summary']
    lines.append(f"汇总：资金可达 {summary['资金可达']}，仅观察 {summary['仅观察']}，"
                 f"数据不足 {summary['数据不足']}，共 {summary['total']} 项。")
    return '\n'.join(lines)
