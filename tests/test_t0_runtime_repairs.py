from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import Mock
import json
import pytest

from trademind.config import AppConfig, load_config
from trademind.market.base import BEIJING, MinuteBar
from trademind.pipeline import Pipeline, DecisionResult
from trademind.models.daily_budget import DailyModelBudget
from trademind.models.deepseek import DeepSeekProvider
from trademind.errors import ProviderError
from trademind.domain.orders import Order, Fill
from trademind.domain.enums import OrderSide, OrderStatus, RunMode
from trademind.domain.base import new_id

I = '513050.SH'
NOW = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)


def bar(at, price='1.50'):
    p = D(price)
    return MinuteBar(instrument_id=I, trade_date=at.date(), bar_time=at,
                     open=p, high=p, low=p, close=p, volume=D('1000'), amount=p*1000,
                     provider='test', source_time=at, available_at=at, fetched_at=at)


def pipeline(tmp_path, clock=None):
    cfg = AppConfig()
    cfg.t0.enabled = True
    cfg.llm.provider = 'stub'
    cfg.universe = [I]
    cfg.account_id = 'repair-test'
    cfg.data.provider_pref = ['fixture']
    cfg.storage.url = f'sqlite:///{tmp_path}/repair.db'
    pipe = Pipeline(cfg, clock=clock or (lambda: NOW))
    pipe.ensure_account(at=NOW-timedelta(minutes=30))
    pipe.router.get_daily_bars = Mock(side_effect=AssertionError('T0 must not read daily bars'))
    pipe.router.get_minute_bars = Mock(return_value=([bar(NOW)], None))
    return pipe


def buy_position(pipe, at=NOW-timedelta(minutes=5)):
    oid = new_id('o')
    order = Order(id=oid, trace_id='test', created_at=at, order_id=oid,
                  client_order_id=oid, intent_id='test', guard_id='test', account_id=pipe.cfg.account_id,
                  instrument_id=I, mode=RunMode.PAPER, side=OrderSide.BUY,
                  quantity=D('100'), limit_price=D('1.50'), updated_at=at, status=OrderStatus.ACKNOWLEDGED)
    fill = Fill(id=new_id('f'), trace_id='test', created_at=at, fill_id=new_id('fill'),
                provider_fill_id=new_id('pf'), order_id=oid, fee=D('0.1'),
                quantity=D('100'), price=D('1.50'),
                executed_at=at, available_at=at)
    pipe.ledger.apply_fill(fill, order, at)


def test_minute_decision_and_audit_no_daily_fallback(tmp_path):
    pipe = pipeline(tmp_path)
    result = pipe.run_decision(I, NOW)
    assert result.error is None
    assert result.guard_verdict == 'WAIT_NO_TRADE'
    assert pipe.reference_price(I, NOW) == D('1.50')
    assert pipe.db.query('SELECT * FROM intents')
    assert pipe.db.query("SELECT * FROM control_audit WHERE action='t0_chain'")
    pipe.router.get_daily_bars.assert_not_called()


@pytest.mark.parametrize('bars', [[], [bar(NOW-timedelta(minutes=4))], [bar(NOW+timedelta(minutes=1))]])
def test_missing_stale_future_minutes_block_models(tmp_path, bars):
    pipe = pipeline(tmp_path)
    pipe.router.get_minute_bars.return_value = (bars, None)
    pipe.llm.complete = Mock(side_effect=AssertionError('must not call model'))
    result = pipe.run_decision(I, NOW)
    assert result.error and 'NotFresh' in result.error
    pipe.llm.complete.assert_not_called()


@pytest.mark.parametrize('at,price', [(NOW, '1.48'), (NOW.replace(hour=14, minute=55), '1.50')])
def test_code_risk_exit_without_models_and_fill(tmp_path, at, price):
    tick = [at]
    pipe = pipeline(tmp_path, clock=lambda: tick[0])
    buy_position(pipe)
    pipe.router.get_minute_bars.return_value = ([bar(at, price)], None)
    pipe.llm.complete = Mock(side_effect=AssertionError('exit must not require AI'))
    result = pipe.run_decision(I, at)
    assert result.error is None
    assert result.order_id, result.guard_reasons
    assert result.portfolio_intent.action.value == 'SELL'
    assert result.summary()['action'] == 'SELL'
    assert result.summary()['target_weight'] == '0'
    pipe.llm.complete.assert_not_called()
    tick[0] = at + timedelta(minutes=1)
    pipe.router.get_minute_bars.return_value = ([bar(tick[0], price)], None)
    assert len(pipe.poll_fills(tick[0])) == 1
    assert not pipe.ledger.snapshot(tick[0]).positions
    assert not pipe.poll_fills(tick[0])
    assert pipe.guard.consecutive_losses == 1
    # A real restart means the previous writer process released the account.
    pipe.release_writer_lease()
    restored = Pipeline(pipe.cfg, clock=lambda: tick[0])
    restored.guard.sync_counters(tick[0])
    assert restored.guard.consecutive_losses == 1


def test_budget_shared_persisted_and_daily_reset(tmp_path):
    pipe = pipeline(tmp_path)
    cfg = pipe.cfg.t0
    cfg.daily_decision_call_budget = 2
    cfg.model_input_cny_per_million = D('1')
    cfg.model_output_cny_per_million = D('2')
    tick = [NOW]
    a = DailyModelBudget(pipe.repo, cfg, pipe.cfg.account_id, lambda: tick[0])
    b = DailyModelBudget(pipe.repo, cfg, pipe.cfg.account_id, lambda: tick[0])
    a.reserve_request(100, 100)
    b.reserve_request(100, 100)
    with pytest.raises(ProviderError):
        a.reserve_request(100, 100)
    assert b.used == 2
    tick[0] += timedelta(days=1)
    assert b.used == 0
    b.reserve_request(100, 100)
    assert b.used == 1


def test_guard_approval_counter_restart_and_next_day(tmp_path):
    from trademind.domain.intents import PortfolioIntent
    pipe = pipeline(tmp_path)
    snap = pipe.context_builder.build_minute(I, NOW)
    pi = PortfolioIntent(id=new_id('pi'), trace_id='test', created_at=NOW,
                         intent_id=new_id('pi'), decision_id=new_id('dec'),
                         account_id=pipe.cfg.account_id, portfolio_version=snap.portfolio.portfolio_version,
                         instrument_id=I, action='BUY', target_weight=D('.4'), current_weight=D('0'),
                         valid_until=NOW+timedelta(minutes=5), mode='paper')
    verdict, _ = pipe.guard.check(pi, snap, D('1.50'), NOW)
    assert verdict.verdict.value == 'ALLOW'
    # A real restart means the previous writer process released the account.
    pipe.release_writer_lease()
    restored = Pipeline(pipe.cfg, clock=lambda: NOW)
    assert restored.guard.daily_buy_orders_count == 1
    restored.guard.sync_counters(NOW+timedelta(days=1))
    assert restored.guard.daily_buy_orders_count == 0


def test_budget_success_cost_upper_bound_and_failure_keeps_reservation(tmp_path):
    pipe = pipeline(tmp_path)
    cfg = pipe.cfg.t0
    cfg.model_input_cny_per_million = cfg.model_output_cny_per_million = D('1')
    budget = DailyModelBudget(pipe.repo, cfg, pipe.cfg.account_id, lambda: NOW)
    from trademind.models.base import CallAuditor
    llm = DeepSeekProvider('TEST_PLACEHOLDER', budget=budget, auditor=CallAuditor(pipe.repo))
    response = Mock(status_code=200)
    response.json.return_value = {'choices':[{'message':{'content':'{"action":"WAIT"}'}}], 'usage':{'prompt_tokens':12,'completion_tokens':8}}
    llm.session.post = Mock(return_value=response)
    output = llm.complete(role='test', system='test', user='test', trace_id='test')
    assert output.call.cost_currency == 'CNY'
    assert D(output.call.cost_amount) > 0
    assert output.call.cost_source == 'operator_price_usage'
    assert D(output.call.cost_amount) == D('0.000020')
    assert D(budget.snapshot()['cost_settled_cny']) == D('0.000020')
    assert D(budget.snapshot()['cost_pending_cny']) == 0
    assert budget.used == 1
    saved = budget.snapshot()['cost_reserved_cny']
    import requests
    llm.session.post.side_effect = requests.exceptions.Timeout('test timeout')
    with pytest.raises(ProviderError):
        llm.complete(role='test', system='test', user='test', trace_id='test')
    assert budget.used == 2 and D(budget.snapshot()['cost_reserved_cny']) > D(saved)


def test_prompt_paper_accounts_isolated_and_no_future_fill(tmp_path):
    from test_dual_model_pipeline import MockLLM, MockJEV
    from trademind.evaluation.prompt_experiment import compare_prompt_versions
    pipe = pipeline(tmp_path)
    contexts = []
    for at in (NOW, NOW+timedelta(minutes=1), NOW+timedelta(minutes=2)):
        pipe.router.get_minute_bars.return_value = ([bar(at)], None)
        contexts.append(pipe.context_builder.build_minute(I, at))
    # Identical decisions still produce independent ledgers and order records.
    llm = MockLLM([
        {'candidate_action':'BUY','confidence':.8}, {'action':'BUY','target_weight':'.4','confidence':.8},
        {'candidate_action':'WAIT','confidence':.8},
        {'candidate_action':'WAIT','confidence':.8},
        {'candidate_action':'HOLD','confidence':.8},
        {'candidate_action':'WAIT','confidence':.8},
        {'candidate_action':'HOLD','confidence':.8},
    ])
    report = compare_prompt_versions(contexts, llm, MockJEV(), run_id='isolation-test')
    assert report.simulation_status == 'historical_paper'
    v1, v2 = report.paper_accounts['v1'], report.paper_accounts['v2']
    assert not v1['errors'] and not v2['errors']
    assert v1['account_id'] != v2['account_id']
    assert v1['orders'] == v1['fills'] == 1
    assert v2['orders'] == v2['fills'] == 0
    assert D(v1['trading_fees_cny']) > 0 and D(v1['net_pnl']) < 0
    assert v2['net_pnl'] == '0.00' or D(v2['net_pnl']) == 0
    assert v1['model_cost_cny'] is None
    assert pipe.ledger.snapshot(NOW).cash == 500
    assert v1['archive']['account_events']
    assert v1['archive']['orders']
    json.dumps(report.to_dict())


def test_t0_pending_fill_never_falls_back_to_daily(tmp_path):
    pipe = pipeline(tmp_path)
    buy_position(pipe)
    pipe.router.get_minute_bars.return_value = ([bar(NOW, '1.48')], None)
    result = pipe.run_decision(I, NOW)
    assert result.order_id
    pipe.router.get_minute_bars.return_value = ([], None)
    assert pipe.poll_fills(NOW+timedelta(minutes=1)) == []
    pipe.router.get_daily_bars.assert_not_called()


def test_future_source_timestamp_and_quality_violations_block(tmp_path):
    pipe = pipeline(tmp_path)
    future = bar(NOW).model_copy(update={'source_time': NOW+timedelta(minutes=1)})
    pipe.router.get_minute_bars.return_value = ([future], None)
    assert pipe.run_decision(I, NOW).error
    from trademind.market.quality import QualityReport
    report = QualityReport()
    report.limit_violations = ['bad OHLC']
    pipe.router.get_minute_bars.return_value = ([bar(NOW)], report)
    result = pipe.run_decision(I, NOW)
    assert result.error and 'quality' in result.error


def test_shared_budget_two_database_connections_cannot_overrun(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from trademind.storage import Database, Repository
    cfg = AppConfig().t0
    cfg.daily_decision_call_budget = 10
    databases = [Database(f'sqlite:///{tmp_path}/budget.db') for _ in range(2)]
    budgets = [DailyModelBudget(Repository(db), cfg, 'same-account', lambda: NOW) for db in databases]
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda i: budgets[i % 2].try_consume(), range(40)))
    assert sum(result) == 10
    assert budgets[0].used == budgets[1].used == 10
    for db in databases:
        db.close()


def test_cost_budget_exact_ceiling_and_restart(tmp_path):
    pipe = pipeline(tmp_path)
    cfg = pipe.cfg.t0
    cfg.model_input_cny_per_million = cfg.model_output_cny_per_million = D('1000000')
    a = DailyModelBudget(pipe.repo, cfg, 'cost-edge', lambda: NOW)
    assert a.reserve_request(2, 3) == D('5')
    b = DailyModelBudget(pipe.repo, cfg, 'cost-edge', lambda: NOW)
    assert b.snapshot()['cost_reserved_cny'] == '5'
    with pytest.raises(ProviderError, match='budget exhausted'):
        b.reserve_request(1, 1)
    assert b.used == 1


def test_missing_prices_and_cost_ceiling_block_http(tmp_path):
    pipe = pipeline(tmp_path)
    cfg = pipe.cfg.t0
    budget = DailyModelBudget(pipe.repo, cfg, pipe.cfg.account_id, lambda: NOW)
    llm = DeepSeekProvider('TEST_PLACEHOLDER', budget=budget)
    llm.session.post = Mock(side_effect=AssertionError('no network'))
    with pytest.raises(ProviderError, match='pricing unconfigured'):
        llm.complete(role='test', system='test', user='test', trace_id='test')
    cfg.model_input_cny_per_million = D('1000000')
    cfg.model_output_cny_per_million = D('1000000')
    with pytest.raises(ProviderError, match='budget exhausted'):
        llm.complete(role='test', system='test', user='test', trace_id='test')
    llm.session.post.assert_not_called()
    assert budget.used == 0


def test_repair_attempts_each_consume_persistent_budget(tmp_path):
    pipe = pipeline(tmp_path)
    cfg = pipe.cfg.t0
    cfg.daily_decision_call_budget = 1
    cfg.model_input_cny_per_million = cfg.model_output_cny_per_million = D('1')
    budget = DailyModelBudget(pipe.repo, cfg, pipe.cfg.account_id, lambda: NOW)
    llm = DeepSeekProvider('TEST_PLACEHOLDER', budget=budget, max_repair_attempts=1)
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'message': {'content': 'malformed'}}]}
    llm.session.post = Mock(return_value=response)
    with pytest.raises(ProviderError, match='budget exhausted'):
        llm.complete(role='test', system='test', user='test', trace_id='test')
    assert llm.session.post.call_count == 1
    assert budget.used == 1
    assert llm.session.post.call_args.kwargs['json']['max_tokens'] == 1024


def test_cli_error_and_steps_current_time(tmp_path, monkeypatch, capsys):
    from trademind import runner
    pipe = pipeline(tmp_path)
    monkeypatch.setattr(runner, '_load', lambda: pipe.cfg)
    ticks = []
    def decide(self, iid, at):
        ticks.append(at)
        return DecisionResult('test', iid, at, '', error='injected failure' if len(ticks)==2 else None)
    monkeypatch.setattr(Pipeline, 'run_decision', decide)
    polled = []
    monkeypatch.setattr(Pipeline, 'poll_fills', lambda self, at: polled.append(at) or [])
    waits = []
    tick = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tick[0].astimezone(tz) if tz else tick[0]
    monkeypatch.setattr(runner, 'datetime', Clock)
    def advance(seconds):
        waits.append(seconds)
        tick[0] += timedelta(seconds=seconds)
    monkeypatch.setattr(runner.time, 'sleep', advance)
    # The CLI runs as its own process in production: the interactive pipe here
    # releases the writer lease so the command can take it.
    pipe.release_writer_lease()
    assert runner.cmd_t0_paper(I, 2) == 1
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'failed'
    assert len(waits) == 1 and waits[0] == 60
    assert ticks[1] > ticks[0]
    assert polled == [ticks[0], ticks[0], ticks[1], ticks[1]]


def test_config_override_applies_to_every_command(monkeypatch):
    from trademind import runner
    seen = []
    monkeypatch.setattr(runner, '_config_path', None)
    monkeypatch.setattr(runner, 'cmd_status', lambda: seen.append(runner._load()) or 0)
    monkeypatch.setattr(runner, 'cmd_recover', lambda: seen.append(runner._load()) or 0)
    monkeypatch.setattr(runner, 'load_config', lambda path: path)
    assert runner.main(['--config', 'example.toml', 'status']) == 0
    assert runner.main(['--config', 'example.toml', 'recover']) == 0
    assert seen == ['example.toml', 'example.toml']
    monkeypatch.setattr(runner, '_config_path', None)


from trademind import runner as _runner_mod
# -- TASK-20261016-02: same-day probe marker + probe-budget backoff (ARCH-022) --
runner = _runner_mod

def _probe_pipe(tmp_path):
    from test_fast_decision import make_pipe
    pipe, tick = make_pipe(tmp_path)
    return pipe, tick


def _probe_response(ok=True):
    from unittest.mock import Mock
    r = Mock(status_code=200)
    content = '{"ok":true}' if ok else '{"ok":false}'
    r.json.return_value = {'choices': [{'message': {'content': content}}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
    return r


def test_same_day_probe_marker_skips_paid_reprobes(tmp_path):
    """First launch probes live (HTTP called) and saves the marker; a same-day
    relaunch skips paid probes entirely (ARCH-022)."""
    from datetime import datetime as dt
    from trademind.market.base import BEIJING
    pipe, tick = _probe_pipe(tmp_path)
    markers = {}
    saves = []
    pipe.repo.get_coalesce = Mock(side_effect=lambda k: {'meta': markers.get(k)} if k in markers else None)
    def save(key, at, meta):
        markers[key] = meta
        saves.append((key, meta))
        return True
    pipe.repo.save_coalesce = Mock(side_effect=save)
    from trademind.domain.base import new_id
    from trademind.domain.enums import ProviderKind
    from trademind.models.base import ModelOutput, build_call
    def complete(**kw):
        pipe.model_budget.reserve_request(100, 100, role=kw['role'])
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK,
                          model='deepseek-flash', system=kw['system'], user=kw['user'],
                          trace_id=kw['trace_id'])
        return ModelOutput(text='{"ok":true}', parsed={'ok': True}, call=call)
    pipe.llm.complete = Mock(side_effect=complete)
    from test_fast_decision import Response
    def jev_post(url, json=None, timeout=None):
        answers = {k: {'type': 'noul', 'noul': 0.95} for k in json['questions']}
        return Response({'model': 'jev-1.13.0', 'answers': answers,
                         'usage': {'input_tokens': 10, 'output_tokens': 5}})
    pipe.jev.session.post = Mock(side_effect=jev_post)
    checks1 = runner._probe_fast_models(pipe)
    assert checks1['ok'] and checks1['probes'] == 'live', checks1
    assert pipe.llm.complete.call_count == 1 and pipe.jev.session.post.call_count == 1
    marker = markers['fast-probe-ok:fast-test']
    assert marker['verified'] is True and marker['day'] == dt.now(BEIJING).date().isoformat()
    # same-day relaunch: zero HTTP, capability from the marker
    checks2 = runner._probe_fast_models(pipe)
    assert checks2['ok'] and checks2['probes'] == 'same_day_marker'
    assert pipe.llm.complete.call_count == 1 and pipe.jev.session.post.call_count == 1
    assert pipe.llm.capability_verified and pipe.jev.capability_verified


def test_failed_probe_clears_same_day_marker(tmp_path):
    from datetime import datetime as dt
    from trademind.market.base import BEIJING
    from trademind.domain.base import new_id
    from trademind.domain.enums import ProviderKind
    from trademind.models.base import ModelOutput, build_call
    pipe, tick = _probe_pipe(tmp_path)
    markers = {'fast-probe-ok:fast-test': {'day': dt.now(BEIJING).date().isoformat(),
                                           'config_hash': None, 'verified': True}}
    pipe.repo.get_coalesce = Mock(side_effect=lambda k: {'meta': markers[k]} if k in markers else None)
    pipe.repo.save_coalesce = Mock(side_effect=lambda k, at, meta: markers.update({k: meta}) or True)
    # live probe fails this time (config_hash None never matches the fresh check,
    # so the live path runs and its failure must clear verified)
    def complete(**kw):
        pipe.model_budget.reserve_request(10, 10, role=kw['role'])
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK,
                          model='deepseek-flash', system=kw['system'], user=kw['user'],
                          trace_id=kw['trace_id'])
        return ModelOutput(text='', parsed={'ok': True}, call=call)
    pipe.llm.complete = Mock(side_effect=complete)
    import requests as requests_mod
    from test_fast_decision import Response
    pipe.jev.session.post = Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(
        requests_mod.exceptions.Timeout('jev down')))
    checks = runner._probe_fast_models(pipe)
    assert checks['ok'] is False
    assert markers['fast-probe-ok:fast-test']['verified'] is False  # marker cleared


def test_probe_budget_exhausted_backs_off_hourly(tmp_path):
    """Preflight flags probe-pair budget exhaustion; supervisor backs off 3600s
    with a distinct non-model-failure reason instead of 5-minute loops."""
    from test_fast_decision import make_pipe
    from trademind.execution.auto_paper import result_policy
    pipe, tick = _probe_pipe(tmp_path)
    pipe.model_budget.limit = 2  # limit is captured at budget construction
    pipe.model_budget.reserve_request(10, 10, role='flash_probe')
    pipe.model_budget.reserve_request(10, 10, role='jev_probe')
    pipe.calendar = Mock(is_session_open=Mock(return_value=True))  # isolate the budget branch
    pipe.jev.base_url = 'https://api.typesafe.ai'
    pipe.jev.model = 'jev-1.13.0'  # official-model gate must pass to reach the budget branch
    preflight = runner._fast_paper_preflight(pipe, [I], probe_models=True)
    assert preflight.get('probe_budget_exhausted') is True
    assert any('probe_pair_unavailable' in b for b in preflight['blockers'])
    report = {'status': 'blocked', 'preflight': preflight,
              'shutdown': {'worker_active': False}}
    halted, delay, note = result_policy(report)
    assert halted is False and delay == 3600
    assert '探针额度今日已耗尽' in note
    # ordinary gate blocks keep the 5-minute cadence
    halted, delay, note = result_policy({'status': 'blocked', 'preflight': {},
                                         'shutdown': {'worker_active': False}})
    assert delay == 300


def test_valid_marker_overrides_spent_probe_budget(tmp_path, monkeypatch):
    """Task17-05 gap A: a valid same-day marker must not be blocked by a spent
    probe-pair budget — the launch skips probes, so the gate does not apply."""
    from datetime import datetime as dt
    from trademind.market.base import BEIJING
    from trademind.domain.base import new_id
    from trademind.domain.enums import ProviderKind
    from trademind.models.base import ModelOutput, build_call
    pipe, tick = _probe_pipe(tmp_path)
    pipe.jev.model = 'jev-1.13.0'  # official-model gate identity
    pipe.calendar = Mock(is_session_open=Mock(return_value=True))
    # The test owns a fixed intraday clock. Wall-clock midnight would leave
    # fewer than 16 contiguous same-day bars and test data quality, not probes.
    fixed = dt(2026, 10, 23, 10, 30, tzinfo=BEIJING)
    tick[0] = fixed
    class FixedClock(dt):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
    monkeypatch.setattr(runner, 'datetime', FixedClock)
    from test_fast_decision import minute
    def fresh_bars(*a, **kw):
        now = fixed
        return ([minute(now - timedelta(minutes=i)) for i in reversed(range(20))], None)
    pipe.router.get_minute_bars = Mock(side_effect=fresh_bars)  # data leg needs real-fresh bars
    def complete(**kw):
        pipe.model_budget.reserve_request(100, 100, role=kw['role'])
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK,
                          model='deepseek-flash', system=kw['system'], user=kw['user'],
                          trace_id=kw['trace_id'])
        return ModelOutput(text='{"ok":true}', parsed={'ok': True}, call=call)
    pipe.llm.complete = Mock(side_effect=complete)
    from test_fast_decision import Response
    def jev_post(url, json=None, timeout=None):
        answers = {k: {'type': 'noul', 'noul': 0.95} for k in json['questions']}
        return Response({'model': 'jev-1.13.0', 'answers': answers,
                         'usage': {'input_tokens': 10, 'output_tokens': 5}})
    pipe.jev.session.post = Mock(side_effect=jev_post)
    assert runner._probe_fast_models(pipe)['probes'] == 'live'
    pipe.model_budget.limit = 2  # pair budget spent afterwards
    preflight = runner._fast_paper_preflight(pipe, [I], probe_models=True)
    assert preflight['ok'] is True and preflight.get('probes') == 'same_day_marker'
    assert not any('probe_pair' in b for b in preflight['blockers'])


def test_llm_endpoint_change_invalidates_same_day_marker(tmp_path):
    """Task17-05 gap B: the marker hash covers LLM/JEV endpoint identity — an
    endpoint change forces a fresh live probe even on the same day."""
    from trademind.domain.base import new_id
    from trademind.domain.enums import ProviderKind
    from trademind.models.base import ModelOutput, build_call
    pipe, tick = _probe_pipe(tmp_path)
    def complete(**kw):
        pipe.model_budget.reserve_request(100, 100, role=kw['role'])
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK,
                          model='deepseek-flash', system=kw['system'], user=kw['user'],
                          trace_id=kw['trace_id'])
        return ModelOutput(text='{"ok":true}', parsed={'ok': True}, call=call)
    pipe.llm.complete = Mock(side_effect=complete)
    from test_fast_decision import Response
    def jev_post(url, json=None, timeout=None):
        answers = {k: {'type': 'noul', 'noul': 0.95} for k in json['questions']}
        return Response({'model': 'jev-1.13.0', 'answers': answers,
                         'usage': {'input_tokens': 10, 'output_tokens': 5}})
    pipe.jev.session.post = Mock(side_effect=jev_post)
    assert runner._probe_fast_models(pipe)['probes'] == 'live'
    assert pipe.llm.complete.call_count == 1
    pipe.llm.base_url = 'https://api.example.com'  # endpoint changed
    checks2 = runner._probe_fast_models(pipe)
    assert checks2['probes'] == 'live' and pipe.llm.complete.call_count == 2
