from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
import json
import pytest

from trademind.config import AppConfig, load_config
from trademind.market.base import BEIJING, MinuteBar
from trademind.market.quality import QualityReport
from trademind.market.router import DatasetRouter
from trademind.pipeline import Pipeline, DecisionResult
from trademind import runner

POOL = ['513050.SH', '513100.SH', '518880.SH']
NOW = datetime(2026,10,8,10,0,tzinfo=BEIJING)


def cfg(tmp_path):
    result = AppConfig()
    result.t0.enabled = True
    result.universe = POOL[:]
    result.llm.provider = 'stub'
    result.data.provider_pref = ['fixture']
    result.storage.url = f'sqlite:///{tmp_path}/pool.db'
    return result


def test_three_t0_pool_and_t1_rejected(tmp_path):
    config = load_config('config/t0-shadow.example.toml', load_credentials=False)
    assert config.universe == config.t0.whitelist == POOL
    assert config.t0.initial_cash == 500
    assert config.t0.model_input_cny_per_million == Decimal('2')
    assert runner._t0_instruments(config) == POOL
    config.universe.append('510050.SH')
    config.t0.whitelist.append('510050.SH')
    with pytest.raises(ValueError):
        runner._t0_instruments(config)


def test_default_paper_checks_all_even_when_one_fails(tmp_path, monkeypatch, capsys):
    config = cfg(tmp_path)
    monkeypatch.setattr(runner, '_load', lambda: config)
    seen = []
    def decide(self, iid, at):
        seen.append(iid)
        return DecisionResult('test',iid,at,'', error='missing data' if iid==POOL[0] else None)
    monkeypatch.setattr(Pipeline, 'run_decision', decide)
    monkeypatch.setattr(Pipeline, 'poll_fills', lambda *a: [])
    assert runner.cmd_t0_paper(steps=1) == 1
    result = json.loads(capsys.readouterr().out)
    assert seen == result['monitored_instruments'] == POOL
    assert len(result['results']) == 3
    assert result['cash'] == '500'


def test_explicit_single_selection_preserved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, '_load', lambda: cfg(tmp_path))
    seen = []
    def decide(self, iid, at):
        seen.append(iid)
        return DecisionResult('test',iid,at,'')
    monkeypatch.setattr(Pipeline, 'run_decision', decide)
    monkeypatch.setattr(Pipeline, 'poll_fills', lambda *a: [])
    assert runner.cmd_t0_paper(POOL[1], steps=1) == 2
    assert seen == [POOL[1]]


def test_data_only_cannot_load_dotenv_or_construct_models(tmp_path, monkeypatch, capsys):
    import trademind.config as config_module
    monkeypatch.setattr(config_module, 'dotenv_values', Mock(side_effect=AssertionError('do not read dotenv')))
    monkeypatch.setattr(runner, '_config_path', 'config/t0-shadow.example.toml')
    monkeypatch.setattr(Pipeline, '__init__', Mock(side_effect=AssertionError('no model pipeline')))
    class Router:
        last_provider_by_dataset = {'minute_bars_1m':'test'}
        def __init__(self, **kw): pass
        def get_minute_bars(self, iid, start, end, **kw):
            if iid == POOL[1]: raise RuntimeError('test data failure')
            at = end-timedelta(seconds=60)
            b = MinuteBar(instrument_id=iid, trade_date=at.date(), bar_time=at,
                          open=1,high=1,low=1,close=1,volume=100,amount=100,
                          provider='test',source_time=at,fetched_at=end,available_at=end)
            return [b], QualityReport()
    monkeypatch.setattr('trademind.market.router.DatasetRouter', Router)
    assert runner.cmd_t0_data() == 1
    result = json.loads(capsys.readouterr().out)
    assert result['model_calls'] == 0
    assert result['monitored_instruments'] == POOL
    assert [r['status'] for r in result['results']] == ['ok','error','ok']


def test_live_observation_reused_only_after_available_time(monkeypatch):
    router = DatasetRouter(pref=['akshare'])
    raw = MinuteBar(instrument_id=POOL[0],trade_date=NOW.date(),bar_time=NOW-timedelta(minutes=1),
                    open=1,high=1,low=1,close=1,volume=100,amount=100,provider='test',
                    source_time=NOW-timedelta(minutes=1),fetched_at=NOW+timedelta(seconds=1),
                    available_at=NOW+timedelta(seconds=1))
    adapter = Mock()
    adapter.get_minute_bars.return_value=[raw]
    monkeypatch.setattr(router,'_get',lambda name:adapter)
    start=NOW-timedelta(days=2)
    router.get_minute_bars(POOL[0],start,NOW)
    before,_=router.get_minute_bars(POOL[0],start,NOW+timedelta(microseconds=1))
    after,_=router.get_minute_bars(POOL[0],start+timedelta(seconds=2),NOW+timedelta(seconds=2))
    assert not before and after == [raw]
    assert adapter.get_minute_bars.call_count==1
    router.get_minute_bars(POOL[0],start+timedelta(seconds=40),NOW+timedelta(seconds=40))
    assert adapter.get_minute_bars.call_count==2


def test_etf_errors_are_not_hidden_or_fallback_to_stock(monkeypatch):
    from trademind.market.adapters.live import AKShareAdapter
    from trademind.errors import DataUnavailable
    import requests
    sdk = Mock()
    sdk.fund_etf_hist_min_em.side_effect = requests.exceptions.ProxyError('TEST_PLACEHOLDER')
    monkeypatch.setattr('trademind.market.adapters.live._require', lambda name:sdk)
    adapter = AKShareAdapter()
    with pytest.raises(DataUnavailable, match='ProxyError'):
        adapter.get_minute_bars(POOL[0],NOW-timedelta(days=2),NOW)
    sdk.stock_zh_a_hist_min_em.assert_not_called()
    router = DatasetRouter(pref=['akshare'])
    monkeypatch.setattr(router,'_get',lambda name:adapter)
    with pytest.raises(RuntimeError):
        router.get_minute_bars(POOL[0],NOW-timedelta(days=2),NOW)
    assert router.last_errors_by_dataset['minute_bars']==[{'provider':'akshare','error_type':'ProxyError'}]
