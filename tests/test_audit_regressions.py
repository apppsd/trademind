"""Regression coverage for ISSUE-04..11; every fill asserts account changes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from audit_helpers import authorize
from test_guard_oms_pipeline import FEES, make_intent, make_snapshot, guard_env, NOW
from trademind.config import AppConfig, RiskConfig
from trademind.domain.base import new_id
from trademind.domain.enums import TradeAction, GuardVerdict, RunMode, OrderStatus
from trademind.domain.orders import Fill, Order
from trademind.errors import RecoveryRequired, ProviderError
from trademind.execution import OMS, PaperBroker, PaperConfig
from trademind.storage import Database, Repository, AuditSink
from trademind.portfolio import Ledger
from trademind.market.router import DatasetRouter
from trademind.market.calendar import TradingCalendar
from trademind.pipeline import Pipeline
from trademind.evaluation.replay import ReplayRunner, run_comparison
from trademind.models.stub import HttpJEVProvider
from trademind.models.base import CallAuditor

UTC = timezone.utc

@pytest.fixture
def env(tmp_path):
    db = Database(f'sqlite:///{tmp_path / "account.db"}')
    repo, audit = Repository(db), AuditSink(db)
    led = Ledger(db, 'paper-001', FEES)
    led.deposit(Decimal('1000000'), NOW)
    broker = PaperBroker(DatasetRouter(pref=['fixture']), TradingCalendar.load(), FEES, PaperConfig())
    oms = OMS(repo, led, broker, audit)
    yield repo, audit, led, broker, oms
    db.close()

def buy(env):
    repo, audit, led, broker, oms = env
    intent = make_intent().model_copy(update={'instrument_id': '600036.SH'})
    gr, qty = authorize(repo, led, audit, intent, NOW)
    order = oms.submit(intent, gr, qty, Decimal('40'), NOW)
    return intent, gr, qty, order

def test_equity_reservation_cancel_and_repeated_cancel(env):
    repo, audit, led, broker, oms = env
    before = led.snapshot(NOW)
    _, gr, qty, order = buy(env)
    after = led.snapshot(NOW)
    assert after.equity == before.equity
    assert after.cash == before.cash and after.reserved_cash > 0
    assert order.guard_id == gr.id
    oms.cancel(order.order_id)
    assert led.snapshot(NOW).reserved_cash == 0
    version = led.version
    oms.cancel(order.order_id)
    assert led.version == version

@pytest.mark.parametrize('mutation', ['fake', 'veto', 'intent', 'qty', 'price', 'expired', 'version', 'kill', 'mode'])
def test_oms_rejects_invalid_authorization(env, mutation):
    repo, audit, led, broker, oms = env
    intent = make_intent().model_copy(update={'instrument_id': '600036.SH'})
    gr, qty = authorize(repo, led, audit, intent, NOW)
    price, at = Decimal('40'), NOW
    if mutation == 'fake': gr = SimpleNamespace(id=gr.id, verdict='ALLOW')
    if mutation == 'veto': gr = gr.model_copy(update={'verdict': GuardVerdict.VETO})
    if mutation == 'intent': intent = intent.model_copy(update={'target_weight': Decimal('.18')})
    if mutation == 'qty': qty += 100
    if mutation == 'price': price += 1
    if mutation == 'expired': at += timedelta(hours=1)
    if mutation == 'version': led.deposit(Decimal('1'), NOW)
    if mutation == 'kill': repo.set_kill_switch(True, 'test')
    if mutation == 'mode': oms.mode = 'live'
    with pytest.raises(RecoveryRequired): oms.submit(intent, gr, qty, price, at)
    assert not repo.db.query('SELECT 1 FROM orders')

@pytest.mark.parametrize('failure_point', ['ledger', 'order', 'audit'])
def test_fill_atomic_failure_retry_and_restart(env, monkeypatch, failure_point):
    repo, audit, led, broker, oms = env
    _, _, qty, order = buy(env)
    before = led.snapshot(NOW)
    target, attr = {'ledger': (led, 'apply_fill'), 'order': (repo, 'update_order'), 'audit': (audit, 'record')}[failure_point]
    original = getattr(target, attr)
    def fail(*a, **kw): raise RuntimeError('injected crash')
    monkeypatch.setattr(target, attr, fail)
    at = NOW + timedelta(days=4)
    with pytest.raises(RuntimeError): oms.poll_fill(order.order_id, at)
    assert not repo.db.query('SELECT 1 FROM fills')
    assert led.snapshot(at).cash == before.cash and not led.snapshot(at).positions
    assert led.snapshot(at).reserved_cash == before.reserved_cash
    fresh = Ledger(repo.db, led.account_id, FEES)
    assert fresh.snapshot(at).cash == before.cash
    monkeypatch.setattr(target, attr, original)
    fill = oms.poll_fill(order.order_id, at)
    assert fill and fill.available_at <= at
    assert led.snapshot(at).positions[0].quantity == qty
    assert led.snapshot(at).reserved_cash == 0
    once = led.snapshot(at).cash
    assert oms.poll_fill(order.order_id, at) is None
    assert led.snapshot(at).cash == once

def test_broker_never_fills_future_and_obeys_limit(env):
    _, _, _, broker, _ = env
    _, _, _, order = buy(env)
    assert broker.try_fill(order, NOW) is None
    fill = broker.try_fill(order, NOW + timedelta(days=4))
    assert fill and NOW < fill.executed_at <= fill.available_at <= NOW + timedelta(days=4)
    assert fill.price <= order.limit_price

def test_rollover_restart_and_same_day_remain_t1(env):
    repo, audit, led, broker, oms = env
    _, _, qty, order = buy(env)
    at = NOW + timedelta(days=4)
    fill = oms.poll_fill(order.order_id, at)
    day = fill.executed_at.astimezone(timezone(timedelta(hours=8))).date()
    led.on_new_trading_day(day)
    assert led.snapshot(at).positions[0].available_quantity == 0
    restarted = Ledger(repo.db, led.account_id, FEES)
    restarted.rebuild_from_events()
    assert restarted.snapshot(at).positions[0].available_quantity == 0
    restarted.on_new_trading_day(day + timedelta(days=1))
    assert restarted.snapshot(at).positions[0].available_quantity == qty
    restarted.rebuild_from_events()
    assert restarted.snapshot(at).positions[0].available_quantity == qty

def test_unknown_order_blocks_new_risk(env):
    repo, audit, led, broker, oms = env
    intent, gr, qty, order = buy(env)
    order.status = OrderStatus.SUBMITTING
    repo.update_order(order)
    assert oms.recover()['unknown'] == 1
    assert oms.poll_fill(order.order_id, NOW + timedelta(days=4)) is None
    other = intent.model_copy(update={'intent_id': new_id('pi'), 'decision_id': new_id('d'), 'portfolio_version': led.version})
    with pytest.raises(AssertionError, match='recovery_required'):
        authorize(repo, led, audit, other, NOW)
    with pytest.raises(RecoveryRequired): oms.reconcile_unknown(order.order_id, 'FILLED')
    oms.reconcile_unknown(order.order_id, 'CANCELLED')
    assert led.snapshot(NOW).reserved_cash == 0

def pipeline(tmp_path, mode='replay', clock=None):
    cfg = AppConfig(mode=mode)
    cfg.llm.provider = 'stub'
    cfg.data.provider_pref = ['fixture']
    cfg.storage.url = f'sqlite:///{tmp_path / "pipe.db"}'
    p = Pipeline(cfg, clock=clock)
    p.ensure_account(at=NOW)
    return p

def test_ai_pipeline_actual_buy_reduce_and_clear(tmp_path):
    p = pipeline(tmp_path)
    at = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
    def trader(action, target):
        p.llm.script['portfolio'] = {'action': action, 'target_weight': target}
        p.llm.script['trader'] = {'action': action, 'target_weight': target, 'confidence': '.7',
                                 'reason': 'scripted lifecycle acceptance', 'holding_horizon_seconds': 86400}
    trader('BUY', '.15')
    result = p.run_decision('600036.SH', at)
    assert result.order_id, result.summary()
    p.poll_fills(at + timedelta(days=1))
    qty = p.ledger.snapshot(at).positions[0].quantity
    assert qty > 0
    at += timedelta(days=2)
    trader('SELL', '.07')
    result = p.run_decision('600036.SH', at, force_research=True)
    assert result.order_id and result.qty > 0, result.summary()
    p.poll_fills(at + timedelta(days=1))
    reduced = p.ledger.snapshot(at).positions[0].quantity
    assert 0 < reduced < qty
    at += timedelta(days=4)
    trader('SELL', '0')
    result = p.run_decision('600036.SH', at, force_research=True)
    assert result.order_id, result.summary()
    p.poll_fills(at + timedelta(days=3))
    assert not p.ledger.snapshot(at).positions
    assert p.ledger.snapshot(at).reserved_cash == 0
    assert len(p.repo.db.query('SELECT 1 FROM fills')) == 3
    p.db.close()

def test_future_thesis_not_reused(tmp_path):
    p = pipeline(tmp_path)
    later = NOW + timedelta(days=2, hours=8)
    result = p.run_decision('600036.SH', later)
    assert result.thesis is not None, result.error
    assert p.latest_valid_thesis('600036.SH', NOW) is None
    p.db.close()

def test_replay_copies_credentials_offline_and_is_repeatable(monkeypatch):
    cfg = AppConfig()
    cfg.llm.provider = 'deepseek'
    cfg.llm.api_key = 'YOUR_API_KEY_HERE'
    cfg.data.provider_pref = ['akshare']
    original = deepcopy(cfg)
    def network(*a, **kw): raise AssertionError('network forbidden during replay')
    monkeypatch.setattr('requests.sessions.Session.request', network)
    runner = ReplayRunner(cfg)
    assert cfg == original
    assert runner.cfg.data.provider_pref == ['fixture'] and runner.pipe.llm_is_stub
    assert not runner.cfg.llm.api_key
    days = TradingCalendar.load().window(NOW.date(), (NOW + timedelta(days=4)).date())
    a = runner.run(cfg.universe, days, Decimal('1000000')).to_dict()
    runner.pipe.db.close()
    second = ReplayRunner(cfg)
    b = second.run(cfg.universe, days, Decimal('1000000')).to_dict()
    assert a == b
    assert len(second.pipe.repo.db.query('SELECT 1 FROM context_snapshots')) > 0
    with pytest.raises(ValueError): second.run(cfg.universe, days, Decimal('1000000'))
    second.pipe.db.close()

def test_online_slow_model_cannot_trade_after_ttl(tmp_path):
    at = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
    current = [at]
    p = pipeline(tmp_path, mode='paper', clock=lambda: current[0])
    p.llm.script['trader'] = {'action': 'BUY', 'target_weight': '.15', 'confidence': '.7', 'reason': 'test'}
    p.llm.script['portfolio'] = {'action': 'BUY', 'target_weight': '.15'}
    original = p.portfolio_ai.finalize
    def slow(*args, **kw):
        result = original(*args, **kw)
        current[0] += timedelta(hours=1)
        return result
    p.portfolio_ai.finalize = slow
    result = p.run_decision('600036.SH', at)
    assert not result.order_id
    assert 'expired' in ' '.join(result.guard_reasons), result.summary()
    assert not p.repo.db.query('SELECT 1 FROM orders')
    assert p.orchestrator.snapshot()['counters']['submitted'] == 1
    p.db.close()

@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'refused', 'unknown'])
def test_http_jev_payload_validation_and_audit(env, monkeypatch, bad):
    repo, _, _, _, _ = env
    provider = HttpJEVProvider('https://example.invalid', auditor=CallAuditor(repo))
    captured = []
    def post(*args, **kw):
        captured.append(kw['json'])
        items = [{'question_id': 'continuation', 'value': 'yes', 'probability': '.7'}]
        if bad == 'missing': items = []
        if bad == 'duplicate': items *= 2
        if bad == 'unknown': items[0]['question_id'] = 'unrequested'
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'status': 'refused' if bad == 'refused' else 'ok', 'judgments': items})
    monkeypatch.setattr(provider._requests, 'post', post)
    with pytest.raises(ProviderError):
        provider.judge(context_id='c', thesis_id='t', instrument_id='600036.SH', as_of=NOW,
                       questions=[{'question_id': 'continuation', 'text': 'visible continuation?'}], trace_id='trace',
                       state={'context_id': 'c', 'as_of': NOW.isoformat(), 'features': {'close': '40'}},
                       thesis={'thesis_id': 't', 'invalidations': ['support broken']})
    assert captured[0]['state']['features']['close'] == '40'
    assert captured[0]['thesis']['invalidations'] and captured[0]['questions'][0]['text']
    assert repo.db.query("SELECT status FROM model_calls WHERE role='jev'")[0]['status'] == 'error'

def test_partial_fill_releases_only_executed_commitment(env, monkeypatch):
    repo, audit, led, broker, oms = env
    _, _, qty, order = buy(env)
    original = broker.try_fill
    before = led.snapshot(NOW).reserved_cash
    at = NOW + timedelta(days=4)
    def partial(o, now):
        return original(o, now).model_copy(update={'quantity': Decimal('100')})
    monkeypatch.setattr(broker, 'try_fill', partial)
    fill = oms.poll_fill(order.order_id, at)
    assert fill.quantity == 100
    after = led.snapshot(at)
    assert after.positions[0].quantity == 100
    assert 0 < after.reserved_cash < before
    assert after.cash - after.reserved_cash >= 0
    oms.cancel(order.order_id)
    assert led.snapshot(at).reserved_cash == 0 and led.snapshot(at).positions[0].quantity == 100

def test_unused_approval_expires_and_releases(env):
    repo, audit, led, _, oms = env
    intent = make_intent()
    gr, _ = authorize(repo, led, audit, intent, NOW)
    assert led.snapshot(NOW).reserved_cash > 0
    oms.expire_approvals(NOW + timedelta(hours=1))
    assert led.snapshot(NOW).reserved_cash == 0

def test_funding_is_not_daily_loss_and_baseline_survives_restart(tmp_path):
    p = pipeline(tmp_path)
    at = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
    p.on_new_trading_day(at.date())
    p.ledger.withdraw(Decimal('50000'), at)
    p.llm.script['trader'] = {'action': 'BUY', 'target_weight': '.10', 'confidence': '.7', 'reason': 'test'}
    p.llm.script['portfolio'] = {'action': 'BUY', 'target_weight': '.10'}
    res = p.run_decision('600036.SH', at)
    assert res.order_id, res.summary()
    assert p._adjusted_day_start() == Decimal('950000')
    # A real restart means the previous writer process released the account.
    p.release_writer_lease()
    restarted = Pipeline(p.cfg)
    restarted.on_new_trading_day(at.date())
    assert restarted._adjusted_day_start() == Decimal('950000')
    restarted.db.close()
    p.db.close()

def test_past_decision_rejects_future_account_state(tmp_path):
    p = pipeline(tmp_path)
    p.ledger.deposit(Decimal('10'), NOW + timedelta(days=3))
    res = p.run_decision('600036.SH', NOW + timedelta(days=2))
    assert 'future events' in res.error
    assert not p.repo.db.query('SELECT 1 FROM context_snapshots')
    p.db.close()

def test_orchestrator_coalesces_inflight_prioritizes_and_bounds():
    from trademind.orchestrator.scheduler import Orchestrator
    queue = Orchestrator(capacity=2, max_concurrent=2)
    assert queue.submit('research', 'a', now=NOW)
    assert queue.submit('holding_review', 'b', now=NOW)
    assert queue.submit('research', 'c', now=NOW) is None
    seen = []
    def handler(t):
        seen.append(t.instrument_id)
        assert queue.submit(t.kind, t.instrument_id, now=NOW) is None
        return True
    queue.run_once(handler, now=NOW)
    assert seen == ['b', 'a']
    assert queue.snapshot()['pending_keys'] == []
    assert queue.counters['coalesced'] == 2 and queue.counters['rejected_full'] == 1

def test_jev_success_payload_tracks_changes_and_stays_unverified(env, monkeypatch):
    repo, _, _, _, _ = env
    provider = HttpJEVProvider('https://example.invalid', auditor=CallAuditor(repo))
    captured = []
    def post(*a, **kw):
        captured.append(deepcopy(kw['json']))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'status': 'ok', 'judgments':
                               [{'question_id': 'continuation', 'value': 'yes', 'probability': '.7'}]})
    monkeypatch.setattr(provider._requests, 'post', post)
    for price in ('40', '41'):
        batch = provider.judge(context_id='c', thesis_id='t', instrument_id='600036.SH', as_of=NOW,
                       questions=[{'question_id': 'continuation', 'text': 'visible continuation?'}], trace_id='trace',
                       state={'context_id': 'c', 'as_of': NOW.isoformat(), 'features': {'close': price}},
                       thesis={'thesis_id': 't', 'invalidations': ['support broken']})
        assert batch.status == 'ok' and not batch.is_stub
    assert captured[0]['state'] != captured[1]['state']
    assert not provider.capability_verified
    assert len(repo.db.query("SELECT 1 FROM model_calls WHERE role='jev' AND status='ok'")) == 2

def test_shadow_has_no_orders_or_reservations(tmp_path):
    at = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
    p = pipeline(tmp_path, mode='shadow', clock=lambda: at)
    p.llm.script['trader'] = {'action': 'BUY', 'target_weight': '.10', 'confidence': '.7', 'reason': 'test'}
    p.llm.script['portfolio'] = {'action': 'BUY', 'target_weight': '.10'}
    res = p.run_decision('600036.SH', at)
    assert res.guard_reasons == ['shadow_no_execution']
    assert not p.repo.db.query('SELECT 1 FROM orders')
    assert p.ledger.snapshot(at).reserved_cash == 0
    p.db.close()

@pytest.mark.parametrize('case', ['same_day', 'wrong_target', 'no_position', 'odd_lot_clear'])
def test_sell_legality_and_odd_lot_clear(env, case):
    repo, audit, led, broker, oms = env
    order = Order(id='o', trace_id='t', created_at=NOW, order_id='seed', client_order_id='seed',
                  intent_id='i', guard_id='g', account_id=led.account_id, instrument_id='600036.SH',
                  mode=RunMode.PAPER, side='BUY', quantity='150', limit_price='40', updated_at=NOW)
    fill = Fill(id='f', trace_id='t', created_at=NOW, fill_id='seed', order_id='seed', provider_fill_id='seed',
                quantity='150', price='40', fee='0', executed_at=NOW, available_at=NOW)
    if case != 'no_position': led.apply_fill(fill, order, NOW)
    at = NOW if case == 'same_day' else NOW + timedelta(days=1)
    led.on_new_trading_day(at.date())
    intent = make_intent(action=TradeAction.SELL, weight='0').model_copy(update={
        'instrument_id': '600036.SH', 'portfolio_version': led.version, 'valid_until': at + timedelta(minutes=15)})
    if case == 'wrong_target': intent = intent.model_copy(update={'target_weight': Decimal('.10')})
    if case == 'odd_lot_clear':
        gr, qty = authorize(repo, led, audit, intent, at)
        assert qty == 150
    else:
        with pytest.raises(AssertionError): authorize(repo, led, audit, intent, at)

def test_price_decline_counts_as_daily_loss(env):
    repo, audit, led, broker, oms = env
    _, _, qty, order = buy(env)
    oms.poll_fill(order.order_id, NOW + timedelta(days=4))
    from trademind.guard import Guard
    at = NOW + timedelta(days=5)
    intent = make_intent().model_copy(update={'instrument_id': '600036.SH', 'portfolio_version': led.version,
                                            'valid_until': at + timedelta(minutes=30), 'target_weight': Decimal('.18')})
    from trademind.domain.context import PortfolioView
    account = led.snapshot(at, {'600036.SH': Decimal('20')})
    snap = make_snapshot(None).model_copy(update={'as_of': at, 'expires_at': at+timedelta(minutes=15),
        'portfolio': PortfolioView(id='pv', trace_id='t', created_at=at, as_of=at, account_id=led.account_id,
                                  currency='CNY', portfolio_version=led.version, cash=account.cash,
                                  equity=account.equity, positions={})})
    guard = Guard(repo, led, RiskConfig(), 'paper', ['600036.SH'], audit, lambda: Decimal('1000000'))
    gr, _ = guard.check(intent, snap, Decimal('20'), at)
    assert gr.reason_codes == ['daily_loss_limit']

def test_missing_snapshot_is_recovered_without_second_deposit(env):
    repo, audit, led, _, oms = env
    _, _, qty, order = buy(env)
    at = NOW + timedelta(days=4)
    oms.poll_fill(order.order_id, at)
    before = led.snapshot(at)
    repo.db.execute('DELETE FROM account_state WHERE account_id=?', (led.account_id,))
    fresh = Ledger(repo.db, led.account_id, FEES)
    after = fresh.snapshot(at)
    assert (after.cash, after.reserved_cash, after.version) == (before.cash, before.reserved_cash, before.version)
    assert after.positions[0].quantity == qty
    assert repo.load_account_state(led.account_id) is not None

def test_failed_guard_audit_leaves_no_approval_or_reservation(guard_env, monkeypatch):
    guard, snap, led, repo, audit = guard_env
    def fail(*a, **kw): raise RuntimeError('audit unavailable')
    monkeypatch.setattr(audit, 'record_critical', fail)
    with pytest.raises(RuntimeError): guard.check(make_intent(), snap, Decimal('40'), NOW)
    assert led.snapshot(NOW).reserved_cash == 0
    assert not repo.active_reservations(led.account_id)
    assert not repo.db.query('SELECT 1 FROM guard_results')

def test_later_bar_can_fill_after_untouched_first_bar(env, monkeypatch):
    _, _, _, broker, _ = env
    _, _, _, order = buy(env)
    original = broker.router.get_daily_bars
    def bars(*a, **kw):
        result, report = original(*a, **kw)
        first = True
        changed = []
        for b in result:
            if b.trade_date > NOW.date() and first:
                b = b.model_copy(update={'low': Decimal('50'), 'high': Decimal('55'), 'open': Decimal('52'), 'close': Decimal('52')})
                first = False
            changed.append(b)
        return changed, report
    monkeypatch.setattr(broker.router, 'get_daily_bars', bars)
    fill = broker.try_fill(order, NOW + timedelta(days=4))
    assert fill and fill.executed_at.date() > (NOW + timedelta(days=1)).date()

def test_concurrent_guard_approvals_serialize_cash(guard_env):
    from concurrent.futures import ThreadPoolExecutor
    guard, snap, led, repo, audit = guard_env
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(guard.check, make_intent(), snap, Decimal('40'), NOW) for _ in range(2)]
        results = [f.result()[0] for f in futures]
    assert sum(r.verdict == GuardVerdict.ALLOW for r in results) == 1
    assert led.snapshot(NOW).cash - led.snapshot(NOW).reserved_cash >= 0
    assert led.snapshot(NOW).equity == Decimal('1000000')
