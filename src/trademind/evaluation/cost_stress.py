"""Offline cost & fill stress evaluation over archived intents (TASK-20261025-05).

Two outputs from one read-only bundle (explicit JSON, no env, no network, no
production writes, no model calls, no regeneration of history):

- A: round-trip breakeven cost table for given notionals — pure arithmetic,
  needs no fills at all.
- B: deterministic stress replay of archived BUY/SELL intents against the
  bundle's own subsequent minute bars under frozen scenario knobs (slippage
  per side, commission minimum, fill delay in bars, volume participation cap).
  The replay recomputes fills, positions, available cash and downstream order
  legality in an isolated in-memory account — it never subtracts a constant
  from an old NAV.

Scenario knobs are sensitivity parameters, not real broker quotes and not
production config. Orders keep their archived limit price: a bar whose adverse
price (plus slippage) does not reach the limit does not fill. Statuses:

- ok: scenario fully evaluated on a completed round trip.
- insufficient_data: inputs complete but no completed BUY→SELL round trip.
- incomplete: inputs missing for an intent (no subsequent bars).
- unsupported: scenario requires a capability this tool does not implement.

Fees are computed with the production ``compute_fees`` semantics and deducted
exactly once per fill; model cost is reported separately and never merged into
trading P&L. Unknown model cost makes the combined net unknown. Zero trades
still carry the real model cost. Nothing here may be read as "the tool runs,
therefore the strategy is profitable".
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from ..domain.base import new_id, utcnow
from ..domain.enums import OrderSide
from ..domain.instruments import FeeSchedule
from ..portfolio.ledger import compute_fees
from ..market.profiles import get_profile

BUNDLE_VERSION = 2
TOOL_VERSION = 2

# Frozen sensitivity scenarios (TASK-20261025-05 §3). Slippage in bps per side;
# commission_min in CNY per side; delay_bars: 0 = first bar after the intent
# becomes visible, k = skip k further eligible bars; participation: max share
# of bar volume fillable (None = unconstrained). These are NOT broker quotes.
SCENARIOS = (
    {'id': 'baseline', 'slippage_bps': 5, 'commission_min': '0.1', 'delay_bars': 0,
     'participation': None},
    {'id': 'slippage-10bps', 'slippage_bps': 10, 'commission_min': '0.1', 'delay_bars': 0,
     'participation': None},
    {'id': 'slippage-20bps', 'slippage_bps': 20, 'commission_min': '0.1', 'delay_bars': 0,
     'participation': None},
    {'id': 'commission-min-1', 'slippage_bps': 5, 'commission_min': '1', 'delay_bars': 0,
     'participation': None},
    {'id': 'commission-min-5', 'slippage_bps': 5, 'commission_min': '5', 'delay_bars': 0,
     'participation': None},
    {'id': 'delay-1', 'slippage_bps': 5, 'commission_min': '0.1', 'delay_bars': 1,
     'participation': None},
    {'id': 'delay-2', 'slippage_bps': 5, 'commission_min': '0.1', 'delay_bars': 2,
     'participation': None},
    {'id': 'participation-10pct', 'slippage_bps': 5, 'commission_min': '0.1', 'delay_bars': 0,
     'participation': Decimal('0.10')},
)

LIMITATIONS = [
    '场景参数是敏感性假设，不是真实券商报价；结果不用于生产，也不构成策略正期望的证据。',
    '撮合为保守逐bar规则（对手价+滑点、保留限价、参与率封顶），未建模盘口队列路径。',
    '交易费用按生产 compute_fees 语义每笔成交只扣一次；模型成本独立汇总，不与交易损益混算。',
    '预留（reservation）不是账单；未知费用使组合净结果为 unknown 而非 0。',
    '跨场景净值不保证单调：触价/未成交会改变后续路径，报告只陈述各路径事实。',
    '仅支持核验T0 ETF、初始空仓且无冻结的隔离账户；每意图一次撮合尝试，余单按评估规则取消。',
    '成交现金/持仓按available_at入账；同刻先提交后入账，不能预支同刻成交。回撤为可见bar采样口径。',
]


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


def parse_bundle(data):
    """Validate the offline bundle contract; returns Decimals and datetimes."""
    if not isinstance(data, dict):
        raise ValueError('bundle must be a JSON object')
    if data.get('bundle_version') != BUNDLE_VERSION:
        raise ValueError(f'unsupported bundle_version (need {BUNDLE_VERSION})')
    if not isinstance(data.get('source'), str) or not data['source']:
        raise ValueError('source required')
    as_of = _moment(data.get('as_of'), 'as_of')
    config = data.get('account_config')
    if not isinstance(config, dict):
        raise ValueError('account_config required')
    initial_cash = _dec(config.get('initial_cash'), 'account_config.initial_cash')
    if initial_cash <= 0:
        raise ValueError('initial_cash must be positive')
    limits = {key: _dec(config.get(key), f'account_config.{key}') for key in
              ('min_cash_reserve', 'max_single_exposure', 'max_total_exposure')}
    if any(value < 0 for value in limits.values()):
        raise ValueError('account limits must be nonnegative')
    if config.get('positions') or _dec(config.get('reserved_cash', 0), 'reserved_cash') != 0:
        raise ValueError('only fresh empty accounts with no initial reservation are supported')
    fee_raw = data.get('fee_profile')
    if not isinstance(fee_raw, dict):
        raise ValueError('fee_profile required')
    fee_profile = {key: _dec(fee_raw.get(key), f'fee_profile.{key}') for key in
                   ('commission_rate', 'commission_min', 'stamp_duty_rate_sell', 'transfer_fee_rate')}
    if any(value < 0 for value in fee_profile.values()):
        raise ValueError('fee_profile must be nonnegative')
    model_cost = data.get('model_cost')
    if model_cost is None:
        model_cost = {'settled_cny': '0', 'pending_cny': '0', 'unknown': True}
    if not isinstance(model_cost, dict) or type(model_cost.get('unknown')) is not bool:
        raise ValueError('model_cost requires explicit amounts and boolean unknown')
    model = {'settled_cny': _dec(model_cost.get('settled_cny'), 'model_cost.settled_cny'),
             'pending_cny': _dec(model_cost.get('pending_cny'), 'model_cost.pending_cny'),
             'unknown': model_cost['unknown']}
    if model['settled_cny'] < 0 or model['pending_cny'] < 0:
        raise ValueError('model costs must be nonnegative')
    model['unknown'] = model['unknown'] or model['pending_cny'] > 0
    intents, bars = [], []
    ids = set()
    for entry in data.get('intents') or []:
        side = entry.get('side')
        if side not in ('BUY', 'SELL'):
            raise ValueError('intent side must be BUY or SELL')
        iid = entry.get('instrument_id')
        if not isinstance(iid, str):
            raise ValueError('intent instrument_id required')
        profile = get_profile(iid)
        if profile.security_type != 'etf' or profile.t_plus_days != 0:
            raise ValueError('only verified T0 ETF supported')
        qty = _dec(entry.get('quantity'), 'intent quantity')
        price = _dec(entry.get('limit_price'), 'intent limit_price')
        if qty <= 0 or qty % profile.lot_size != 0 or price <= 0 or price % profile.price_tick != 0:
            raise ValueError('positive lawful quantity and tick-aligned price required')
        intended_at = _moment(entry.get('intended_at'), 'intent intended_at')
        if intended_at > as_of:
            raise ValueError('intent beyond archive as_of')
        intent_id = entry.get('intent_id') or f'intent-{len(intents)+1}'
        if intent_id in ids:
            raise ValueError('duplicate intent_id')
        ids.add(intent_id)
        intents.append({'intent_id': intent_id,
                        'instrument_id': entry.get('instrument_id'),
                        'side': side, 'limit_price': price, 'quantity': qty,
                        'lot_size': Decimal(profile.lot_size), 'price_tick': profile.price_tick,
                        'intended_at': intended_at})
    seen_bars = set()
    for entry in data.get('bars') or []:
        bar_time = _moment(entry.get('bar_time'), 'bar bar_time')
        available = _moment(entry.get('available_at'), 'bar available_at')
        if available < bar_time:
            raise ValueError('bar cannot be visible before its close')
        key = (entry.get('instrument_id'), bar_time)
        if key in seen_bars:
            raise ValueError('duplicate bar; revisions require a separate input contract')
        seen_bars.add(key)
        bars.append({'instrument_id': entry.get('instrument_id'),
                     'bar_time': bar_time, 'available_at': available,
                     'high': _dec(entry.get('high'), 'bar high'),
                     'low': _dec(entry.get('low'), 'bar low'),
                     'close': _dec(entry.get('close'), 'bar close'),
                     'volume': (_dec(entry.get('volume'), 'bar volume')
                                if entry.get('volume') is not None else None)})
        bar = bars[-1]
        if (not isinstance(bar['instrument_id'], str) or not bar['instrument_id']
            or not 0 < bar['low'] <= bar['close'] <= bar['high']
            or bar['volume'] is not None and bar['volume'] < 0):
            raise ValueError('invalid bar price/volume/instrument')
    intents.sort(key=lambda item: item['intended_at'])
    return {'bundle_version': BUNDLE_VERSION, 'source': data['source'],
            'initial_cash': initial_cash, 'limits': limits, 'as_of': as_of,
            'fee_profile': fee_profile, 'model_cost': model,
            'intents': intents, 'bars': sorted(bars, key=lambda item: (item['bar_time'],
                                                                    item['instrument_id']))}


def input_fingerprint(raw_data):
    return hashlib.sha256(json.dumps(raw_data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def make_fees(rate, minimum, stamp='0', transfer='0.00001'):
    """FeeSchedule with production-style identity fields (no persistence)."""
    now = utcnow()
    return FeeSchedule(id=new_id('fs'), trace_id=new_id('tr'), created_at=now,
                       commission_rate=str(rate), commission_min=str(minimum),
                       stamp_duty_rate_sell=str(stamp), transfer_fee_rate=str(transfer))


def breakeven_table(notionals=(Decimal(100), Decimal(200)), slippage_bps=(5, 10, 20),
                    commission_mins=('0.1', '1', '5'), rate=Decimal('0.0001'),
                    transfer=Decimal('0.00001'), stamp_sell=Decimal('0')):
    """Round-trip breakeven cost for one unit round trip per notional (A).

    Uses production fee semantics per leg: max(notional×rate, min) + stamp on
    the sell leg + transfer, quantized to 0.01; slippage applies adversely on
    both legs. Pure arithmetic — no fills, no archives required.
    """
    rows = []
    for notional in notionals:
        quantity = Decimal(100)  # table base: one lot; notional drives the price
        price = (notional / quantity).quantize(Decimal('0.001'))
        for bps in slippage_bps:
            for minimum in commission_mins:
                fees = make_fees(rate, minimum, stamp_sell, transfer)
                buy_fee = compute_fees(OrderSide.BUY, quantity, price, fees)
                sell_fee = compute_fees(OrderSide.SELL, quantity, price, fees)
                slip = notional * Decimal(bps) * 2 / Decimal(10000)
                total = buy_fee + sell_fee + slip
                rows.append({'notional_cny': str(notional), 'slippage_bps': bps,
                             'commission_min_cny': str(minimum),
                             'buy_fee': str(buy_fee), 'sell_fee': str(sell_fee),
                             'slippage_cost': str(slip.quantize(Decimal('0.01'))),
                             'round_trip_cost': str(total.quantize(Decimal('0.01'))),
                             'breakeven_ratio': str((total / notional).quantize(Decimal('0.0001'))),
                             'base_note': f'按 {quantity:.0f} 股一手、名义本金 {notional} 元计算；'
                                          '摊销当日模型成本需另加 模型成本/本金 的收益'})
    return rows


def _fill_for(intent, eligible_bar, scenario, quantity):
    """Conservative fill decision on one bar; returns (price, filled_qty, reason)."""
    slip = Decimal(scenario['slippage_bps']) / Decimal(10000)
    if intent['side'] == 'BUY':
        price = (eligible_bar['high'] * (1 + slip))
        if price > intent['limit_price']:
            return None, Decimal(0), '合格bar对手价+滑点未触及限价，不强制成交'
    else:
        price = eligible_bar['low'] * (1 - slip)
        if price < intent['limit_price']:
            return None, Decimal(0), '合格bar对手价-滑点未触及限价，不强制成交'
    filled = quantity
    if eligible_bar['volume'] is None or eligible_bar['volume'] <= 0:
        return None, Decimal(0), '缺少正成交量，不能假设可成交'
    if eligible_bar['high'] == eligible_bar['low']:
        return None, Decimal(0), '单价bar无盘口流动性证据，保守不假定可成交'
    participation = scenario.get('participation')
    if participation is not None:
        volume = eligible_bar['volume']
        if volume is None:
            return None, Decimal(0), '参与率场景要求bar成交量，快照缺失该字段'
        cap = (volume * participation / intent['lot_size']).to_integral_value(rounding=ROUND_FLOOR) * intent['lot_size']
        filled = min(quantity, cap)
        if filled <= 0:
            return None, Decimal(0), '参与率封顶后可成交量为0'
    tick = intent['price_tick']
    price = (price / tick).to_integral_value(
        rounding=ROUND_CEILING if intent['side'] == 'BUY' else ROUND_FLOOR) * tick
    if intent['side'] == 'BUY' and price > intent['limit_price'] or intent['side'] == 'SELL' and price < intent['limit_price']:
        return None, Decimal(0), '按不利方向对齐tick后超出限价'
    return price, filled, None


def replay_scenario(bundle, scenario):
    """Replay submission, visibility and fill events in causal time order.

    Each intent reserves cash/sellable holdings at submission. Its single fill
    attempt is made only when the selected subsequent bar becomes visible.
    Unfilled remainders are explicitly cancelled; bar participation is shared.
    """
    cash = bundle['initial_cash']
    positions, prices, pending, participation_used = {}, {}, {}, {}
    fees_base = dict(bundle['fee_profile'])
    fee_profile = make_fees(fees_base['commission_rate'], scenario['commission_min'],
                            fees_base['stamp_duty_rate_sell'], fees_base['transfer_fee_rate'])
    bars_by_instr = {}
    for bar in bundle['bars']:
        bars_by_instr.setdefault(bar['instrument_id'], []).append(bar)
    fills, rejected, unfilled, incomplete, curve = [], [], [], [], []
    events = []
    for index, bar in enumerate(bundle['bars']):
        if bar['available_at'] <= bundle['as_of']:
            events.append((bar['available_at'], 1, index, 'mark', bar))
    for index, intent in enumerate(bundle['intents']):
        eligible = [bar for bar in bars_by_instr.get(intent['instrument_id'], [])
                    if intent['intended_at'] < bar['bar_time'] <= bar['available_at'] <= bundle['as_of']]
        eligible.sort(key=lambda bar: (bar['available_at'], bar['bar_time']))
        bar = eligible[scenario['delay_bars']] if len(eligible) > scenario['delay_bars'] else None
        events.append((intent['intended_at'], 0, index, 'submit', (intent, bar)))
        if bar is not None:
            events.append((bar['available_at'], 2, index, 'fill', (intent, bar)))

    limits = bundle['limits']
    for at, _, index, kind, item in sorted(events, key=lambda event: event[:3]):
        if kind == 'mark':
            prices[item['instrument_id']] = item['close']
        elif kind == 'submit':
            intent, bar = item
            iid = intent['instrument_id']
            qty, limit = intent['quantity'], intent['limit_price']
            reason = None
            if intent['side'] == 'BUY':
                reserve = qty * limit + compute_fees(OrderSide.BUY, qty, limit, fee_profile)
                frozen = sum((order['cash'] for order in pending.values()), Decimal(0))
                own_value = positions.get(iid, Decimal(0)) * prices.get(iid, Decimal(0))
                held_value = sum((q * prices.get(key, Decimal(0)) for key, q in positions.items()), Decimal(0))
                commitments = sum((order['notional'] for order in pending.values()), Decimal(0))
                own_pending = sum((order['notional'] for order in pending.values() if order['iid'] == iid), Decimal(0))
                if reserve > cash - frozen - limits['min_cash_reserve']:
                    reason = '重算现金不足：含费用冻结及最低现金储备'
                elif own_value + own_pending + qty * limit > limits['max_single_exposure']:
                    reason = '单标的敞口上限（含在途买单承诺）'
                elif held_value + commitments + qty * limit > limits['max_total_exposure']:
                    reason = '总敞口上限（含在途买单承诺）'
                order = {'iid': iid, 'cash': reserve, 'sell_qty': Decimal(0), 'notional': qty * limit}
            else:
                frozen = sum((order['sell_qty'] for order in pending.values() if order['iid'] == iid), Decimal(0))
                if positions.get(iid, Decimal(0)) - frozen < qty:
                    reason = '重算持仓不足：提交时尚无可卖持仓（含在途卖单冻结）'
                order = {'iid': iid, 'cash': Decimal(0), 'sell_qty': qty, 'notional': Decimal(0)}
            if reason:
                rejected.append({'intent_id': intent['intent_id'], 'side': intent['side'], 'reason': reason,
                                 'at': at.isoformat()})
            elif bar is None:
                incomplete.append({'intent_id': intent['intent_id'],
                                   'reason': '档案截止前可见bar不足，不使用未来信息'})
                # Missing future data is not evidence that this order cancelled.
                pending[intent['intent_id']] = order
            else:
                pending[intent['intent_id']] = order
        else:
            intent, bar = item
            if pending.pop(intent['intent_id'], None) is None:
                continue
            iid = intent['instrument_id']
            price, filled, reason = _fill_for(intent, bar, scenario, intent['quantity'])
            key = (iid, bar['bar_time'])
            if not reason and scenario['participation'] is not None:
                cap = (bar['volume'] * scenario['participation'] / intent['lot_size']).to_integral_value(rounding=ROUND_FLOOR) * intent['lot_size']
                filled = min(filled, max(Decimal(0), cap - participation_used.get(key, Decimal(0))))
                if filled == 0:
                    reason = '该bar参与率共享容量已耗尽'
            if reason:
                target = incomplete if bar['volume'] is None else unfilled
                target.append({'intent_id': intent['intent_id'], 'side': intent['side'],
                               'quantity': str(intent['quantity']), 'reason': reason})
                continue
            notional = price * filled
            fee = compute_fees(OrderSide[intent['side']], filled, price, fee_profile)
            if intent['side'] == 'BUY':
                cash -= notional + fee
                positions[iid] = positions.get(iid, Decimal(0)) + filled
            else:
                cash += notional - fee
                positions[iid] -= filled
            participation_used[key] = participation_used.get(key, Decimal(0)) + filled
            prices[iid] = bar['close']
            if filled < intent['quantity']:
                unfilled.append({'intent_id': intent['intent_id'], 'side': intent['side'],
                                 'quantity': str(intent['quantity'] - filled),
                                 'reason': '参与率封顶后仅部分成交，剩余数量按单次尝试规则取消'})
            fills.append({'intent_id': intent['intent_id'], 'instrument_id': iid, 'side': intent['side'],
                          'quantity': str(filled), 'price': str(price), 'fee': str(fee),
                          'bar_time': bar['bar_time'].isoformat(), 'available_at': at.isoformat()})
        equity = cash + sum((qty * prices.get(iid, Decimal(0)) for iid, qty in positions.items()), Decimal(0))
        curve.append({'at': at.isoformat(), 'equity': str(equity)})

    fees_total = sum((Decimal(fill['fee']) for fill in fills), Decimal(0))
    open_positions = {iid: str(quantity) for iid, quantity in positions.items() if quantity > 0}
    trading_cash_net = cash - bundle['initial_cash']
    equity = cash + sum((qty * prices[iid] for iid, qty in positions.items() if qty), Decimal(0))
    trading_equity_net = equity - bundle['initial_cash']
    peak, max_drawdown = bundle['initial_cash'], Decimal(0)
    for row in curve:
        value = Decimal(row['equity'])
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak)
    model = bundle['model_cost']
    result = {'scenario_id': scenario['id'], 'params': {**{k: v for k, v in scenario.items() if k != 'id'},
                                                       'participation': str(scenario['participation'])
                                                       if scenario['participation'] is not None else None},
              'status': 'ok', 'fills': fills, 'rejected': rejected, 'unfilled': unfilled,
              'incomplete': incomplete, 'fees_total': str(fees_total),
              'fills_count': len(fills), 'open_positions': open_positions,
              'pending_orders': list(pending), 'ending_cash': str(cash), 'ending_equity': str(equity),
              'equity_curve': curve, 'max_drawdown': str(max_drawdown),
              'trading_equity_net': str(trading_equity_net),
              'open_position_note': ('仍有未平仓，现金流不等于交易损益；权益按截止前最后可见close估值'
                                     if open_positions else None),
              'trading_cash_net': str(trading_cash_net),
              'model_cost_cny': str(model['settled_cny']),
              'combined_net': None if model['unknown'] else str(trading_equity_net - model['settled_cny']),
              'net_after_settled_model_estimate': str(trading_equity_net - model['settled_cny']),
              'combined_net_unknown': model['unknown']}
    if incomplete:
        result['status'] = 'incomplete'
        result['status_reason'] = '截止前缺合格可见bar；待处理订单保留，不能假定取消'
    elif open_positions or not any(fill['side'] == 'SELL' for fill in fills):
        result['status'] = 'insufficient_data'
        result['status_reason'] = '无全部平仓的BUY→SELL往返，不能称完整执行结果'
    return result



def evaluate_bundle(raw_data):
    """Full A+B evaluation; deterministic, read-only, no side effects."""
    bundle = parse_bundle(raw_data)
    scenarios = [replay_scenario(bundle, scenario) for scenario in SCENARIOS]
    return {'tool': 'evaluate_t0_cost_stress', 'tool_version': TOOL_VERSION,
            'bundle_version': BUNDLE_VERSION, 'source': bundle['source'],
            'input_sha256': input_fingerprint(raw_data),
            'sample_scope': {'intents': len(bundle['intents']), 'bars': len(bundle['bars']),
                             'as_of': bundle['as_of'].isoformat(),
                             'account_limits': {key: str(value) for key, value in bundle['limits'].items()},
                             'intent_range': ([bundle['intents'][0]['intended_at'].isoformat(),
                                               bundle['intents'][-1]['intended_at'].isoformat()]
                                              if bundle['intents'] else None),
                             'initial_cash': str(bundle['initial_cash'])},
            'breakeven_table': breakeven_table(rate=bundle['fee_profile']['commission_rate'],
                                               transfer=bundle['fee_profile']['transfer_fee_rate'],
                                               stamp_sell=bundle['fee_profile']['stamp_duty_rate_sell']),
            'model_cost': {'settled_cny': str(bundle['model_cost']['settled_cny']),
                           'pending_cny': str(bundle['model_cost']['pending_cny']),
                           'unknown': bundle['model_cost']['unknown'],
                           'note': '模型成本独立于交易损益；零交易也照实计入'},
            'scenarios': scenarios, 'limitations': LIMITATIONS}

def render_markdown(report):
    """Deterministic Chinese Markdown rendering (breakeven table + scenarios)."""
    scope = report['sample_scope']
    lines = ['# 离线成本与撮合压力评估', '',
             f"- 输入来源：`{report['source']}`；输入指纹：`{report['input_sha256']}`",
             f"- 样本范围：意图 {scope['intents']} 条，bar {scope['bars']} 根，"
             f"隔离账户初始现金 {scope['initial_cash']} 元"
             + (f"，意图时间 {scope['intent_range'][0]} ~ {scope['intent_range'][1]}" if scope['intent_range'] else ''),
             f"- 模型成本：settled {report['model_cost']['settled_cny']} 元，pending {report['model_cost']['pending_cny']} 元"
             + ('，**未知**' if report['model_cost']['unknown'] else '')
             + '（独立于交易损益；零交易也照实计入；预留不是账单）',
             '- 结论口径：场景参数是敏感性假设而非真实券商报价；工具完成 ≠ 策略正期望',
             '', '## A. 往返盈亏平衡成本表（无需真实成交）', '',
             '| 名义本金 | 滑点/边 | 最低佣金/边 | 买费 | 卖费 | 滑点成本 | 往返总成本 | 盈亏平衡收益率 |',
             '|---|---|---|---|---|---|---|---|']
    for row in report['breakeven_table']:
        lines.append('| {n} | {b} | {m} | {bf} | {sf} | {sc} | {tot} | {ratio} |'.format(
            n=row['notional_cny'], b=f"{row['slippage_bps']}bps", m=row['commission_min_cny'],
            bf=row['buy_fee'], sf=row['sell_fee'], sc=row['slippage_cost'],
            tot=row['round_trip_cost'], ratio=row['breakeven_ratio']))
    lines += ['', '摊销说明：' + report['breakeven_table'][0]['base_note'], '',
              '## B. 执行假设压力回放（隔离账户逐笔重算）', '',
              '| 场景 | 状态 | 成交数 | 交易现金净额 | 交易权益损益 | 费用合计 | 扣模型费净额 | 回撤 | 未成交/被拒/缺输入 |',
              '|---|---|---|---|---|---|---|---|---|']
    for item in report['scenarios']:
        params = item.get('params', {})
        knob = (f"滑点{params.get('slippage_bps')}bps/佣金下限{params.get('commission_min')}"
                f"/延迟{params.get('delay_bars')}bar"
                + (f"/参与率{params.get('participation')}" if params.get('participation') else ''))
        combined = 'unknown' if item.get('combined_net_unknown') else item.get('combined_net', '—')
        counts = f"{len(item.get('unfilled', []))}/{len(item.get('rejected', []))}/{len(item.get('incomplete', []))}"
        lines.append(f"| {item['scenario_id']}（{knob}） | {item['status']} | {item.get('fills_count', 0)} | "
                     f"{item.get('trading_cash_net', '—')} | {item.get('trading_equity_net', '—')} | "
                     f"{item.get('fees_total', '—')} | {combined} | {item.get('max_drawdown', '—')} | {counts} |")
    detail_lines = []
    for item in report['scenarios']:
        for key, label in (('unfilled', '未成交'), ('rejected', '被拒'), ('incomplete', '缺输入')):
            for entry in item.get(key, []):
                detail_lines.append(f"- {item['scenario_id']} {label}：`{entry.get('intent_id')}` "
                                    f"{entry.get('side', '')} {entry.get('reason')}")
        if item.get('open_position_note'):
            detail_lines.append(f"- {item['scenario_id']}：{item['open_position_note']}")
        if item.get('status_reason'):
            detail_lines.append(f"- {item['scenario_id']}：{item['status_reason']}")
    if detail_lines:
        lines += ['', '### 路径明细（未成交/被拒/缺输入/未平仓）', ''] + detail_lines
    lines += ['', '## 限制', '']
    lines += [f'- {item}' for item in report['limitations']]
    lines.append('')
    return '\n'.join(lines)
