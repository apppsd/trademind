from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import Mock
import pytest

from test_fast_decision import make_pipe, minute, I, NOW
from test_fast_comparison import contexts, archive
from trademind.pipeline import Pipeline
from trademind.evaluation.fast_comparison import compare_archives
from trademind.errors import NotFresh
from trademind.market.base import MinuteBar
from trademind.domain.enums import OrderSide, OrderStatus, RunMode, TradeAction
from trademind.domain.orders import Order, Fill
from trademind.domain.base import new_id
from trademind.domain.strategy import Condition
from trademind.context.fast_state import conditions_allow

B = '513100.SH'  # second whitelisted T0 instrument for multi-position tests


def _bar_for(iid, at, price='1.50'):
    p = Decimal(price)
    return MinuteBar(instrument_id=iid, trade_date=at.date(), bar_time=at,
                     open=p, high=p, low=p, close=p, volume=Decimal('1000'),
                     amount=p*1000, provider='test', source_time=at,
                     available_at=at, fetched_at=at)


def _apply_buy(pipe, iid, at, price='1.50'):
    oid = new_id('o')
    order = Order(id=oid, trace_id='test', created_at=at, order_id=oid,
                  client_order_id=oid, intent_id='test', guard_id='test',
                  account_id=pipe.cfg.account_id, instrument_id=iid,
                  mode=RunMode.PAPER, side=OrderSide.BUY,
                  quantity=Decimal('100'), limit_price=Decimal(price),
                  updated_at=at, status=OrderStatus.ACKNOWLEDGED)
    fill = Fill(id=new_id('f'), trace_id='test', created_at=at, fill_id=new_id('fill'),
                provider_fill_id=new_id('pf'), order_id=oid, fee=Decimal('0.1'),
                quantity=Decimal('100'), price=Decimal(price),
                executed_at=at, available_at=at)
    pipe.ledger.apply_fill(fill, order, at)


def test_intent_preserves_bar_expiry(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(return_value=(
        [minute(NOW-timedelta(seconds=150, minutes=i)) for i in reversed(range(20))], None))
    result = pipe.run_decision(I, NOW)
    assert result.intent.action.value == 'BUY'
    assert result.intent.valid_until == NOW+timedelta(seconds=30)


def test_marker_failure_rolls_back_fill_and_memory_then_retry(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    result = pipe.run_decision(I, NOW)
    before = pipe.ledger.snapshot(NOW)
    tick[0] += timedelta(minutes=1)
    original = pipe.repo.save_coalesce
    def fail(key, *args, **kwargs):
        if key.startswith('fast-holding:'):
            raise OSError('injected storage failure')
        return original(key, *args, **kwargs)
    pipe.repo.save_coalesce = fail
    with pytest.raises(OSError):
        pipe.poll_fills(tick[0])
    after = pipe.ledger.snapshot(tick[0])
    assert (after.version, after.cash, after.reserved_cash) == (before.version, before.cash, before.reserved_cash)
    assert not after.positions and len(pipe.repo.open_orders()) == 1
    assert not pipe.db.query('SELECT * FROM fills')
    pipe.repo.save_coalesce = original
    assert len(pipe.poll_fills(tick[0])) == 1
    assert pipe.repo.get_coalesce('fast-holding:'+pipe.cfg.account_id+':'+I)['meta']['deadline']
    assert not pipe.poll_fills(tick[0])


def test_restart_rebuilds_missing_holding_deadline(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.run_decision(I, NOW)
    tick[0] += timedelta(minutes=1)
    pipe.poll_fills(tick[0])
    key = 'fast-holding:'+pipe.cfg.account_id+':'+I
    deadline = pipe.repo.get_coalesce(key)['meta']['deadline']
    pipe.repo.save_coalesce(key, tick[0], {})
    # A real restart means the previous writer process released the account.
    pipe.release_writer_lease()
    restored = Pipeline(pipe.cfg, clock=lambda:tick[0])
    assert restored.repo.get_coalesce(key)['meta']['deadline'] == deadline
    assert restored.repo.get_coalesce(key)['meta']['recovered']


def test_slow_model_does_not_block_risk_exit(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.run_decision(I, NOW)
    tick[0] += timedelta(minutes=1)
    pipe.poll_fills(tick[0])
    tick[0] += timedelta(minutes=4)
    entered, release = Event(), Event()
    original = pipe._fast_pipeline.run
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    pipe._fast_pipeline.run = slow
    pipe.enable_async_models()
    try:
        pending = pipe.run_decision(I, tick[0])
        assert pending.guard_verdict == 'WAIT_MODEL_PENDING'
        assert entered.wait(1)
        tick[0] += timedelta(minutes=1)
        risk = pipe.run_decision(I, tick[0])
        assert risk.order_id and risk.portfolio_intent.action.value == 'SELL'
        assert not release.is_set()
    finally:
        release.set()
        pipe._model_executor.shutdown(wait=True)


def test_async_expired_result_never_submits(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.enable_async_models()
    try:
        assert pipe.run_decision(I, NOW).guard_verdict == 'WAIT_MODEL_PENDING'
        future = pipe._model_pending[0]
        future.result(timeout=2)
        tick[0] += timedelta(minutes=4)
        result = pipe.run_decision(I, tick[0])
        assert 'expired' in result.error and not result.order_id
        assert not pipe.repo.open_orders()
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_async_valid_result_executes_on_next_tick_with_original_trace(tmp_path):
    pipe, tick=make_pipe(tmp_path)
    pipe.enable_async_models()
    try:
        first=pipe.run_decision(I,NOW)
        future=pipe._model_pending[0]
        intent,_=future.result(timeout=2)
        assert not pipe.repo.open_orders()
        tick[0]+=timedelta(seconds=5)
        result=pipe.run_decision(I,tick[0])
        assert result.order_id and result.guard_verdict=='ALLOW'
        assert result.trace_id==intent.trace_id==first.trace_id
        assert result.context_id==intent.context_id
        assert result.portfolio_intent.valid_until==intent.valid_until
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_replay_completion_time_and_profile(tmp_path):
    ctx = contexts(tmp_path)
    ar = {c.context_id:archive(c) for c in ctx}
    for c in ctx:
        ar[c.context_id].update(available_at=(c.as_of+timedelta(seconds=2)).isoformat(),
                              valid_until=(c.as_of+timedelta(seconds=30)).isoformat(), holding_horizon_seconds=60)
    report = compare_archives(ctx, {'old':ar, 'new':ar}, artifact_kind='fixture')
    assert not report['accounts']['new']['errors']
    assert report['accounts']['new']['profile'] == 'jev_fast_decision'
    assert report['accounts']['new']['end_to_end_p50_ms'] == 2000


def test_replay_expired_completion_rejected(tmp_path):
    ctx = contexts(tmp_path)
    ar = {c.context_id:archive(c) for c in ctx}
    ar[ctx[0].context_id].update(available_at=(ctx[0].as_of+timedelta(seconds=40)).isoformat(),
                              valid_until=(ctx[0].as_of+timedelta(seconds=30)).isoformat())
    report = compare_archives(ctx, {'new':ar}, artifact_kind='fixture')
    assert any('validity' in e for e in report['accounts']['new']['errors'])


def test_replay_new_holding_timeout_differs_from_old(tmp_path):
    ctx = contexts(tmp_path)
    ar = {ctx[0].context_id:archive(ctx[0], 'BUY')}
    ar[ctx[0].context_id].update(target_weight='0.4', confidence='1', holding_horizon_seconds=60,
        model_calls=[{'cost_amount':'0.01','cost_currency':'CNY','cost_source':'operator_price_usage','latency_ms':2}])
    report = compare_archives(ctx, {'old':ar,'new':ar}, artifact_kind='fixture')
    assert report['accounts']['new']['actions'].get('SELL') == 1
    assert not report['accounts']['old']['actions'].get('SELL')


def test_flash_missing_usage_keeps_pending_reservation(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    from trademind.models.deepseek import DeepSeekProvider
    provider = DeepSeekProvider('TEST_ONLY', budget=pipe.model_budget)
    response = Mock(status_code=200)
    response.json.return_value = {'choices':[{'message':{'content':'{"ok":true}'}}]}
    provider.session.post = Mock(return_value=response)
    output = provider.complete(role='strategy', system='json', user='test', trace_id='test')
    assert output.call.cost_source == 'mixed_usage_and_reservation'
    state = pipe.model_budget.snapshot()
    assert Decimal(state['cost_pending_cny']) > 0 and Decimal(state['cost_settled_cny']) == 0


def test_flash_json_repair_settles_both_requests(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    from trademind.models.deepseek import DeepSeekProvider
    provider = DeepSeekProvider('TEST_ONLY', budget=pipe.model_budget)
    def response(text):
        r = Mock(status_code=200)
        r.json.return_value = {'choices':[{'message':{'content':text}}], 'usage':{'prompt_tokens':10,'completion_tokens':20}}
        return r
    provider.session.post = Mock(side_effect=[response('bad json'),response('{"ok":true}')])
    output = provider.complete(role='strategy', system='json', user='test', trace_id='test')
    assert pipe.model_budget.used == 2
    assert Decimal(output.call.cost_amount) == Decimal('0.000060')
    assert Decimal(pipe.model_budget.snapshot()['cost_pending_cny']) == 0


def test_async_fresh_conditions_revalidated(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.enable_async_models()
    try:
        pipe.run_decision(I, NOW)
        pipe._model_pending[0].result(timeout=2)
        tick[0] += timedelta(seconds=5)
        pipe.router.get_minute_bars = Mock(return_value=([minute(tick[0]-timedelta(minutes=i), '0.90') for i in reversed(range(20))],None))
        result = pipe.run_decision(I, tick[0])
        assert 'conditions changed' in result.error and not result.order_id
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_async_account_version_change_never_submits(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.enable_async_models()
    try:
        pipe.run_decision(I, NOW)
        pipe._model_pending[0].result(timeout=2)
        tick[0] += timedelta(seconds=5)
        pipe.ledger.deposit(Decimal('1'), tick[0])
        result = pipe.run_decision(I, tick[0])
        assert 'account changed' in result.error and not result.order_id
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_replay_preserves_runtime_config_and_no_credentials(tmp_path):
    from dataclasses import asdict
    from trademind.config import AppConfig
    cfg=AppConfig()
    cfg.paper.initial_cash=Decimal('500')
    cfg.paper.slippage_bps=17
    cfg.risk.quote_max_age_seconds=123
    cfg.t0.fast_eval_seconds=60
    runtime={k:asdict(getattr(cfg,k)) for k in ('t0','risk','paper')}
    ctx=contexts(tmp_path)
    ar={c.context_id:archive(c) for c in ctx}
    report=compare_archives(ctx,{'old':ar,'new':ar},runtime_configs={'old':runtime,'new':runtime})
    assert report['accounts']['new']['runtime_config']['paper']['slippage_bps']==17
    assert report['accounts']['new']['runtime_config']['risk']['quote_max_age_seconds']==123
    assert report['accounts']['new']['runtime_config']['t0']['fast_eval_seconds']==60
    assert not report['accounts']['new']['archive_gaps']


# -- TASK-20261013-12: single source of trade-condition semantics (ISSUE-20261013-04)

def test_conditions_allow_contract_semantics():
    feats = {'close': Decimal('1.5'), 'ret_1m': Decimal('0')}
    up = Condition(field='close', operator='gt', value='1')
    down = Condition(field='close', operator='lt', value='1')
    far = Condition(field='close', operator='gt', value='99')
    from types import SimpleNamespace
    plan = SimpleNamespace(entry_conditions=[up], exit_conditions=[down, far])
    # BUY: every entry condition must match; SELL: any one exit condition.
    assert conditions_allow(TradeAction.BUY, plan, feats)
    assert not conditions_allow(TradeAction.SELL, plan, feats)
    # An empty condition set never authorizes a trade on either side.
    assert not conditions_allow(TradeAction.BUY, SimpleNamespace(entry_conditions=[], exit_conditions=[up]), feats)
    assert not conditions_allow(TradeAction.SELL, SimpleNamespace(entry_conditions=[up], exit_conditions=[]), feats)
    # SELL fires when only one of several exit conditions matches.
    one_of_two = SimpleNamespace(entry_conditions=[far], exit_conditions=[up, down])
    assert conditions_allow(TradeAction.SELL, one_of_two, feats)


def _sell_plan_state(tmp_path, exit_conditions):
    """Holding filled at 1.50; cached plan restricted to SELL/HOLD/WAIT."""
    pipe, tick = make_pipe(tmp_path)
    pipe.run_decision(I, tick[0])
    tick[0] += timedelta(minutes=1)
    pipe.poll_fills(tick[0])
    tick[0] += timedelta(minutes=4)
    planner = pipe._fast_pipeline.planner
    row = pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['allowed_actions'] = ['SELL', 'HOLD', 'WAIT']
    row['meta']['plan']['exit_conditions'] = exit_conditions
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])
    return pipe, tick


def test_async_one_of_two_exit_conditions_executes_sell(tmp_path):
    pipe, tick = _sell_plan_state(tmp_path, [
        dict(field='close', operator='lt', value='2'),       # matches at 1.50
        dict(field='ret_1m', operator='lt', value='-0.01')])  # does not match
    pipe.enable_async_models()
    try:
        pending = pipe.run_decision(I, tick[0])
        assert pending.guard_verdict == 'WAIT_MODEL_PENDING'
        future = pipe._model_pending[0]
        intent, _ = future.result(timeout=2)
        assert intent.action.value == 'SELL'
        tick[0] += timedelta(seconds=5)
        result = pipe.run_decision(I, tick[0])
        assert not result.error, result.error
        assert result.order_id and result.intent.action.value == 'SELL'
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_async_no_exit_condition_matching_blocks_sell(tmp_path):
    pipe, tick = _sell_plan_state(tmp_path, [
        dict(field='close', operator='lt', value='2'),        # matches at submit (1.50)
        dict(field='ret_1m', operator='lt', value='-0.01')])  # never matches
    pipe.enable_async_models()
    try:
        assert pipe.run_decision(I, tick[0]).guard_verdict == 'WAIT_MODEL_PENDING'
        intent, _ = pipe._model_pending[0].result(timeout=2)
        assert intent.action.value == 'SELL'
        # Market flips before execution: no exit condition matches any more.
        pipe.router.get_minute_bars = Mock(return_value=(
            [_bar_for(I, tick[0]-timedelta(minutes=i), '2.50') for i in reversed(range(20))], None))
        tick[0] += timedelta(seconds=5)
        result = pipe.run_decision(I, tick[0])
        assert 'conditions changed' in result.error and not result.order_id
        assert not pipe.repo.open_orders()
    finally:
        pipe._model_executor.shutdown(wait=True)


def test_empty_exit_conditions_never_offer_sell(tmp_path):
    pipe, tick = _sell_plan_state(tmp_path, [])
    result = pipe.run_decision(I, tick[0])
    state = pipe.jev.session.post.call_args.kwargs['json']['state']
    assert 'SELL' not in state['allowed_actions']
    assert result.intent.action.value == 'HOLD' and not result.order_id
    assert not pipe.repo.open_orders()


# -- TASK-20261013-13: per-position quote failure isolation (ISSUE-20261013-05)

def test_stale_holding_does_not_block_other_symbol_risk_exit(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.universe.append(B)
    pipe.guard.universe.add(B)
    _apply_buy(pipe, I, tick[0]-timedelta(minutes=5), '1.50')   # stale quote
    _apply_buy(pipe, B, tick[0]-timedelta(minutes=5), '1.50')   # fresh, below stop
    def routed(iid, *a, **kw):
        if iid == I:
            raise NotFresh('isolated stale quote')
        return ([_bar_for(iid, tick[0]-timedelta(minutes=i), '1.48') for i in reversed(range(20))], None)
    pipe.router.get_minute_bars = Mock(side_effect=routed)
    pipe.llm.complete = Mock(side_effect=AssertionError('risk exit must not call models'))
    result = pipe.run_decision(B, tick[0])
    assert result.error is None, result.error
    assert result.portfolio_intent.action.value == 'SELL' and result.order_id
    assert any(f.startswith(I) for f in result.diagnostics['valuation_failures'])
    # Incomplete quotes never fabricate a new day equity baseline.
    assert pipe.repo.get_coalesce('day-baseline:fast-test') is None
    # The stale symbol is untouched: no trade, no order, position intact.
    assert not any(o['instrument_id'] == I for o in pipe.repo.open_orders())
    assert next(p for p in pipe.ledger.snapshot(tick[0]).positions if p.instrument_id == I).quantity == Decimal('100')
    pipe.llm.complete.assert_not_called()


def test_incomplete_valuation_vetoes_new_buy(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.universe.append(B)
    pipe.guard.universe.add(B)
    _apply_buy(pipe, I, tick[0]-timedelta(minutes=5), '1.50')   # stale quote
    def routed(iid, *a, **kw):
        if iid == I:
            raise NotFresh('isolated stale quote')
        return ([_bar_for(iid, tick[0]-timedelta(minutes=i), '1.50') for i in reversed(range(20))], None)
    pipe.router.get_minute_bars = Mock(side_effect=routed)
    result = pipe.run_decision(B, tick[0])
    assert result.guard_verdict == 'VETO'
    assert 'incomplete_account_valuation' in result.guard_reasons
    assert not result.order_id and not pipe.repo.open_orders()
    assert any(f.startswith(I) for f in result.diagnostics['valuation_failures'])
    assert pipe.repo.get_coalesce('day-baseline:fast-test') is None


def test_all_quotes_stale_refuses_without_orders(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    _apply_buy(pipe, I, tick[0]-timedelta(minutes=5), '1.50')
    pipe.router.get_minute_bars = Mock(side_effect=NotFresh('all stale'))
    pipe.llm.complete = Mock(side_effect=AssertionError('no model call on stale facts'))
    result = pipe.run_decision(I, tick[0])
    assert 'NotFresh' in result.error and not result.order_id
    assert not pipe.repo.open_orders()
    assert any(f.startswith(I) for f in result.diagnostics['valuation_failures'])
    pipe.llm.complete.assert_not_called()
