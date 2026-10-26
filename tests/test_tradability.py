"""TASK-20261025-04: pure tradability diagnostics over explicit snapshots."""
import json
from datetime import datetime, timedelta, timezone

from trademind.evaluation.tradability import (evaluate_from_raw, parse_snapshot,
                                              render_markdown)
from trademind.market.profiles import etf_profile, get_profile

AS_OF = datetime(2026, 10, 23, 6, 57, 16, tzinfo=timezone.utc)


def snapshot(**overrides):
    data = {
        'snapshot_version': 1, 'account_id': 'paper-t0-local-1k',
        'as_of': AS_OF.isoformat(), 'cash': '1000', 'reserved_cash': '0',
        'positions': [], 'open_orders': [],
        'limits': {'max_single_exposure': '200', 'max_total_exposure': '300',
                   'min_cash_reserve': '100'},
        'instruments': [{'instrument_id': '513050.SH', 'price': '0.949',
                         'price_source': 'local-1k context_snapshots (read-only)',
                         'price_time': AS_OF.isoformat()}],
    }
    data.update(overrides)
    return data


def run(data, **kwargs):
    # Mirror the CLI resolver contract: rule-source refusal becomes 无核验规则.
    def resolver(instrument_id):
        try:
            return get_profile(instrument_id)
        except ValueError:
            return None
    return evaluate_from_raw(data, resolver, **kwargs)


def test_price_one_yuan_is_affordable_with_fees_and_lot_cap():
    report = run(snapshot(instruments=[{'instrument_id': '513050.SH', 'price': '1.00',
                                        'price_source': 'test', 'price_time': AS_OF.isoformat()}]))
    item = report['instruments'][0]
    assert item['status'] == '资金可达'
    # notional 100 + max(0.01, 0.1) commission + 0.001 transfer, quantized to 0.10.
    assert item['one_lot_notional'] == '100.00' and item['one_lot_fees'] == '0.10'
    assert item['one_lot_cost'] == '100.10'
    # Budget 900 → cash allows 8 lots; single cap 200 → exactly 2 (2×100 = 200);
    # total 300 → 3. The single cap binds at 2.
    assert item['max_affordable_lots'] == 2


def test_exact_principal_without_fees_is_rejected():
    # One lot at 2.00: notional 200. Budget exactly 200 covers principal but not
    # the 200.10 cost (commission min + transfer): 刚好覆盖本金但不足费用必须拒绝.
    lot = {'instrument_id': '513050.SH', 'price': '2.00', 'price_source': 'test',
           'price_time': AS_OF.isoformat()}
    data = snapshot(cash='300', instruments=[lot])
    item = run(data)['instruments'][0]
    assert item['status'] == '仅观察'
    assert any('现金不足' in reason for reason in item['reasons'])
    # One fen more and the same lot fits.
    data['cash'] = '300.10'
    assert run(data)['instruments'][0]['status'] == '资金可达'


def test_price_two_yuan_zero_one_blocked_by_single_cap():
    report = run(snapshot(instruments=[{'instrument_id': '513050.SH', 'price': '2.01',
                                        'price_source': 'test', 'price_time': AS_OF.isoformat()}]))
    item = report['instruments'][0]
    assert item['status'] == '仅观察'
    assert any('单标的上限' in reason for reason in item['reasons'])
    # One lot at 2.01 still fits the 300 total cap — no total-cap reason here;
    # 518880 in the three-ETF test covers the simultaneous-reasons case.


def test_local_sample_three_etfs_match_expected_statuses():
    data = snapshot(instruments=[
        {'instrument_id': '513050.SH', 'price': '0.949', 'price_source': 'local-1k',
         'price_time': AS_OF.isoformat()},
        {'instrument_id': '513100.SH', 'price': '2.241', 'price_source': 'local-1k',
         'price_time': AS_OF.isoformat()},
        {'instrument_id': '518880.SH', 'price': '8.197', 'price_source': 'local-1k',
         'price_time': AS_OF.isoformat()},
    ])
    report = run(data)
    by_id = {item['instrument_id']: item for item in report['instruments']}
    assert by_id['513050.SH']['status'] == '资金可达'
    assert by_id['513050.SH']['max_affordable_lots'] == 2  # single cap binds first
    assert by_id['513100.SH']['status'] == '仅观察'
    assert any('单标的上限' in r for r in by_id['513100.SH']['reasons'])
    assert by_id['518880.SH']['status'] == '仅观察'
    assert any('单标的上限' in r for r in by_id['518880.SH']['reasons'])
    assert any('总敞口上限' in r for r in by_id['518880.SH']['reasons'])
    assert report['summary'] == {'资金可达': 1, '仅观察': 2, '数据不足': 0, 'total': 3}


def test_zero_negative_stale_missing_price_stay_data_insufficient():
    base = {'instrument_id': '513050.SH', 'price_source': 'test'}
    cases = [
        ({**base, 'price': '0', 'price_time': AS_OF.isoformat()}, '行情无效'),
        ({**base, 'price': '-1', 'price_time': AS_OF.isoformat()}, '行情无效'),
        ({**base, 'price': None}, '行情缺失'),
        ({**base, 'price': '0.949'}, '缺少价格可见时间'),
        ({**base, 'price': '0.949', 'price_time': (AS_OF - timedelta(seconds=301)).isoformat()},
         '行情陈旧'),
    ]
    for entry, marker in cases:
        item = run(snapshot(instruments=[entry]))['instruments'][0]
        assert item['status'] == '数据不足', entry
        assert any(marker in reason for reason in item['reasons']), entry


def test_stale_inside_tolerance_is_fresh_and_future_price_time_rejected():
    fresh = {'instrument_id': '513050.SH', 'price': '0.949', 'price_source': 'test',
             'price_time': (AS_OF - timedelta(seconds=299)).isoformat()}
    assert run(snapshot(instruments=[fresh]), max_price_age_seconds=300)['instruments'][0]['status'] == '资金可达'
    ahead = dict(fresh, price_time=(AS_OF + timedelta(seconds=1)).isoformat())
    item = run(snapshot(instruments=[ahead]))['instruments'][0]
    assert item['status'] == '数据不足' and any('行情陈旧' in r for r in item['reasons'])


def test_unknown_instrument_reports_missing_rules_instead_of_guessing():
    entry = {'instrument_id': '999999.SH', 'price': '1.00', 'price_source': 'test',
             'price_time': AS_OF.isoformat()}
    item = run(snapshot(instruments=[entry]))['instruments'][0]
    assert item['status'] == '数据不足'
    assert any('无核验规则' in reason for reason in item['reasons'])


def test_non_t0_settlement_is_observation_only_even_when_affordable():
    profile = etf_profile('513050.SH').model_copy(update={'settlement_rule': 'T+1'})
    entry = {'instrument_id': '513050.SH', 'price': '0.949', 'price_source': 'test',
             'price_time': AS_OF.isoformat()}
    report = evaluate_from_raw(snapshot(instruments=[entry]), lambda iid: profile)
    item = report['instruments'][0]
    assert item['status'] == '仅观察'
    assert any('非T0' in reason for reason in item['reasons'])


def test_existing_position_shrinks_single_cap_room():
    entry = {'instrument_id': '513050.SH', 'price': '1.00', 'price_source': 'test',
             'price_time': AS_OF.isoformat()}
    data = snapshot(positions=[{'instrument_id': '513050.SH', 'quantity': '100'}],
                    instruments=[entry])
    item = run(data)['instruments'][0]
    # Position MV 100 leaves single-cap room 100; one more lot (notional 100 ≤ 100) fits exactly.
    assert item['status'] == '资金可达' and item['max_affordable_lots'] == 1
    data['positions'] = [{'instrument_id': '513050.SH', 'quantity': '101'}]
    item = run(data)['instruments'][0]
    assert item['status'] == '仅观察'
    assert any('单标的上限' in reason for reason in item['reasons'])


def test_position_without_price_gets_valuation_note():
    entry = {'instrument_id': '513050.SH', 'price': '0.949', 'price_source': 'test',
             'price_time': AS_OF.isoformat()}
    data = snapshot(positions=[{'instrument_id': '513100.SH', 'quantity': '100'}],
                    instruments=[entry])
    report = run(data)
    assert report['position_valuation_note'] and '513100.SH' in report['position_valuation_note']
    assert report['instruments'][0]['status'] == '数据不足'


def test_other_position_with_stale_or_future_price_never_frees_exposure():
    for offset in (-301, 1):
        data = snapshot(positions=[{'instrument_id': '513100.SH', 'quantity': '10000'}])
        data['instruments'].append({'instrument_id': '513100.SH', 'price': '0.01',
                                    'price_time': (AS_OF + timedelta(seconds=offset)).isoformat()})
        report = run(data)
        assert all(item['status'] == '数据不足' for item in report['instruments'])


def test_duplicate_position_rows_sum_instead_of_overwriting():
    data = snapshot(positions=[{'instrument_id': '513050.SH', 'quantity': '150'},
                               {'instrument_id': '513050.SH', 'quantity': '150'}])
    assert run(data)['instruments'][0]['status'] == '仅观察'


def test_pending_buy_consumes_exposure_but_not_cash_twice():
    data = snapshot(reserved_cash='200.10', open_orders=[{
        'instrument_id': '513100.SH', 'side': 'BUY', 'quantity': '100', 'filled_quantity': '0',
        'limit_price': '2', 'status': 'OPEN'}])
    report = run(data)
    assert report['instruments'][0]['status'] == '资金可达'
    assert report['instruments'][0]['max_affordable_lots'] == 1
    assert report['instruments'][0]['cash_budget'] == '699.90'
    data['open_orders'][0]['limit_price'] = '2.10'
    assert run(data)['instruments'][0]['status'] == '仅观察'
    data['open_orders'][0].pop('quantity')
    assert run(data)['instruments'][0]['status'] == '数据不足'


def test_report_is_deterministic_and_fingerprinted():
    data = snapshot()
    first, second = run(data), run(data)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True)
    assert len(first['input_sha256']) == 64
    data['cash'] = '1001'
    assert run(data)['input_sha256'] != first['input_sha256']


def test_markdown_renders_statuses_and_reasons():
    text = render_markdown(run(snapshot(instruments=[
        {'instrument_id': '513050.SH', 'price': '0.949', 'price_source': 'local-1k',
         'price_time': AS_OF.isoformat()},
        {'instrument_id': '513100.SH', 'price': '2.241', 'price_source': 'local-1k',
         'price_time': AS_OF.isoformat()},
        {'instrument_id': '518880.SH', 'price': None, 'price_source': 'local-1k'},
    ])))
    for marker in ('资金可达性诊断', '资金可达 ≠ 下单授权', '单标的上限', '行情缺失',
                   '513050.SH', '518880.SH'):
        assert marker in text


def test_parse_snapshot_rejects_bad_contracts():
    bad = [
        {},                                   # not even versioned
        {**snapshot(), 'snapshot_version': 2},
        {**snapshot(), 'as_of': '2026-10-23 06:57:16'},          # naive time
        {**snapshot(), 'cash': '-1'},
        {**snapshot(), 'limits': {}},
        {**snapshot(), 'instruments': []},
        {**snapshot(), 'instruments': [{'instrument_id': '513050.SH', 'price': 'abc'}]},
        {**snapshot(), 'instruments': [{'instrument_id': '513050.SH'}, {'instrument_id': '513050.SH'}]},
        {**snapshot(), 'positions': [{'instrument_id': '513050.SH', 'quantity': '-1'}]},
    ]
    for data in bad:
        try:
            parse_snapshot(data)
        except ValueError:
            continue
        raise AssertionError(f'expected rejection: {data!r}')
    parsed = parse_snapshot(snapshot())
    assert parsed['cash'] == 1000 and parsed['as_of'] == AS_OF
