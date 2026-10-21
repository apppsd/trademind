from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from trademind import runner
from trademind.config import MarketAccessConfig, load_config
from trademind.market import multi_asset as m

NOW = datetime.now(timezone.utc)


def quote(kind='etf', iid='513050.SH', price='1'):
    return m.AssetQuote(iid, kind, 'test', 'HKD' if kind == 'hk_stock' else 'CNY',
                        Decimal(price), None, NOW, NOW, 'test')


def sdk():
    return SimpleNamespace(
        fund_etf_spot_em=Mock(return_value=pd.DataFrame([{'代码': '513050', '最新价': 1}, {'代码':'510050','最新价':2}])),
        fund_lof_spot_em=Mock(return_value=pd.DataFrame([{'代码': '162411', '最新价': 1}])),
        bond_zh_hs_cov_spot=Mock(return_value=pd.DataFrame([{'symbol': 'sh110001', 'trade': 110, 'ticktime':'15:00:00'}])),
        stock_hk_spot_em=Mock(return_value=pd.DataFrame([{'代码':'00700', '最新价':500}])),
        futures_symbol_mark=Mock(return_value=pd.DataFrame([{'symbol':'螺纹钢','mark':'shfe_rb'}])),
        futures_zh_realtime=Mock(return_value=pd.DataFrame([{'symbol':'rb2610','trade':3200},{'symbol':'RB0','trade':3200}]))
    )


def test_all_market_discovery_native_currency_unknown_rules():
    api = sdk()
    report = m.MultiAssetMarket(MarketAccessConfig(), api).scan()
    assert report['instrument_count'] == 6
    assert {a['kind'] for a in report['assets']} == set(m.MARKETS)
    assert report['purchase_conditions_met_count'] == report['paper_candidate_count'] == 0
    assert report['status'] == 'incomplete'  # synthetic RB0 rejected, not buyable
    by_id = {a['instrument_id']:a for a in report['assets']}
    assert by_id['00700.HK']['currency'] == 'HKD'
    assert by_id['00700.HK']['execution_supported'] is False
    assert by_id['110001.SH']['source_time'] is None
    assert by_id['110001.SH']['freshness'] == 'unknown'
    assert by_id['510050.SH']['t0'] is False
    assert by_id['162411.SZ']['t0'] is None
    api.futures_zh_realtime.assert_called_once_with(symbol='螺纹钢')


def test_permission_fees_freshness_and_cash_are_independent():
    cfg = MarketAccessConfig(confirmed_permissions=['cn_fund'])
    q = quote(price='3')
    result = m.eligibility(q, cfg, {})
    assert 'fees_unverified' in result['blocking_reasons']
    assert not result['purchase_conditions_met']
    rules = {q.instrument_id:{'fee_buffer':'1','source_url':'https://www.sse.com.cn/'}}
    result = m.eligibility(q, cfg, rules, NOW)
    assert result['minimum_with_fee_buffer'] == '301'
    assert result['purchase_conditions_met'] and result['paper_candidate']
    cfg.cash_cny = Decimal('300')
    assert 'insufficient_cash' in m.eligibility(q, cfg, rules)['blocking_reasons']
    cfg.cash_cny = Decimal('500')
    q.source_time = None
    result = m.eligibility(q, cfg, rules)
    assert result['purchase_conditions_met'] and not result['paper_candidate']


def test_cny_never_spent_as_hkd_and_futures_require_margin():
    cfg = MarketAccessConfig(confirmed_permissions=['hk_stock', 'futures'], cash_cny=Decimal('100000'))
    hk = quote('hk_stock','00700.HK','500')
    rules = {hk.instrument_id:{'t0':True,'lot_size':100,'fee_buffer':10,'source_url':'https://www.hkex.com.hk/'}}
    result = m.eligibility(hk,cfg,rules)
    assert result['available_cash'] == '0' and 'insufficient_cash' in result['blocking_reasons']
    fut = quote('futures','RB2610.SHFE','3000')
    rules[fut.instrument_id] = {'t0':True,'lot_size':1,'fee_buffer':10,'source_url':'https://www.shfe.cn/'}
    assert 'margin_contract_unverified' in m.eligibility(fut,cfg,rules)['blocking_reasons']
    rules[fut.instrument_id].update(contract_multiplier=10, margin_rate='0.15')
    result = m.eligibility(fut,cfg,rules)
    assert result['minimum_with_fee_buffer'] == '4510.00'
    assert result['purchase_conditions_met'] and not result['execution_supported']
    assert not result['paper_candidate']


@pytest.mark.parametrize('value', ['NaN','Infinity','--',None])
def test_invalid_price_cannot_pass(value):
    records, rejected = m.normalize([{'代码':'513050','最新价':value}], 'etf','test',NOW)
    assert not rejected and records[0].price is None
    result = m.eligibility(records[0], MarketAccessConfig(), {})
    assert 'quote_unavailable' in result['blocking_reasons']


def test_failure_isolated_and_exception_content_not_reported():
    api = sdk()
    api.fund_etf_spot_em.side_effect = requests.exceptions.ProxyError('do not print source details')
    result = m.MultiAssetMarket(MarketAccessConfig(),api).scan()
    assert result['status'] == 'incomplete' and result['instrument_count'] == 4
    assert result['sources'][0]['error_type'] == 'ProxyError'
    assert 'do not print' not in json.dumps(result)
    api.stock_hk_spot_em.assert_called_once()


def test_deadline_reports_unscanned_and_restores_http(monkeypatch):
    api = sdk()
    cfg = MarketAccessConfig(markets=['etf','lof'])
    # Creation at 0, first source at 0, subsequent source at 61.
    ticks = iter([0,0,61])
    monkeypatch.setattr(m.time,'monotonic',lambda:next(ticks))
    original = requests.Session.request
    result = m.MultiAssetMarket(cfg,api).scan()
    assert result['sources'][1]['status'] == 'not_scanned'
    assert requests.Session.request is original
    api.fund_lof_spot_em.assert_not_called()


def test_bounded_sdk_request_timeout_and_proxy_preserved(monkeypatch):
    original = Mock(return_value='result')
    monkeypatch.setattr(requests.Session,'request',original)
    monkeypatch.setattr(m.time,'monotonic',lambda:10)
    session = requests.Session()
    with m.bounded_requests(20,12):
        assert session.get('https://example.test/',timeout=100) == 'result'
        assert session.trust_env is True
    assert original.call_args.kwargs['timeout'] == 10
    assert requests.Session.request is original


@pytest.mark.parametrize('kind,iid,endpoint,symbol', [
    ('etf','513050.SH','fund_etf_hist_min_em','513050'),
    ('lof','162411.SZ','fund_lof_hist_min_em','162411'),
    ('convertible_bond','110001.SH','bond_zh_hs_cov_min','sh110001'),
    ('hk_stock','00700.HK','stock_hk_hist_min_em','00700'),
    ('futures','RB2610.SHFE','futures_zh_minute_sina','RB2610'),
])
def test_minute_routes_and_ohlc_no_fabrication(kind,iid,endpoint,symbol):
    good = {'datetime':(NOW-timedelta(minutes=1)).isoformat(),'open':1,'high':2,'low':1,'close':2,'volume':100}
    future = dict(good,datetime=(NOW+timedelta(days=1)).isoformat())
    bad = dict(good,high=0)
    fn = Mock(return_value=pd.DataFrame([good,future,bad]))
    api = SimpleNamespace(**{endpoint:fn})
    result = m.MultiAssetMarket(MarketAccessConfig(),api).minute_bars(iid,kind)
    assert result['rejected_rows'] == 2 and len(result['bars']) == 1
    assert result['bars'][0]['amount_native'] is None
    assert result['bars'][0]['amount_currency'] == ('HKD' if kind == 'hk_stock' else 'CNY')
    assert fn.call_args.kwargs['symbol'] == symbol
    assert result['replay_point_in_time'] is False


def test_cli_credentials_isolated_and_report_written(tmp_path,monkeypatch,capsys):
    import trademind.config as config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config,'dotenv_values',Mock(side_effect=AssertionError('no dotenv')))
    monkeypatch.setattr(runner,'_config_path',None)
    monkeypatch.setattr(m,'MultiAssetMarket',Mock(return_value=SimpleNamespace(scan=lambda:{
        'status':'success','model_calls':0,'assets':[], 'instrument_count':0})))
    assert runner.main(['market-data']) == 0
    assert json.loads(capsys.readouterr().out)['model_calls'] == 0
    assert json.loads((tmp_path/'data/runtime/market-observation.json').read_text())['assets'] == []


def test_configuration_rules_unknown_permissions_and_expiry():
    cfg = load_config('config/market-access.example.toml',load_credentials=False)
    assert cfg.market_access.cash_cny == 500
    assert cfg.market_access.confirmed_permissions == []
    with pytest.raises(ValueError):
        m.MultiAssetMarket(MarketAccessConfig(confirmed_permissions=['all_markets']))
    q = quote()
    with pytest.raises(ValueError):
        m.eligibility(q,cfg.market_access,{q.instrument_id:{'lot_size':100}})
    rules = {q.instrument_id:{'source_url':'https://www.sse.com.cn/','effective_until':'2000-01-01'}}
    assert 'rules_expired' in m.eligibility(q,cfg.market_access,rules)['blocking_reasons']


def test_observation_api_does_not_initialize_models(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import importlib
    api = importlib.import_module('trademind.api.app')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(api,'init_app',Mock(side_effect=AssertionError('no model startup')))
    client = TestClient(api.app)
    assert client.get('/api/market-observations').json()['status'] == 'not_scanned'
    path = tmp_path/'data/runtime/market-observation.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'status':'success','assets':[{'kind':'etf'}, {'kind':'futures'}]}))
    result = client.get('/api/market-observations?kind=futures&limit=1').json()
    assert result['assets'] == [{'kind':'futures'}]
    assert client.get('/api/market-observations?kind=invalid').status_code == 400
    assert client.get('/api/market-observations?limit=10000').status_code == 400


def test_real_sdk_futures_catalog_exchange_names():
    api = sdk()
    api.futures_symbol_mark.return_value = pd.DataFrame([{'exchange':'郑州商品交易所', 'symbol':'PTA', 'mark':'pta_qh'},
                                                        {'exchange':'上海期货交易所','symbol':'原油','mark':'sc_qh'}])
    api.futures_zh_realtime.side_effect = [pd.DataFrame([{'symbol':'TA610','trade':5000}]),
                                          pd.DataFrame([{'symbol':'sc2610','trade':500}])]
    result = m.MultiAssetMarket(MarketAccessConfig(markets=['futures']),api).scan()
    assert result['status'] == 'success'
    assert {a['instrument_id'] for a in result['assets']} == {'TA610.CZCE','SC2610.INE'}


@pytest.mark.parametrize('iid',['110072.SH','123001.SZ','00700.HK','RB2610.SHFE','162411.SZ'])
def test_other_assets_cannot_be_misclassified_as_a_share(iid):
    from trademind.market.profiles import get_profile
    with pytest.raises(ValueError):
        get_profile(iid)
