"""TASK-20261025-05: offline breakeven table + isolated fill-stress replay."""
import json
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trademind.evaluation.cost_stress import (SCENARIOS, breakeven_table, evaluate_bundle,
                                              parse_bundle, replay_scenario)

T0 = datetime(2026, 10, 23, 6, 50, 0, tzinfo=timezone.utc)


def synth_bundle(**overrides):
    """One sufficient synthetic BUY→SELL round trip on 513050.SH (2026-10-23 local prices)."""
    bars = [{'instrument_id': '513050.SH', 'bar_time': (T0 + timedelta(minutes=n)).isoformat(),
             'open': '0.949', 'high': '0.951', 'low': '0.948', 'close': '0.950',
             'available_at': (T0 + timedelta(minutes=n)).isoformat(),
             'volume': '50000'} for n in range(1, 8)]
    data = {
        'bundle_version': 2, 'source': 'synthetic round-trip fixture (test only)',
        'as_of': (T0 + timedelta(minutes=10)).isoformat(),
        'account_config': {'initial_cash': '1000', 'min_cash_reserve': '0',
                           'max_single_exposure': '900', 'max_total_exposure': '900'},
        'fee_profile': {'commission_rate': '0.0001', 'commission_min': '0.1',
                        'stamp_duty_rate_sell': '0', 'transfer_fee_rate': '0.00001'},
        'model_cost': {'settled_cny': '0.20', 'pending_cny': '0', 'unknown': False},
        'intents': [
            {'intent_id': 'i-buy', 'instrument_id': '513050.SH', 'side': 'BUY',
             'limit_price': '0.955', 'quantity': '100', 'intended_at': T0.isoformat()},
            {'intent_id': 'i-sell', 'instrument_id': '513050.SH', 'side': 'SELL',
             'limit_price': '0.945', 'quantity': '100',
             'intended_at': (T0 + timedelta(minutes=4)).isoformat()},
        ],
        'bars': bars,
    }
    data.update(overrides)
    return data


def scenario(report, scenario_id):
    return next(item for item in report['scenarios'] if item['scenario_id'] == scenario_id)


def test_breakeven_table_matches_hand_calculation():
    rows = {(row['notional_cny'], row['slippage_bps'], row['commission_min_cny']): row
            for row in breakeven_table()}
    # 100元 @5bps, min 0.1: 买费 max(0.01,0.1)+0.001=0.10；卖费同；滑点 2×100×0.0005=0.10 → 0.30。
    row = rows[('100', 5, '0.1')]
    assert row['buy_fee'] == '0.10' and row['sell_fee'] == '0.10'
    assert row['round_trip_cost'] == '0.30' and row['breakeven_ratio'] == '0.0030'
    # 200元 @5bps, min 0.1: 0.10+0.10+0.20 = 0.40 → 0.20%。
    row = rows[('200', 5, '0.1')]
    assert row['round_trip_cost'] == '0.40' and row['breakeven_ratio'] == '0.0020'
    # 最低佣金 5 元/边时 100 元往返成本 10.10（10.1%）——小额往返被最低佣金摧毁。
    assert rows[('100', 5, '5')]['round_trip_cost'] == '10.10'
    assert rows[('200', 5, '5')]['breakeven_ratio'] == '0.0510'


def test_higher_cost_never_improves_net_when_fills_identical():
    report = evaluate_bundle(synth_bundle())
    nets = [Decimal(scenario(report, sid)['trading_cash_net'])
            for sid in ('baseline', 'slippage-10bps', 'slippage-20bps')]
    assert nets[0] >= nets[1] > nets[2]  # adverse tick rounding can make nearby bps equal
    fees = [Decimal(scenario(report, sid)['fees_total'])
            for sid in ('baseline', 'slippage-10bps', 'slippage-20bps')]
    assert fees[0] == fees[1] == fees[2]  # same quantities/prices? no—prices differ with slip
    # Conservative tick rounding: BUY ceil(0.9514755)=0.952; SELL floor(0.947526)=0.947.
    baseline = scenario(report, 'baseline')
    assert baseline['fills_count'] == 2
    buy, sell = baseline['fills']
    assert buy['price'] == '0.952' and sell['price'] == '0.947'
    expected = (Decimal('-0.952') * 100 - Decimal(buy['fee'])
                + Decimal('0.947') * 100 - Decimal(sell['fee']))
    assert Decimal(baseline['trading_cash_net']) == expected


def test_worse_fill_can_break_the_path_without_forced_monotonicity():
    # limit exactly at the slippage frontier: 5bps fills, 20bps does not.
    bundle = synth_bundle()
    bundle['intents'][0]['limit_price'] = '0.952'
    bundle['intents'][1]['limit_price'] = '0.947'
    report = evaluate_bundle(bundle)
    assert scenario(report, 'baseline')['fills_count'] == 2
    worse = scenario(report, 'slippage-20bps')
    assert worse['status'] == 'insufficient_data'
    assert any('未触及限价' in entry['reason'] for entry in worse['unfilled'])
    # No cross-path NAV monotonicity is asserted — only the explicit path change.


def test_delay_uses_only_bars_after_intent_and_skips_extra_bars():
    report = evaluate_bundle(synth_bundle())
    base_buy = scenario(report, 'baseline')['fills']
    delayed = scenario(report, 'delay-1')['fills']
    assert base_buy and delayed
    # delay-1 must fill on the SECOND eligible bar (a different bar_time), never an earlier one.
    first_eligible = T0 + timedelta(minutes=1)
    assert base_buy[0]['bar_time'] == first_eligible.isoformat()
    assert delayed[0]['bar_time'] == (T0 + timedelta(minutes=2)).isoformat()


def test_limit_price_blocks_fills_outside_limit():
    bundle = synth_bundle()
    bundle['intents'][0]['limit_price'] = '0.940'  # below every bar's adverse price
    report = evaluate_bundle(bundle)
    item = scenario(report, 'baseline')
    assert item['fills_count'] == 0
    assert any('未触及限价' in entry['reason'] for entry in item['unfilled'])


def test_replay_recomputes_cash_and_blocks_illegal_orders():
    bundle = synth_bundle()
    bundle['account_config']['initial_cash'] = '50'  # cannot afford the BUY
    report = evaluate_bundle(bundle)
    item = scenario(report, 'baseline')
    assert item['fills_count'] == 0
    assert any('重算现金不足' in entry['reason'] for entry in item['rejected'])
    # The downstream SELL is then illegal too (no position) — legality chained, not assumed.
    assert any('重算持仓不足' in entry['reason'] for entry in item['rejected'])


def test_zero_trades_still_carry_model_cost_and_unknown_stays_unknown():
    empty = synth_bundle(intents=[])
    report = evaluate_bundle(empty)
    item = scenario(report, 'baseline')
    assert item['status'] == 'insufficient_data'
    assert item['trading_cash_net'] == '0'
    assert Decimal(item['model_cost_cny']) == Decimal('0.20')
    assert Decimal(item['combined_net']) == Decimal('-0.20')
    unknown = evaluate_bundle(synth_bundle(model_cost={'settled_cny': '0', 'pending_cny': '0',
                                                       'unknown': True}))
    assert scenario(unknown, 'baseline')['combined_net'] is None
    assert scenario(unknown, 'baseline')['combined_net_unknown'] is True


def test_fees_deducted_exactly_once_and_ledger_recomputes():
    report = evaluate_bundle(synth_bundle())
    baseline = scenario(report, 'baseline')
    fills = baseline['fills']
    fees_sum = sum(Decimal(fill['fee']) for fill in fills)
    assert Decimal(baseline['fees_total']) == fees_sum
    # cash path: initial − (buy notional + buy fee) + (sell notional − sell fee)
    cash = Decimal('1000')
    for fill in fills:
        notional = Decimal(fill['price']) * Decimal(fill['quantity'])
        cash = cash - notional - Decimal(fill['fee']) if fill['side'] == 'BUY' \
            else cash + notional - Decimal(fill['fee'])
    assert cash - Decimal('1000') == Decimal(baseline['trading_cash_net'])


def test_participation_caps_fill_by_volume_and_reports_remainder():
    bundle = synth_bundle()
    bundle['intents'][0]['quantity'] = '500'
    bundle['intents'][1]['quantity'] = '500'
    bundle['bars'] = [dict(bar, volume='1000') for bar in bundle['bars']]
    report = evaluate_bundle(bundle)
    item = scenario(report, 'participation-10pct')
    assert item['fills'][0]['quantity'] == '100'  # 1000 × 10% = 100 shares this bar
    remainders = [entry for entry in item['unfilled'] if '部分成交' in entry['reason']]
    assert len(remainders) == 1 and remainders[0]['quantity'] == '400'
    # The archived SELL requests 500 but only 100 were acquired. Do not
    # silently resize that order or pretend that future holdings exist.
    assert any(item['intent_id'] == 'i-sell' for item in item['rejected'])
    assert item['open_positions'] == {'513050.SH': '100'}
    # baseline with the same archive has no participation cap and fills the full 500.
    assert scenario(report, 'baseline')['fills'][0]['quantity'] == '500'


def test_missing_bars_report_incomplete_without_future_information():
    bundle = synth_bundle()
    bundle['bars'] = bundle['bars'][:5]  # sell intent at T0+4min needs bar 5+ for delay-2
    report = evaluate_bundle(bundle)
    delayed = scenario(report, 'delay-2')
    assert delayed['status'] in ('incomplete', 'insufficient_data')
    assert any('可见bar不足' in entry['reason'] for entry in delayed['incomplete'])
    baseline = scenario(report, 'baseline')
    assert baseline['status'] == 'ok'  # baseline still has both bars available


def test_isolated_deterministic_and_fingerprinted():
    first = evaluate_bundle(synth_bundle())
    second = evaluate_bundle(synth_bundle())
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == json.dumps(
        second, sort_keys=True, ensure_ascii=False)
    assert len(first['input_sha256']) == 64
    assert SCENARIOS[0]['id'] == 'baseline' and len(SCENARIOS) == 8


def test_parse_bundle_rejects_bad_contracts():
    bad = [
        {},
        {**synth_bundle(), 'bundle_version': 99},
        {**synth_bundle(), 'account_config': {'initial_cash': '0'}},
        {**synth_bundle(), 'fee_profile': {}},
        {**synth_bundle(), 'intents': [{'instrument_id': 'x', 'side': 'SHORT',
                                        'limit_price': '1', 'quantity': '1',
                                        'intended_at': T0.isoformat()}]},
        {**synth_bundle(), 'intents': [{'instrument_id': 'x', 'side': 'BUY',
                                        'limit_price': '1', 'quantity': '1',
                                        'intended_at': '2026-10-23 06:50:00'}]},
    ]
    for data in bad:
        try:
            parse_bundle(data)
        except ValueError:
            continue
        raise AssertionError(f'expected rejection: {data!r}')


def test_future_sale_cannot_finance_an_earlier_other_instrument_buy():
    data = synth_bundle()
    data['account_config']['initial_cash'] = '150'
    buy = dict(data['intents'][0], quantity='100', limit_price='1.100')
    sell = dict(data['intents'][1], quantity='100', limit_price='0.900',
                intended_at=(T0 + timedelta(minutes=2)).isoformat())
    other = dict(buy, intent_id='other', instrument_id='513100.SH',
                 intended_at=(T0 + timedelta(minutes=3)).isoformat())
    data['intents'] = [buy, sell, other]
    data['bars'] = [dict(data['bars'][0], instrument_id=iid,
                         bar_time=(T0 + timedelta(minutes=offset)).isoformat(),
                         available_at=(T0 + timedelta(minutes=offset)).isoformat(),
                         high='1.001', low='0.999', close='1')
                    for iid, offset in [('513050.SH', 1), ('513050.SH', 10), ('513100.SH', 4)]]
    report = scenario(evaluate_bundle(data), 'baseline')
    assert [fill['intent_id'] for fill in report['fills']] == ['i-buy', 'i-sell']
    assert any(entry['intent_id'] == 'other' for entry in report['rejected'])
    assert [fill['available_at'] for fill in report['fills']] == sorted(fill['available_at'] for fill in report['fills'])


def test_visibility_cutoff_and_sell_before_buy_fill_are_respected():
    data = synth_bundle()
    for bar in data['bars']:
        bar['available_at'] = (T0 + timedelta(minutes=9)).isoformat()
    report = scenario(evaluate_bundle(data), 'baseline')
    assert not any(fill['side'] == 'SELL' for fill in report['fills'])
    assert any('持仓不足' in item['reason'] for item in report['rejected'])
    data['as_of'] = (T0 + timedelta(minutes=8)).isoformat()
    report = scenario(evaluate_bundle(data), 'baseline')
    assert not report['fills'] and report['status'] == 'incomplete'


def test_pending_or_missing_model_cost_cannot_become_known_zero():
    data = synth_bundle(intents=[])
    data['model_cost']['pending_cny'] = '5'
    item = scenario(evaluate_bundle(data), 'baseline')
    assert item['combined_net'] is None
    assert item['net_after_settled_model_estimate'] == '-0.20'
    data.pop('model_cost')
    assert scenario(evaluate_bundle(data), 'baseline')['combined_net'] is None


@pytest.mark.parametrize('field,value', [('quantity', '-100'), ('quantity', '1'),
                                        ('limit_price', '0'), ('limit_price', '0.9505')])
def test_invalid_order_cannot_create_cash_or_illegal_fills(field, value):
    data = synth_bundle()
    data['intents'][0][field] = value
    with pytest.raises(ValueError):
        evaluate_bundle(data)


def test_shared_bar_participation_is_not_reused_per_order():
    data = synth_bundle()
    data['intents'] = [data['intents'][0], dict(data['intents'][0], intent_id='second')]
    for bar in data['bars']:
        bar['volume'] = '1000'
    item = scenario(evaluate_bundle(data), 'participation-10pct')
    assert sum(Decimal(fill['quantity']) for fill in item['fills']) == 100
    assert any('共享容量' in order['reason'] for order in item['unfilled'])
    data['bars'][0]['volume'] = '999'
    assert not scenario(evaluate_bundle(data), 'participation-10pct')['fills']


def test_unclosed_roundtrip_reports_equity_instead_of_cash_loss():
    data = synth_bundle()
    data['intents'][0]['quantity'] = '200'
    item = scenario(evaluate_bundle(data), 'baseline')
    assert item['open_positions'] == {'513050.SH': '100'}
    assert item['status'] == 'insufficient_data'
    assert Decimal(item['trading_equity_net']) > Decimal(item['trading_cash_net'])
    assert Decimal(item['max_drawdown']) >= 0


def test_cash_reserve_and_exposure_limits_are_checked_at_submission():
    for limits in ({'min_cash_reserve': '950'}, {'max_single_exposure': '90'}, {'max_total_exposure': '90'}):
        data = synth_bundle()
        data['account_config'].update(limits)
        item = scenario(evaluate_bundle(data), 'baseline')
        assert not item['fills'] and item['rejected']


def test_missing_bar_provenance_and_bad_prices_or_fees_are_rejected():
    data = synth_bundle()
    data['bars'][0].pop('available_at')
    with pytest.raises(ValueError):
        evaluate_bundle(data)
    data = synth_bundle()
    data['bars'][0]['low'] = '10'
    with pytest.raises(ValueError):
        evaluate_bundle(data)
    data = synth_bundle()
    data['fee_profile']['commission_min'] = '-1'
    with pytest.raises(ValueError):
        evaluate_bundle(data)


def test_partial_sell_reports_remaining_holding_without_overselling():
    data = synth_bundle()
    for intent in data['intents']:
        intent['quantity'] = '500'
    for bar in data['bars'][4:]:
        bar['volume'] = '1000'
    item = scenario(evaluate_bundle(data), 'participation-10pct')
    assert [fill['quantity'] for fill in item['fills']] == ['500', '100']
    assert item['open_positions'] == {'513050.SH': '400'}
    assert item['status'] == 'insufficient_data'


def test_single_price_bar_is_not_assumed_to_have_fillable_liquidity():
    data = synth_bundle()
    for bar in data['bars']:
        bar.update(high='0.950', low='0.950', close='0.950')
    item = scenario(evaluate_bundle(data), 'baseline')
    assert not item['fills']
    assert any('单价bar' in order['reason'] for order in item['unfilled'])
