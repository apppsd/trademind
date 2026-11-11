"""TASK-20261108-003 P0 regressions: B01 quote-age recheck + B02 valuation quality gate.

B01: the quote-age deadline must bind to the current clock and to the
configured quote_max_age_seconds, not to the decision snapshot's as_of with a
hardcoded 180s — a long model wait can otherwise push execution onto a quote
the strict config already expired. B02: other-position valuation must pass the
same quality gate as the main symbol; a quality-rejected window may never
donate its close to equity.
"""
from datetime import timedelta
from decimal import Decimal as D
from json import loads
from unittest.mock import Mock

import pytest

from test_fast_decision import make_pipe, I, NOW

from trademind.config import AppConfig
from trademind.context.builder import DataUnavailable
from trademind.domain.base import new_id
from trademind.domain.enums import (ConfidenceSemantics, OrderSide, OrderStatus,
                                    RunMode, TradeAction)
from trademind.domain.intents import TradeIntent
from trademind.domain.orders import Order, Fill
from trademind.errors import ProviderError
from trademind.market.base import MinuteBar
from trademind.market.quality import QualityReport
from trademind.pipeline import Pipeline

B = '513100.SH'  # second instrument held in the account (other-position cases)


def bars_at(anchor, age_seconds=20, count=20, iid=I, price='1.50'):
    last = anchor - timedelta(seconds=age_seconds)
    return [mbar(iid, last - timedelta(minutes=i), price) for i in reversed(range(count))]


def mbar(iid, at, price='1.50'):
    p = D(price)
    return MinuteBar(instrument_id=iid, trade_date=at.date(), bar_time=at,
                     open=p, high=p, low=p, close=p, volume=D('1000'), amount=p * 1000,
                     provider='test', source_time=at, available_at=at, fetched_at=at)


def violated_report(iid):
    # Router best-effort shape (no qualified source): visible fresh rows plus
    # a report that carries the quality rejection on the same window.
    report = QualityReport()
    report.limit_violations = [f'{iid}: high/low violation']
    return report


def slow_jev(pipe, tick, seconds):
    base = pipe.jev.session.post.side_effect

    def post(url, json, timeout):
        out = base(url, json, timeout)
        tick[0] += timedelta(seconds=seconds)
        return out

    pipe.jev.session.post.side_effect = post


def apply_buy(pipe, iid, at, price='1.50', qty=100):
    oid = new_id('o')
    order = Order(id=oid, trace_id='test', created_at=at, order_id=oid,
                  client_order_id=oid, intent_id='test', guard_id='test',
                  account_id=pipe.cfg.account_id, instrument_id=iid,
                  mode=RunMode.PAPER, side=OrderSide.BUY,
                  quantity=D(qty), limit_price=D(price), updated_at=at,
                  status=OrderStatus.ACKNOWLEDGED)
    fill = Fill(id=new_id('f'), trace_id='test', created_at=at, fill_id=new_id('fill'),
                provider_fill_id=new_id('pf'), order_id=oid, fee=D('0.1'),
                quantity=D(qty), price=D(price), executed_at=at, available_at=at)
    pipe.ledger.apply_fill(fill, order, at)


def recheck_rows(pipe):
    return pipe.db.query("SELECT payload_json FROM control_audit WHERE action='reference_price_recheck'")


# -- B01: the configured quote age caps the decision during model waits -------

def test_configured_quote_age_caps_decision_during_model_wait(tmp_path):
    """30s config + 20s-old bar + 25s flash wait: the pre-fix hardcoded
    bar_time+180s deadline (NOW+160) would have let NOW+25 through to the
    paid JEV call; the configured cap expires the decision instead."""
    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.risk.quote_max_age_seconds = 30
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20), None))
    base = pipe.llm.complete.side_effect

    def slow_flash(**kw):
        out = base(**kw)
        tick[0] += timedelta(seconds=25)
        return out

    pipe.llm.complete.side_effect = slow_flash
    result = pipe.run_decision(I, NOW)
    assert result.intent.action.value == 'WAIT'
    assert result.intent.reason == 'expired plan or context'
    # The deadline itself is the configured one: bar_time + 30, not + 180.
    assert result.intent.valid_until == NOW - timedelta(seconds=20) + timedelta(seconds=30)
    pipe.jev.session.post.assert_not_called()
    assert not result.order_id


def test_jev_wait_past_configured_deadline_refuses_action(tmp_path):
    """Same cap enforced after the JEV call: the post-model check compares
    the current clock against the config-derived deadline."""
    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.risk.quote_max_age_seconds = 30
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20), None))
    slow_jev(pipe, tick, 25)
    result = pipe.run_decision(I, NOW)
    assert result.intent.action.value == 'WAIT'
    assert result.intent.reason == 'expired during JEV request'
    assert result.intent.valid_until == NOW - timedelta(seconds=20) + timedelta(seconds=30)
    assert not result.order_id


# -- B01: execution-side recheck against the current clock + audit snapshot --

def test_execution_reference_price_rechecked_with_current_clock(tmp_path):
    """A BUY that survives the decision (default 180s cap) still passes the
    execution recheck when fresh quotes arrived during the JEV wait, and the
    round leaves the recheck audit row with both snapshots."""
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20), None))
    slow_jev(pipe, tick, 25)
    result = pipe.run_decision(I, NOW)
    assert result.order_id and result.qty == 100
    rows = recheck_rows(pipe)
    assert rows
    payload = loads(rows[0]['payload_json'])
    assert payload['outcome'] == 'passed'
    assert payload['decision']['as_of'] == NOW.isoformat()
    assert payload['decision']['price'] == '1.50'
    assert payload['recheck']['as_of'] == (NOW + timedelta(seconds=25)).isoformat()
    assert payload['recheck']['price'] == '1.50'


def test_recheck_rejection_blocks_execution_and_keeps_decision_snapshot(tmp_path):
    """Counterexample end-to-end: no newer bar during the wait, so the
    execution recheck judges the decision bar against the current clock and
    rejects, while the decision-time snapshot stays in the audit. R1 rework:
    the rejection now structurally carries the rejected window's quote and
    bar time instead of nulling them."""
    pipe, tick = make_pipe(tmp_path)
    fresh, stale = bars_at(NOW, 20), bars_at(NOW, 200)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (fresh if kw.get('as_of') == NOW else stale, None))
    slow_jev(pipe, tick, 25)
    result = pipe.run_decision(I, NOW)
    assert result.order_id is None
    assert result.error == 'NotFresh: no fresh visible minute quote'
    assert result.diagnostics['data_reject']['reason'] == 'no fresh visible minute quote'
    rows = recheck_rows(pipe)
    assert rows
    payload = loads(rows[0]['payload_json'])
    assert payload['outcome'] == 'rejected'
    assert payload['reason'] == 'no fresh visible minute quote'
    assert payload['decision']['as_of'] == NOW.isoformat()
    assert payload['decision']['price'] == '1.50'
    assert payload['recheck']['price'] == '1.50'  # the rejected quote, not a null
    assert payload['recheck']['bar_time'] == (NOW - timedelta(seconds=200)).isoformat()


# -- B02: other-position valuation passes the same quality gate ---------------

def other_position_route(tick):
    def route(iid, start, end, period='1m', as_of=None, quote_max_age_seconds=None, **kw):
        if iid == B:
            return [mbar(B, NOW)], violated_report(B)
        return bars_at(tick[0], 20), None

    return route


def test_other_position_quality_rejection_excluded_from_valuation(tmp_path):
    """The other holding's window is visible and fresh but quality-rejected:
    its close must not enter equity (avg-cost fallback) and the position must
    be marked valuation_incomplete."""
    pipe, tick = make_pipe(tmp_path)
    # Small cheap holding so a new BUY lot stays lawful for the pipeline test;
    # its 1.50-quality-rejected close differs from the 0.501 avg cost either way.
    apply_buy(pipe, B, NOW - timedelta(minutes=5), price='0.50')
    pipe.router.get_minute_bars = Mock(side_effect=other_position_route(tick))
    snap = pipe.context_builder.build_minute(I, NOW)
    assert snap.aux['valuation_incomplete'] == [B]
    # cash 449.9 (500 − 50 cost − 0.1 fee) + B at avg cost 50.1 (I holds no
    # position, so it adds nothing). The rejected window's 1.50 close would
    # have marked B at 150 instead — equity 599.9.
    assert D(str(snap.portfolio.equity)) == D('449.9') + D('50.1')
    assert D(snap.portfolio.positions[B]['market_value']) == D('50.1')


def test_main_symbol_quality_violation_still_rejects_whole_round(tmp_path):
    """Main-symbol regression: the unified gate keeps the round-level
    DataUnavailable with the same closed message."""
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: ([mbar(I, NOW)], violated_report(I)))
    with pytest.raises(DataUnavailable, match='minute OHLC quality validation failed'):
        pipe.context_builder.build_minute(I, NOW)


def test_pipeline_refuses_new_risk_when_other_position_valuation_incomplete(tmp_path):
    """Consequence chain closed end-to-end: the incomplete valuation reaches
    the Guard, which vetoes the new BUY risk."""
    pipe, tick = make_pipe(tmp_path)
    apply_buy(pipe, B, NOW - timedelta(minutes=5), price='0.50')
    pipe.router.get_minute_bars = Mock(side_effect=other_position_route(tick))
    result = pipe.run_decision(I, NOW)
    assert result.guard_verdict == 'VETO'
    assert 'incomplete_account_valuation' in result.guard_reasons
    assert not result.order_id
    assert any(f.startswith(B + ' ')
               for f in result.diagnostics.get('valuation_failures', []))


# -- R1 rework: the audited decision snapshot is the model's actual input -----

def test_async_consume_audits_model_round_snapshot(tmp_path):
    """Async path (R1 MAJOR-②): the consume round rebinds the pipeline's snap
    to the model-round source, so the audit's decision snapshot must carry
    the model-round as_of and bar — not the consumer round's newer window."""
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20), None))
    pipe.enable_async_models()
    try:
        assert pipe.run_decision(I, NOW).guard_verdict == 'WAIT_MODEL_PENDING'
        pipe._model_pending[0].result(timeout=2)
        tick[0] += timedelta(seconds=30)
        result = pipe.run_decision(I, tick[0])
        assert result.order_id and result.qty == 100
    finally:
        pipe._model_executor.shutdown(wait=True)
    payload = loads(recheck_rows(pipe)[0]['payload_json'])
    assert payload['decision']['as_of'] == NOW.isoformat()  # model round, not consume round
    assert payload['decision']['bar_time'] == (NOW - timedelta(seconds=20)).isoformat()
    assert payload['decision']['price'] == '1.50'
    assert payload['recheck']['as_of'] == (NOW + timedelta(seconds=30)).isoformat()
    assert payload['recheck']['bar_time'] == (NOW + timedelta(seconds=10)).isoformat()
    assert payload['recheck']['price'] == '1.50'
    assert payload['outcome'] == 'passed'


def test_reselection_cannot_replace_decision_snapshot(tmp_path):
    """R1 MAJOR-①: a source re-selection after the model returned must not
    audit a quote the model never saw. The router serves a shifted window as
    soon as the clock moves; the frozen decision snapshot stays the
    model-round bar and only the recheck carries the new window."""
    pipe, tick = make_pipe(tmp_path)

    def route(*a, **kw):
        if tick[0] > NOW:  # post-model re-selection serves a different window
            return bars_at(tick[0], 100), None
        return bars_at(tick[0], 20), None

    pipe.router.get_minute_bars = Mock(side_effect=route)
    slow_jev(pipe, tick, 25)
    result = pipe.run_decision(I, NOW)
    assert result.order_id
    payload = loads(recheck_rows(pipe)[0]['payload_json'])
    assert payload['decision']['bar_time'] == (NOW - timedelta(seconds=20)).isoformat()
    assert payload['decision']['price'] == '1.50'
    assert payload['recheck']['bar_time'] == (
        NOW + timedelta(seconds=25) - timedelta(seconds=100)).isoformat()
    assert payload['recheck']['price'] == '1.50'
    assert payload['outcome'] == 'passed'


# -- R1 rework MINOR-1: protective exit exemption -----------------------------

def test_time_stop_exit_exempt_from_recheck_and_fills_at_as_of_price(tmp_path):
    """R1 MINOR-1①: protective exits keep as_of pricing and never pass the
    execution-side recheck. The decision-time quote (20s old, legal under
    the 30s cap) is over the cap by fill time — the exit still fires and
    the match still completes."""
    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.risk.quote_max_age_seconds = 30
    apply_buy(pipe, I, NOW - timedelta(minutes=5), price='1.50')
    pipe.repo.save_coalesce('fast-holding:' + pipe.cfg.account_id + ':' + I,
                            NOW - timedelta(minutes=5),
                            {'deadline': (NOW - timedelta(seconds=5)).isoformat()})
    pipe.router.get_minute_bars = Mock(return_value=(bars_at(NOW, 20), None))
    result = pipe.run_decision(I, NOW)
    assert result.error is None
    assert result.order_id and result.portfolio_intent.action.value == 'SELL'
    assert not recheck_rows(pipe)  # the B01 recheck never gates this path
    exits = pipe.db.query("SELECT payload_json FROM control_audit WHERE action='risk_exit'")
    assert exits and loads(exits[0]['payload_json'])['reason'] == 'time_stop'
    tick[0] += timedelta(seconds=60)  # the decision quote is now 80s old (> cap)
    pipe.router.get_minute_bars = Mock(return_value=(bars_at(tick[0], 20), None))
    assert len(pipe.poll_fills(tick[0])) == 1
    assert not pipe.ledger.snapshot(tick[0]).positions


def test_other_position_valuation_failure_does_not_block_legit_sell(tmp_path):
    """R1 MINOR-1②: the other holding's quote failure lands in
    valuation_failures and must not block a legitimate SELL of the main
    holding — the incomplete-valuation Guard veto covers BUY risk only."""
    pipe, tick = make_pipe(tmp_path)
    apply_buy(pipe, B, NOW - timedelta(minutes=5), price='0.50')
    result = pipe.run_decision(I, NOW)  # healthy round: BUY the main holding
    assert result.order_id
    tick[0] += timedelta(minutes=1)
    assert len(pipe.poll_fills(tick[0])) == 1
    # Force the next legal action to SELL, then break only B's quote source.
    planner = pipe._fast_pipeline.planner
    row = pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['allowed_actions'] = ['SELL', 'HOLD', 'WAIT']
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])

    def route(iid, start, end, period='1m', as_of=None, quote_max_age_seconds=None, **kw):
        if iid == B:
            raise ProviderError('b source down')
        return bars_at(tick[0], 20), None

    pipe.router.get_minute_bars = Mock(side_effect=route)
    tick[0] += timedelta(minutes=4)
    sell = pipe.run_decision(I, tick[0])
    assert sell.intent.action.value == 'SELL' and sell.order_id
    assert any(f.startswith(B + ' ')
               for f in sell.diagnostics.get('valuation_failures', []))
    tick[0] += timedelta(minutes=1)
    assert len(pipe.poll_fills(tick[0])) == 1
    assert not any(p.instrument_id == I for p in pipe.ledger.snapshot(tick[0]).positions)
    assert any(p.instrument_id == B for p in pipe.ledger.snapshot(tick[0]).positions)


# -- R1 rework MINOR-2: the quality gate has no replay exemption --------------

def test_replay_mode_quality_gate_still_excludes_rejected_close(tmp_path):
    """R1 MINOR-2: replay mode gets no schema exemption — a quality-rejected
    other-position window is excluded from equity in replay exactly as in
    paper (decision-snapshot degradation applies to the B01 recheck only)."""
    pipe, tick = make_pipe(tmp_path, mode='replay')
    apply_buy(pipe, B, NOW - timedelta(minutes=5), price='0.50')
    pipe.router.get_minute_bars = Mock(side_effect=other_position_route(tick))
    snap = pipe.context_builder.build_minute(I, NOW)
    assert snap.aux['valuation_incomplete'] == [B]
    assert D(str(snap.portfolio.equity)) == D('449.9') + D('50.1')
    assert D(snap.portfolio.positions[B]['market_value']) == D('50.1')


# -- R1 rework MINOR-3: same quote, aging only by the clock -------------------

def test_same_quote_aged_only_by_clock_rejected_at_recheck(tmp_path, monkeypatch):
    """R1 MINOR-3: one fixed quote for every caller — no window swap — with
    only the clock advancing past the deadline inside a long dual-model
    wait. The execution recheck must reject on age alone and the audit must
    carry the rejected quote and time, not nulls."""
    tick = [NOW]
    cfg = AppConfig()
    cfg.t0.enabled = True  # default profile → dual-model chain, no fast decider
    cfg.llm.provider = 'stub'
    cfg.universe = [I]
    cfg.account_id = 'aging-test'
    cfg.data.provider_pref = ['fixture']
    cfg.storage.url = f'sqlite:///{tmp_path}/aging.db'
    pipe = Pipeline(cfg, clock=lambda: tick[0])
    pipe.ensure_account(at=NOW - timedelta(minutes=30))
    fixed = [mbar(I, NOW - timedelta(seconds=20))]
    pipe.router.get_minute_bars = Mock(return_value=(fixed, None))

    class _SlowDualModel:
        def __init__(self, **kw):
            pass

        def run(self, snap, trace_id=None):
            tick[0] += timedelta(seconds=200)  # model wait: only the clock moves
            return TradeIntent(
                id=new_id('ti'), trace_id=trace_id, created_at=tick[0],
                decision_id=new_id('fast'), context_id=snap.context_id,
                thesis_id='dual-no-plan', instrument_id=snap.instrument_id,
                portfolio_version=snap.portfolio.portfolio_version,
                account_id=pipe.cfg.account_id, action=TradeAction.SELL,
                target_weight=D('0'), current_weight=D('0'), confidence=D('1'),
                confidence_semantics=ConfidenceSemantics.ANSWER_DISTRIBUTION_CONFIDENCE,
                holding_horizon_seconds=0, evidence_ids=[], reason='test dual intent',
                exit_conditions=[], valid_until=tick[0] + timedelta(seconds=120),
                model='dual-stub', prompt_id='dual'), {}

    monkeypatch.setattr('trademind.agents.pipeline.DualModelPipeline', _SlowDualModel)
    result = pipe.run_decision(I, NOW)
    assert result.error == 'NotFresh: no fresh visible minute quote'
    assert result.diagnostics['data_reject']['reason'] == 'no fresh visible minute quote'
    assert not pipe.repo.open_orders()
    payload = loads(recheck_rows(pipe)[0]['payload_json'])
    assert payload['outcome'] == 'rejected'
    assert payload['reason'] == 'no fresh visible minute quote'
    assert payload['decision']['as_of'] == NOW.isoformat()
    assert payload['decision']['price'] == '1.50'
    assert payload['decision']['bar_time'] == (NOW - timedelta(seconds=20)).isoformat()
    # The rejected quote is the same fixed bar the whole round saw.
    assert payload['recheck']['as_of'] == (NOW + timedelta(seconds=200)).isoformat()
    assert payload['recheck']['price'] == '1.50'
    assert payload['recheck']['bar_time'] == (NOW - timedelta(seconds=20)).isoformat()
