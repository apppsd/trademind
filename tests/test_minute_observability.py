"""TASK-20261103-001 offline observability tests: minute-bar data gates,
pre-decision blocker buckets and daily-report denominators. All samples are
fixed offline bars; no market or model HTTP is involved."""
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock

import pytest

from trademind.execution.paper_run import PaperRun, read_intraday_health
from trademind.reports.daily import (generate_report, generate_for_config, render_markdown,
                                     write_report)
from trademind.storage import Database

from test_fast_decision import I, NOW, make_pipe, minute


def bars_at(tick, count, age_seconds=0, price='1.50'):
    """Ascending contiguous 1m bars ending `age_seconds` before the tick."""
    base = tick - timedelta(seconds=age_seconds)
    return [minute(base - timedelta(minutes=i), price) for i in reversed(range(count))]


def audit_rows(db, action):
    return db.query("SELECT action, payload_json FROM control_audit WHERE action=?", (action,))


def test_age_106s_with_14_bars_is_data_quality_not_stale(tmp_path):
    """~106s age is inside the 180s threshold; 14 contiguous bars fail only
    the feature warm-up (ret_15m needs 16, atr_14 needs 15) — a distinct
    WAIT_DATA_QUALITY gate, not a freshness NotFresh (Issue30-01)."""
    pipe, tick = make_pipe(tmp_path)
    tick[0] = NOW
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 14, age_seconds=106), None))
    result = pipe.run_decision(I, tick[0])
    assert result.error is None
    assert result.guard_verdict == 'WAIT_DATA_QUALITY'
    assert result.intent is None and result.order_id is None
    diag = result.diagnostics
    assert diag['age_threshold_seconds'] == 180
    assert diag['data_age_seconds'] == pytest.approx(106, abs=2)
    assert diag['data_window'] == {'continuous_1m_bars': 14,
                                   'required_for_ret_15m': 16, 'required_for_atr_14': 15}
    assert diag['missing_fields']['ret_15m'] == 'needs 16 contiguous 1m bars, have 14'
    assert diag['missing_fields']['atr_14'] == 'needs 15 contiguous 1m bars, have 14'
    assert 'freshness' not in diag['missing_fields'] and 'bars' not in diag['missing_fields']
    # The data-reject round is audited for the daily pre-decision denominator.
    rows = audit_rows(pipe.db, 'data_reject')
    assert len(rows) == 1
    payload = __import__('json').loads(rows[0]['payload_json'])
    assert payload['reason_class'] == 'WAIT_DATA_QUALITY'
    assert payload['reason'] == 'incomplete minute features'
    assert payload['instrument_id'] == I
    # No paid model call may follow an incomplete-feature gate.
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()


def test_overage_with_enough_bars_is_notfresh_subtype(tmp_path):
    """Full 20-bar features but a 200s-old last bar: freshness gate wins with
    its distinct stale subtype recorded in diagnostics and the audit row
    (with the available missing_fields), separate from feature incompleteness."""
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20, age_seconds=200), None))
    result = pipe.run_decision(I, tick[0])
    assert (result.error or '').startswith('NotFresh:')
    assert 'stale visible minute context' in result.error
    diag = result.diagnostics
    assert diag['data_age_seconds'] == pytest.approx(200, abs=2)
    assert diag['age_threshold_seconds'] == 180
    assert diag['data_window']['continuous_1m_bars'] == 20
    assert diag['data_reject'] == {'reason_class': 'NotFresh', 'reason': 'stale visible minute context',
                                   'missing_fields': {'freshness': 'latest bar is stale by 200s > 180s'}}
    rows = audit_rows(pipe.db, 'data_reject')
    assert len(rows) == 1
    payload = __import__('json').loads(rows[0]['payload_json'])
    assert payload['reason_class'] == 'NotFresh'
    assert payload['reason'] == 'stale visible minute context'
    assert payload['missing_fields'] == {'freshness': 'latest bar is stale by 200s > 180s'}
    pipe.llm.complete.assert_not_called()


def test_missing_bars_keeps_counts_unknown(tmp_path):
    """No visible bars at all: NotFresh round with data_window absent — the
    continuous count must stay unknown, never fabricated from a sample. The
    missing-bars subtype is distinct from the over-age one (review R2)."""
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([], None))
    result = pipe.run_decision(I, tick[0])
    assert (result.error or '').startswith('NotFresh:')
    assert result.diagnostics['data_window'] is None
    assert result.diagnostics['data_age_seconds'] is None
    assert result.diagnostics['missing_fields']['bars'] == 'no visible minute bars at as_of'
    assert result.diagnostics['data_reject'] == {
        'reason_class': 'NotFresh', 'reason': 'missing visible minute bars',
        'missing_fields': {'bars': 'no visible minute bars at as_of'}}
    rows = audit_rows(pipe.db, 'data_reject')
    assert len(rows) == 1
    payload = __import__('json').loads(rows[0]['payload_json'])
    assert payload['reason'] == 'missing visible minute bars'
    assert payload['account_id'] == pipe.cfg.account_id


def test_model_pending_round_audited_and_classified(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.enable_async_models()
    try:
        result = pipe.run_decision(I, tick[0])
        assert result.guard_verdict == 'WAIT_MODEL_PENDING'
        assert result.intent is None
        assert audit_rows(pipe.db, 'model_pending')
        # The public decision keeps the verdict; record_progress classifies it.
        from trademind.runner import _public_decision
        run = PaperRun(pipe, [I])
        run.record_progress([{'decision': _public_decision(result)}],
                            shutdown={'worker_active': False, 'unconsumed_result': False},
                            now=tick[0])
        health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                      now=tick[0], session_open=True, run_id=run.run_id)
        assert health['last_blocker_class'] == 'async_pending'
        assert health['last_blocker_class_zh'] == '异步模型结果待消费'
        # No halt and no data problem: the ordinary wait tier stays visible.
        assert health['health_status'] == '有效策略观望'
    finally:
        pipe.close_model_worker()


def test_health_buckets_distinguish_cooldown_data_and_strategy_wait(tmp_path):
    from trademind.agents.strategy_planner import StrategyPlanner
    from trademind.market.profiles import get_profile
    # Shadow mode: orders are vetoed, so no open order can mask the wait
    # tiers under 持仓风险处理 while buckets are being classified.
    pipe, tick = make_pipe(tmp_path, 'shadow')
    # A real BUY round claims the global window.
    assert pipe.run_decision(I, tick[0]).intent.action.value == 'BUY'
    # Next tick inside the window: saved WAIT intent with the cooldown reason.
    tick[0] += timedelta(seconds=1)
    result = pipe.run_decision(I, tick[0])
    assert result.intent.reason == 'global model evaluation cooldown'
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    # DB-only fallback classifies from the saved intent reason.
    assert health['last_blocker_class'] == 'global_cooldown'
    assert health['last_blocker_class_zh'] == '全局模型冷却'
    assert health['health_status'] == '有效策略观望'
    # Additive persisted-window facts: remaining seconds computed from the
    # claim row, unknown stays None.
    assert health['fast_eval_seconds'] == 300
    assert health['global_cooldown_seconds_remaining'] == 299
    assert health['global_cooldown_last_claim_at']

    # Strategy wait: window expired, cached plan without actionable conditions.
    tick[0] += timedelta(seconds=301)
    context = pipe.context_builder.build_minute(I, tick[0])
    planner = StrategyPlanner(pipe.llm, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
    planner.get(context, get_profile(I), 'seed')
    row = pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['entry_conditions'] = [dict(field='close', operator='gt', value='9')]
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])
    from trademind.agents.fast_pipeline import FastDecisionPipeline
    fast = FastDecisionPipeline(pipe.llm, pipe.jev, pipe.repo, pipe.cfg.t0,
                                pipe.cfg.account_id, pipe.ledger, pipe.calendar,
                                pipe.model_budget, lambda: tick[0], lambda: {})
    intent, _ = fast.run(context, 'strategy-wait')
    assert intent.reason == 'no actionable strategy conditions'
    pipe.repo.save_intent(intent, 'generated')
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert health['last_blocker_class'] == 'strategy_wait'
    assert health['health_status'] == '有效策略观望'

    # Data reject bucket via published progress: 行情不足 keeps its tier while
    # the blocker class names the reason family and carries the bar facts.
    run = PaperRun(pipe, [I])
    rejected = {'instrument': I, 'guard': 'WAIT_DATA_QUALITY', 'action': None, 'error': None,
                'diagnostics': {'reason': 'incomplete minute features',
                                'data_age_seconds': 106, 'age_threshold_seconds': 180,
                                'missing_fields': {'ret_15m': 'needs 16 contiguous 1m bars, have 14'},
                                'data_window': {'continuous_1m_bars': 14,
                                                'required_for_ret_15m': 16, 'required_for_atr_14': 15}}}
    run.record_progress([{'decision': rejected}], now=tick[0])
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, run_id=run.run_id)
    assert health['health_status'] == '行情不足'
    assert health['last_blocker_class'] == 'data_reject'
    assert health['last_blocker_class_zh'] == '行情数据拒绝'
    assert health['last_continuous_1m_bars'] == 14
    assert health['last_age_threshold_seconds'] == 180
    assert health['last_data_age_seconds'] == 106


def test_preflight_reports_threshold_window_and_reject_class(tmp_path):
    """Fixed offline samples through the real preflight: age inside threshold
    with short bars is WAIT_DATA_QUALITY; over-age with enough bars is
    NotFresh; a healthy sample passes with no blockers."""
    import trademind.runner as runner
    utc = timezone.utc
    pipe, tick = make_pipe(tmp_path)

    def preflight(count, age_seconds):
        pipe.router.get_minute_bars = Mock(
            side_effect=lambda *a, **kw: (bars_at(tick[0], count, age_seconds=age_seconds), None))
        return runner._fast_paper_preflight(
            pipe, [I], probe_models=False, clock=lambda: tick[0].astimezone(utc))

    report = preflight(14, 106)
    item = report['data'][0]
    assert item['ok'] is False
    assert item['reject_reason_class'] == 'WAIT_DATA_QUALITY'
    assert item['age_threshold_seconds'] == 180
    assert item['data_age_seconds'] == pytest.approx(106, abs=2)
    assert item['data_window']['continuous_1m_bars'] == 14
    assert item['as_of'].endswith('+08:00')
    assert 'fresh_complete_minute_data_unverified' in report['blockers']

    report = preflight(20, 200)
    item = report['data'][0]
    assert item['reject_reason_class'] == 'NotFresh'
    assert item['data_window']['continuous_1m_bars'] == 20

    report = preflight(20, 0)
    assert report['ok'] is True and report['blockers'] == []
    item = report['data'][0]
    assert item['ok'] is True and item['reject_reason_class'] is None
    assert item['data_window']['continuous_1m_bars'] == 20


def test_public_decision_keeps_new_diagnostic_keys(tmp_path):
    from trademind.runner import _public_decision
    pipe, tick = make_pipe(tmp_path)
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'BUY'
    summary = _public_decision(result)
    diag = summary['diagnostics']
    assert diag['age_threshold_seconds'] == 180
    assert diag['data_window']['continuous_1m_bars'] == 20
    assert diag['data_window']['required_for_ret_15m'] == 16
    assert diag['data_window']['required_for_atr_14'] == 15
    # The just-claimed window surfaces its remaining seconds.
    assert diag['global_cooldown_seconds_remaining'] == 300
    # Unknown data_window stays unknown in public summaries too.
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([], None))
    tick[0] += timedelta(seconds=1)
    rejected = pipe.run_decision(I, tick[0])
    assert (rejected.error or '').startswith('NotFresh')
    assert _public_decision(rejected)['diagnostics']['data_window'] is None


def _seed_cached_plan(pipe, tick, entry_conditions=None, allowed_actions=None):
    """Seed a valid cached plan through the real planner, then optionally
    narrow its conditions/actions (same pattern as the bucket tests above)."""
    from trademind.agents.strategy_planner import StrategyPlanner
    from trademind.market.profiles import get_profile
    context = pipe.context_builder.build_minute(I, tick[0])
    planner = StrategyPlanner(pipe.llm, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
    planner.get(context, get_profile(I), 'seed')
    row = pipe.repo.get_coalesce(planner.key(I))
    if entry_conditions is not None:
        row['meta']['plan']['entry_conditions'] = entry_conditions
    if allowed_actions is not None:
        row['meta']['plan']['allowed_actions'] = allowed_actions
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])
    return context, row['meta']['plan']['plan_id']


def _fast(pipe, tick):
    from trademind.agents.fast_pipeline import FastDecisionPipeline
    return FastDecisionPipeline(pipe.llm, pipe.jev, pipe.repo, pipe.cfg.t0,
                                pipe.cfg.account_id, pipe.ledger, pipe.calendar,
                                pipe.model_budget, lambda: tick[0], lambda: {})


def test_no_actionable_gate_lists_failing_entry_condition(tmp_path):
    """t279u item A: a cached WAIT whose only failure is one entry condition
    carries per-condition truth evidence — real actual value, matched=false,
    threshold kept — with no JEV call and no window claim of any kind."""
    pipe, tick = make_pipe(tmp_path, 'shadow')
    context, plan_id = _seed_cached_plan(pipe, tick,
                                         entry_conditions=[dict(field='close', operator='gt', value='9')])
    intent, meta = _fast(pipe, tick).run(context, 'gate-evidence')
    assert intent.reason == 'no actionable strategy conditions'
    gate = meta['diagnostics']['action_gate']
    assert gate['plan_id'] == plan_id
    assert gate['allowed_actions'] == ['WAIT']
    assert gate['exclusion'] == {'BUY': 'conditions_unmet', 'SELL': 'no_position_or_qty',
                                 'HOLD': 'no_position_or_qty'}
    assert gate['entry_conditions'] == [{'field': 'close', 'operator': 'gt', 'threshold': '9',
                                         'actual': '1.50', 'matched': False, 'missing': False}]
    assert gate['exit_conditions'][0]['matched'] is True
    # Pure cached WAIT: no model path, no claim row, cooldown report complete.
    assert meta['model_path_claimed'] is False and meta['jev_called'] is False
    window = meta['diagnostics']['model_window']
    assert window == {'key': 'fast-eval:' + pipe.cfg.account_id, 'scope': 'account',
                      'effective_interval_seconds': 300, 'last_claim_at': None,
                      'next_eligible_at': None, 'remaining_seconds': None,
                      'claim_this_tick': False, 'claim_role': None, 'claim_trace_id': None}
    assert pipe.repo.get_coalesce('fast-eval:' + pipe.cfg.account_id) is None
    pipe.jev.session.post.assert_not_called()


def test_plan_disallow_buy_shows_plan_disallowed_exclusion(tmp_path):
    """The plan-level gate is named even when the entry conditions themselves
    are satisfied — exclusion classes match build_fast_state, not guesswork."""
    pipe, tick = make_pipe(tmp_path, 'shadow')
    context, _ = _seed_cached_plan(pipe, tick, allowed_actions=['SELL', 'HOLD', 'WAIT'])
    intent, meta = _fast(pipe, tick).run(context, 'gate-plan')
    assert intent.reason == 'no actionable strategy conditions'
    gate = meta['diagnostics']['action_gate']
    assert gate['plan_allowed_actions'] == ['SELL', 'HOLD', 'WAIT']
    assert gate['exclusion']['BUY'] == 'plan_disallowed'
    assert gate['entry_conditions'][0]['matched'] is True  # conditions met; plan is the blocker


def test_action_gate_names_pending_order_and_cash_reserve_classes():
    """The two classes invisible above the fast-state build: a pending
    instrument order and a cash-reserve shortfall each get their own slug."""
    from trademind.context.fast_state import action_gate
    from trademind.domain.strategy import FastDecisionState, StrategyPlan
    def plan():
        return StrategyPlan(plan_id='plan-g', instrument_id=I, market='XSHG', rule_version='r',
            created_at=NOW, data_cutoff=NOW, available_at=NOW,
            valid_until=NOW + timedelta(seconds=600), evidence_ids=['e'],
            trend='t', risk='r', critic='c', reason='测试',
            allowed_actions=['BUY', 'SELL', 'HOLD', 'WAIT'],
            entry_conditions=[dict(field='close', operator='gt', value='1')],
            exit_conditions=[dict(field='close', operator='lt', value='2')],
            invalidation_conditions=[], holding_horizon_seconds=300)
    def state(account):
        return FastDecisionState(context_id='c', instrument_id=I, observed_at=NOW, data_cutoff=NOW,
            source_time=NOW, fetched_at=NOW, available_at=NOW, market='XSHG', rule_version='r',
            features={'close': '1.50'}, bars=[], account=account, allowed_actions=['WAIT'],
            plan=plan(), quality={}, fees={}, risk_limits={'reserve': '500'}, budget={})
    def account(cash, reserved, orders):
        return {'id': 'a', 'version': 1, 'cash': cash, 'reserved_cash': reserved,
                'equity': '500', 'positions': [], 'open_orders': orders}
    pending = action_gate(state(account('500', '0', [{'instrument_id': I}])))
    assert pending['exclusion'] == {'BUY': 'pending_order', 'SELL': 'pending_order',
                                    'HOLD': 'no_position_or_qty'}
    tight = action_gate(state(account('500', '100', [])))
    assert tight['exclusion']['BUY'] == 'cash_reserve'
    assert tight['exclusion']['SELL'] == 'no_position_or_qty'


def test_public_decision_and_health_surface_action_gate_and_window_claim(tmp_path):
    """The public allowlist and intraday health carry the gate plus the full
    cooldown-claim report — before, during and after a real claim."""
    from trademind.runner import _public_decision
    pipe, tick = make_pipe(tmp_path, 'shadow')
    context, _ = _seed_cached_plan(pipe, tick,
                                   entry_conditions=[dict(field='close', operator='gt', value='9')])
    result = pipe.run_decision(I, tick[0])
    assert result.intent.reason == 'no actionable strategy conditions'
    summary = _public_decision(result)
    assert summary['diagnostics']['action_gate']['entry_conditions'][0]['matched'] is False
    window = summary['diagnostics']['model_window']
    assert window['claim_this_tick'] is False and window['last_claim_at'] is None
    run = PaperRun(pipe, [I])
    run.record_progress([{'decision': summary}], now=tick[0])
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0],
                                  session_open=True, run_id=run.run_id, t0_cfg=pipe.cfg.t0)
    assert health['last_action_gate']['entry_conditions'][0]['threshold'] == '9'
    assert health['global_cooldown_key'] == 'fast-eval:' + pipe.cfg.account_id
    assert health['global_cooldown_scope'] == 'account'
    assert health['global_cooldown_seconds_remaining'] is None
    assert health['global_cooldown_next_eligible_at'] is None
    assert health['last_model_path_claimed'] is False
    # A real BUY round claims the window; the next tick reports the full claim.
    row = pipe.repo.get_coalesce(pipe._fast_pipeline.planner.key(I))
    row['meta']['plan']['entry_conditions'] = [dict(field='close', operator='gt', value='1')]
    pipe.repo.save_coalesce(pipe._fast_pipeline.planner.key(I), tick[0], row['meta'])
    tick[0] += timedelta(seconds=1)
    claim_at = tick[0]
    assert pipe.run_decision(I, tick[0]).intent.action.value == 'BUY'
    tick[0] += timedelta(seconds=1)
    cooldown = _public_decision(pipe.run_decision(I, tick[0]))
    assert cooldown['diagnostics']['reason'] == 'global model evaluation cooldown'
    assert 'action_gate' not in cooldown['diagnostics']
    claim = cooldown['diagnostics']['model_window']
    assert claim['last_claim_at'] == claim_at.isoformat()
    assert claim['next_eligible_at'] == (claim_at + timedelta(seconds=300)).isoformat()
    assert claim['remaining_seconds'] == 299
    assert claim['claim_this_tick'] is False and claim['claim_role'] == 'jev'
    assert claim['claim_trace_id']  # persisted claim keeps its trace link (rereview R3)
    run.record_progress([{'decision': cooldown}], now=tick[0])
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0],
                                  session_open=True, run_id=run.run_id, t0_cfg=pipe.cfg.t0)
    assert health['global_cooldown_seconds_remaining'] == 299
    assert health['global_cooldown_next_eligible_at'] == (claim_at + timedelta(seconds=300)).isoformat()
    assert health['global_cooldown_claim_role'] == 'jev'
    assert health['last_action_gate'] is None  # the cooldown round has no gate
    assert health['last_model_path_claimed'] is False


def test_daily_report_surfaces_action_gate_with_coverage_note(tmp_path):
    """Saved intents carry the audited gate when present; historical
    no-actionable intents are never backfilled — the coverage note says so,
    and the PnL math stays untouched."""
    gate = {'plan_id': 'plan-9', 'allowed_actions': ['WAIT'],
            'plan_allowed_actions': ['BUY', 'WAIT'],
            'exclusion': {'BUY': 'conditions_unmet', 'SELL': 'no_position_or_qty',
                          'HOLD': 'no_position_or_qty'},
            'entry_conditions': [{'field': 'close', 'operator': 'gt', 'threshold': '9',
                                  'actual': '1.50', 'matched': False, 'missing': False}],
            'exit_conditions': []}
    db = Database('sqlite:///' + str(tmp_path / 'daily.db'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',
               ('paper-test', 1, '500', '0', NOW.isoformat(), '{}'))
    _event_row(db, 1, '2026-10-11T10:00:00+08:00')
    _intent_row(db, 'd2', 0, '2026-10-12T09:50:00+08:00', 'no actionable strategy conditions')
    _audit_row(db, 'a5', 't0_chain', '2026-10-12T09:50:05+08:00',
               {'decision_id': 'd2', 'meta': {'diagnostics': {'action_gate': gate}}})
    _intent_row(db, 'd3', 0, '2026-10-12T09:55:00+08:00', 'no actionable strategy conditions')
    report = generate_report(db.path, 'paper-test', '2026-10-12', NOW)
    by_id = {d['decision_id']: d for d in report['decisions']}
    assert by_id['d2']['action_gate']['entry_conditions'][0]['actual'] == '1.50'
    assert 'action_gate' not in by_id['d3']
    assert '2 条' in report['action_gate_coverage_note'] and '1 条携带逐条件证据' in report['action_gate_coverage_note']
    markdown = render_markdown(report)
    assert '## 无可行动条件逐条件证据' in markdown
    assert 'BUY=条件未满足' in markdown and '收盘价大于9' in markdown
    assert '不补造' in markdown
    assert report['ending']['cash'] == '500' and report['trading_pnl'] == '0'
    db.close()


def _intent_row(db, ident, version, at, reason, action='WAIT'):
    payload = {'decision_id': ident, 'intent_version': version, 'instrument_id': I,
               'created_at': at, 'action': action, 'reason': reason,
               'account_id': 'paper-test'}
    # Column order per schema: decision_id, intent_version, instrument_id,
    # action, valid_until, status, payload_json, created_at.
    db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?)',
               (ident, version, I, action, at, 'generated', __import__('json').dumps(payload), at))


def _audit_row(db, ident, action, at, payload):
    db.execute('INSERT INTO control_audit VALUES (?,?,?,?,?,?)',
               (ident, 'decision', action, 'tr-' + ident, at, __import__('json').dumps(payload)))


def _event_row(db, seq, at, amount='500'):
    e = {'account_id': 'paper-test', 'seq': seq, 'kind': 'deposit', 'effective_at': at,
         'amount': amount, 'quantity': None, 'currency': 'CNY', 'instrument_id': I,
         'note': '', 'order_id': f'order-{seq}'}
    db.execute('INSERT INTO account_events(account_id,seq,kind,instrument_id,amount,effective_at,payload_json) '
               'VALUES (?,?,?,?,?,?,?)',
               ('paper-test', seq, 'deposit', I, amount, at, __import__('json').dumps(e)))


def test_daily_report_separates_intent_and_pre_round_denominators(tmp_path):
    """619-style saved-intent counts and pre-decision reject/pending rounds
    stay two independent denominators; old days without per-round audit rows
    say so instead of pretending a recoverable denominator."""
    db = Database('sqlite:///' + str(tmp_path / 'daily.db'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',
               ('paper-test', 1, '500', '0', NOW.isoformat(), '{}'))
    _event_row(db, 1, '2026-10-11T10:00:00+08:00')
    # Same decision_id twice → dedupe keeps one entry.
    _intent_row(db, 'd1', 0, '2026-10-12T09:40:00+08:00', 'global model evaluation cooldown')
    _intent_row(db, 'd1', 1, '2026-10-12T09:45:00+08:00', 'global model evaluation cooldown')
    _intent_row(db, 'd2', 0, '2026-10-12T09:50:00+08:00', 'no actionable strategy conditions')
    # Pre-decision rounds: two data rejects with different subtypes, one
    # async-pending round, one other-day row that must not leak in. All rows
    # carry this account's attribution (unattributed rows are covered by the
    # dedicated account-scoping test below).
    _audit_row(db, 'a1', 'data_reject', '2026-10-12T09:41:00+08:00',
               {'account_id': 'paper-test', 'reason_class': 'WAIT_DATA_QUALITY',
                'reason': 'incomplete minute features', 'instrument_id': I})
    _audit_row(db, 'a2', 'data_reject', '2026-10-12T09:42:00+08:00',
               {'account_id': 'paper-test', 'reason_class': 'NotFresh',
                'reason': 'missing or stale visible minute context', 'instrument_id': I})
    _audit_row(db, 'a3', 'model_pending', '2026-10-12T09:43:00+08:00',
               {'account_id': 'paper-test', 'instrument_id': I})
    _audit_row(db, 'a4', 'data_reject', '2026-10-11T09:41:00+08:00',
               {'account_id': 'paper-test', 'reason_class': 'NotFresh', 'reason': 'outside day'})
    report = generate_report(db.path, 'paper-test', '2026-10-12', NOW)
    dist = report['decision_distribution']
    assert dist['denominator'] == 2  # d1 deduped to its max intent_version
    assert dist['by_reason'] == {'global model evaluation cooldown': 1,
                                 'no actionable strategy conditions': 1}
    assert dist['by_action'] == {'WAIT': 2}
    assert dist['cutoff'] == report['as_of']
    assert 'dedupe_rule' in dist and 'saved intents only' in dist['denominator_semantics']
    pre = report['pre_decision_rounds']
    assert pre['buckets']['data_reject']['count'] == 2
    assert pre['buckets']['data_reject']['reasons'] == {
        'incomplete minute features': 1, 'missing or stale visible minute context': 1}
    assert pre['buckets']['model_pending']['count'] == 1
    assert 'control_audit' in pre['source'] and pre['coverage_note']
    markdown = render_markdown(report)
    assert '## 已留档决策原因分布' in markdown
    assert '模型判断处于全局冷却期' in markdown
    assert '## 前置数据拒绝与异步等待轮次' in markdown
    assert '异步模型等待轮次' in markdown
    assert '分母不可恢复' in markdown
    db.close()


def test_daily_report_empty_pre_rounds_and_pnl_math_untouched(tmp_path):
    """A day with intents but no pre-decision audit rows states the buckets
    are absent instead of printing zero rounds, and the cash math is unchanged."""
    db = Database('sqlite:///' + str(tmp_path / 'daily.db'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',
               ('paper-test', 1, '500', '0', NOW.isoformat(), '{}'))
    _event_row(db, 1, '2026-10-11T10:00:00+08:00')
    _intent_row(db, 'd1', 0, '2026-10-12T09:40:00+08:00', 'global model evaluation cooldown')
    report = generate_report(db.path, 'paper-test', '2026-10-12', NOW)
    assert report['decision_distribution']['denominator'] == 1
    assert report['pre_decision_rounds']['buckets'] == {}
    assert report['ending']['cash'] == '500'
    assert report['trading_pnl'] == '0'
    markdown = render_markdown(report)
    assert '当日没有已留档的前置拒绝或异步等待轮次' in markdown
    db.close()


def test_pre_decision_rounds_are_account_scoped(tmp_path):
    """Review R1: only rows attributed to the report account enter its
    per-action denominators. Foreign-account rows are excluded outright;
    missing/blank attribution and unparseable payloads land in dedicated
    unknown_account buckets — never folded into the current account."""
    db = Database('sqlite:///' + str(tmp_path / 'daily.db'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',
               ('paper-test', 1, '500', '0', NOW.isoformat(), '{}'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',
               ('another-account', 1, '500', '0', NOW.isoformat(), '{}'))
    _event_row(db, 1, '2026-10-11T10:00:00+08:00')
    # One attributed round for the report account.
    _audit_row(db, 'a1', 'data_reject', '2026-10-12T09:41:00+08:00',
               {'account_id': 'paper-test', 'reason_class': 'NotFresh',
                'reason': 'stale visible minute context', 'instrument_id': I})
    # Foreign-account rounds must not inflate the paper-test denominators.
    _audit_row(db, 'a2', 'data_reject', '2026-10-12T09:42:00+08:00',
               {'account_id': 'another-account', 'reason_class': 'NotFresh',
                'reason': 'foreign account reason', 'instrument_id': I})
    _audit_row(db, 'a3', 'model_pending', '2026-10-12T09:43:00+08:00',
               {'account_id': 'another-account', 'instrument_id': I})
    # Missing attribution and an unparseable payload: unknown buckets only.
    _audit_row(db, 'a4', 'data_reject', '2026-10-12T09:44:00+08:00',
               {'reason_class': 'NotFresh', 'reason': 'orphan reason', 'instrument_id': I})
    _audit_row(db, 'a5', 'model_pending', '2026-10-12T09:45:00+08:00', 'not-json{')
    report = generate_report(db.path, 'paper-test', '2026-10-12', NOW)
    pre = report['pre_decision_rounds']
    assert pre['buckets']['data_reject'] == {'count': 1,
                                             'reasons': {'stale visible minute context': 1}}
    assert 'model_pending' not in pre['buckets']
    assert pre['buckets']['unknown_account:data_reject'] == {'count': 1,
                                                             'reasons': {'unknown': 1}}
    assert pre['buckets']['unknown_account:model_pending'] == {'count': 1, 'reasons': {'unknown': 1}}
    assert '属于其他账户' in pre['coverage_note'] and '缺少账户归属' in pre['coverage_note']
    markdown = render_markdown(report)
    assert '无法归属账户的行情数据拒绝轮次' in markdown
    db.close()


def test_notfresh_subtypes_reach_public_summary_health_and_daily(tmp_path):
    """Review R2 end-to-end: missing bars and over-age raise distinct safe
    subtypes that survive the public summary (whose error truncates to the
    bare NotFresh class), surface in intraday health, and split the daily
    data_reject aggregation. Reference-quote and async subtypes keep their
    own reasons through the same chain."""
    from trademind.runner import _public_decision
    # (a) Missing bars → public summary + health.
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([], None))
    summary = _public_decision(pipe.run_decision(I, tick[0]))
    assert summary['error'] == 'NotFresh'
    assert summary['diagnostics']['data_reject']['reason'] == 'missing visible minute bars'
    assert summary['diagnostics']['missing_fields']['bars'] == 'no visible minute bars at as_of'
    run = PaperRun(pipe, [I])
    run.record_progress([{'decision': summary}], now=tick[0])
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, run_id=run.run_id)
    assert health['last_decision_reason'] == 'missing visible minute bars'
    assert health['last_decision_reason_zh'] == 'as_of 时点缺少可见分钟K线'
    assert health['last_blocker_class'] == 'data_reject'
    assert health['health_status'] == '行情不足'
    # (b) Over-age → distinct subtype in the same public chain.
    tick[0] += timedelta(seconds=1)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20, age_seconds=200), None))
    stale = _public_decision(pipe.run_decision(I, tick[0]))
    assert stale['error'] == 'NotFresh'
    assert stale['diagnostics']['data_reject']['reason'] == 'stale visible minute context'
    # (c) The daily aggregation splits the two subtypes. Audit rows carry
    # wall-clock `at`, so the sample re-stamps them onto the controlled clock
    # before generating the report (offline test only).
    pipe.db.execute("UPDATE control_audit SET at=? WHERE action='data_reject'",
                    (tick[0].isoformat(),))
    report = generate_report(pipe.db.path, pipe.cfg.account_id, '2026-10-12', tick[0])
    pre = report['pre_decision_rounds']
    assert pre['buckets']['data_reject']['reasons'] == {
        'missing visible minute bars': 1, 'stale visible minute context': 1}
    pipe.release_writer_lease()
    # (d) Reference-quote failure after a clean context build keeps its own
    # subtype as a closed constant: the instrument id moved out of the
    # reason string (it stays in the structured audit field). Its own
    # directory: the writer lease is per database file.
    ref_dir = tmp_path / 'ref-quote'
    ref_dir.mkdir()
    pipe3, tick3 = make_pipe(ref_dir)
    calls = {'n': 0}
    def fresh_then_stale(*a, **kw):
        calls['n'] += 1
        if calls['n'] == 1:
            return (bars_at(tick3[0], 20), None)
        return (bars_at(tick3[0], 20, age_seconds=200), None)
    pipe3.router.get_minute_bars = Mock(side_effect=fresh_then_stale)
    quote = _public_decision(pipe3.run_decision(I, tick3[0]))
    assert quote['error'] == 'NotFresh'
    assert quote['diagnostics']['data_reject']['reason'] == 'no fresh visible minute quote'
    # (e) Async result whose plan conditions no longer hold at consumption.
    async_dir = tmp_path / 'async-conditions'
    async_dir.mkdir()
    pipe4, tick4 = make_pipe(async_dir, 'shadow')
    pipe4.enable_async_models()
    try:
        assert pipe4.run_decision(I, tick4[0]).guard_verdict == 'WAIT_MODEL_PENDING'
        intent, _meta = pipe4._model_pending[0].result(timeout=5)
        assert intent.action.value == 'BUY'
        tick4[0] += timedelta(seconds=1)
        pipe4.router.get_minute_bars = Mock(
            side_effect=lambda *a, **kw: (bars_at(tick4[0], 20, price='0.50'), None))
        async_summary = _public_decision(pipe4.run_decision(I, tick4[0]))
        assert async_summary['error'] == 'NotFresh'
        assert async_summary['diagnostics']['data_reject']['reason'] == \
            'async strategy conditions changed before execution'
    finally:
        pipe4._model_executor.shutdown(wait=True)


def test_age_threshold_follows_configured_quote_max_age(tmp_path):
    """Review R4: the reported threshold is the effective gate
    min(180, quote_max_age_seconds), on both sides of a tighter 60s gate.
    The intent-validity 180s bound is reported separately and unchanged."""
    from trademind.runner import _public_decision
    pipe, tick = make_pipe(tmp_path, 'shadow')
    pipe.cfg.risk.quote_max_age_seconds = 60
    summary = _public_decision(pipe.run_decision(I, tick[0]))
    assert summary['action'] == 'BUY'
    assert summary['diagnostics']['age_threshold_seconds'] == 60
    assert summary['diagnostics']['intent_max_age_seconds'] == 180
    # Rejected side: a 100s-old last bar is inside 180 but outside 60.
    tick[0] += timedelta(seconds=1)
    pipe.router.get_minute_bars = Mock(
        side_effect=lambda *a, **kw: (bars_at(tick[0], 20, age_seconds=100), None))
    rejected = _public_decision(pipe.run_decision(I, tick[0]))
    assert rejected['error'] == 'NotFresh'
    assert rejected['diagnostics']['age_threshold_seconds'] == 60
    assert rejected['diagnostics']['data_reject']['reason'] == 'stale visible minute context'


def test_generate_for_config_carries_window_and_claim_facts(tmp_path):
    """Review R3: the report entry keeps the effective config — the window
    interval and next-eligible time resolve from the persisted claim row
    instead of staying unknown, saved decisions carry their per-round
    model_window (claim trace included), and the Markdown/HTML show the
    cooldown evidence block."""
    from trademind.runner import _public_decision
    pipe, tick = make_pipe(tmp_path, 'shadow')
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'BUY'
    run = PaperRun(pipe, [I])
    # Progress rows are matched against the health read's calendar day. For a
    # past-day report the cutoff is that day's midnight, so the fixture stamps
    # the progress row at the cutoff instant (offline test only).
    from trademind.market.base import BEIJING
    cutoff = datetime(2026, 10, 13, 0, 0, tzinfo=BEIJING)
    run.record_progress([{'decision': _public_decision(result)}], now=cutoff)
    claim_at = tick[0]
    # The t0_chain audit row is stamped with wall clock; align it to the
    # controlled report day so the daily read window finds it (offline test).
    pipe.db.execute('UPDATE control_audit SET at=?', (claim_at.isoformat(),))
    report, _output = generate_for_config(pipe.cfg, '2026-10-12')
    ih = report['intraday_health']
    assert ih['fast_eval_seconds'] == 300
    assert ih['global_cooldown_next_eligible_at'] == (claim_at + timedelta(seconds=300)).isoformat()
    # The claim is long expired at the day cutoff — a computed 0, not unknown.
    assert ih['global_cooldown_seconds_remaining'] == 0
    win = next(d['model_window'] for d in report['decisions'] if d.get('model_window'))
    assert win['claim_this_tick'] is True
    # With no cached plan the flash refresh performs the first admission, so
    # the persisted claim role is the strategy role (JEV reuses the claim).
    assert win['claim_role'] == 'strategy'
    assert win['claim_trace_id']
    markdown = render_markdown(report)
    assert '## 全局模型窗口（只读事实）' in markdown
    assert '评估窗口间隔' in markdown and '剩余：' in markdown
    assert '最近认领' in markdown and '最近一轮是否认领窗口：是' in markdown


# -- Review R2 round 3: closed public subtype whitelist -----------------------

def test_classify_not_fresh_reason_is_a_closed_whitelist():
    """Only the whitelist constants and the three legacy stable prefixes
    survive classification; arbitrary bodies, unknown text and non-string
    values all collapse to 'unknown' — the classifier can never emit a
    string outside NOT_FRESH_PUBLIC_REASONS."""
    from trademind.errors import NOT_FRESH_PUBLIC_REASONS, classify_not_fresh_reason
    # Every allowed constant is idempotent and the set is pairwise distinct.
    for constant in NOT_FRESH_PUBLIC_REASONS:
        assert classify_not_fresh_reason(constant) == constant
    assert len(NOT_FRESH_PUBLIC_REASONS) == len(set(NOT_FRESH_PUBLIC_REASONS))
    # Legacy dynamic strings map by stable prefix; no detail is carried over.
    assert classify_not_fresh_reason('no fresh visible minute quote for 510300') == \
        'no fresh visible minute quote'
    assert classify_not_fresh_reason('no reference price for 513100') == 'no reference price'
    assert classify_not_fresh_reason(
        'trading calendar coverage insufficient: SYNTHETIC_WRAP') == \
        'trading calendar coverage insufficient'
    # The legacy merged subtype and the feature gate stay known constants,
    # so historical buckets remain distinguishable.
    assert classify_not_fresh_reason('missing or stale visible minute context') == \
        'missing or stale visible minute context'
    assert classify_not_fresh_reason('incomplete minute features') == 'incomplete minute features'
    # Anything else — including injected bodies — is 'unknown'.
    assert classify_not_fresh_reason('SYNTHETIC_UNTRUSTED_EXCEPTION_BODY') == 'unknown'
    assert classify_not_fresh_reason(None) == 'unknown'
    assert classify_not_fresh_reason({'reason': 'x'}) == 'unknown'


def test_synthetic_notfresh_body_never_reaches_any_consumer(tmp_path):
    """Round3 probe regression: a NotFresh raised with an arbitrary body
    must not leak that body into the public summary, intraday health (fresh
    or legacy progress file), or the daily JSON/Markdown/HTML — consumers
    see only the closed 'unknown' subtype."""
    body = 'SYNTHETIC_UNTRUSTED_EXCEPTION_BODY api_key=sk-leak-token'
    from trademind.errors import NotFresh
    from trademind.runner import _public_decision
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(side_effect=NotFresh(body))
    summary = _public_decision(pipe.run_decision(I, tick[0]))
    assert summary['error'] == 'NotFresh'
    assert summary['diagnostics']['data_reject'] == {'reason_class': 'NotFresh',
                                                     'reason': 'unknown'}
    assert body not in __import__('json').dumps(summary)
    run = PaperRun(pipe, [I])
    run.record_progress([{'decision': summary}], now=tick[0])
    health = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, run_id=run.run_id)
    assert health['last_decision_reason'] == 'unknown'
    assert body not in __import__('json').dumps(health)
    # A progress file written by an earlier build carrying the raw body
    # still cannot leak: the health boundary re-classifies data-channel
    # reasons on read.
    progress_path = pipe.db.path.parent / 'intraday-health.json'
    leaked = __import__('json').loads(progress_path.read_text(encoding='utf-8'))
    leaked['last_decision_reason'] = body
    leaked['last_blocker_class'] = 'data_reject'
    progress_path.write_text(__import__('json').dumps(leaked), encoding='utf-8')
    legacy = read_intraday_health(pipe.db.path, pipe.cfg.account_id,
                                  now=tick[0], session_open=True, run_id=run.run_id)
    assert legacy['last_decision_reason'] == 'unknown'
    assert body not in __import__('json').dumps(legacy)
    # The daily aggregation re-classifies the audit row; JSON/MD/HTML stay
    # free of the body. Audit rows carry wall-clock `at`, so the sample
    # re-stamps them onto the controlled clock (offline test only).
    pipe.db.execute("UPDATE control_audit SET at=? WHERE action='data_reject'",
                    (tick[0].isoformat(),))
    report = generate_report(pipe.db.path, pipe.cfg.account_id, '2026-10-12', tick[0])
    assert report['pre_decision_rounds']['buckets']['data_reject']['reasons'] == {'unknown': 1}
    assert body not in __import__('json').dumps(report)
    assert body not in render_markdown(report)
    output = write_report(report, tmp_path / 'daily-out')
    written = '\n'.join(p.read_text(encoding='utf-8') for p in Path(output).parent.glob('*'))
    assert body not in written
    pipe.release_writer_lease()


def test_legacy_dynamic_raise_sites_classify_through_the_catch(tmp_path):
    """A NotFresh carrying a legacy dynamic message (instrument id, wrapped
    calendar error) is classified at the recording point: the public block
    and the audit row keep only the closed constant, never the raw text."""
    from trademind.errors import NotFresh
    from trademind.runner import _public_decision
    # (a) Legacy instrument-embedding reference-quote message.
    pipe, tick = make_pipe(tmp_path)
    pipe.router.get_minute_bars = Mock(
        side_effect=NotFresh('no fresh visible minute quote for ' + I))
    summary = _public_decision(pipe.run_decision(I, tick[0]))
    assert summary['diagnostics']['data_reject']['reason'] == 'no fresh visible minute quote'
    assert I not in summary['diagnostics']['data_reject']['reason']
    rows = audit_rows(pipe.db, 'data_reject')
    assert __import__('json').loads(rows[0]['payload_json'])['reason'] == \
        'no fresh visible minute quote'
    pipe.release_writer_lease()
    # (b) Wrapped calendar error: the underlying text never surfaces.
    wrap_dir = tmp_path / 'calendar-wrap'
    wrap_dir.mkdir()
    pipe2, tick2 = make_pipe(wrap_dir)
    pipe2.router.get_minute_bars = Mock(
        side_effect=NotFresh('trading calendar coverage insufficient: SYNTHETIC_WRAP'))
    wrapped = _public_decision(pipe2.run_decision(I, tick2[0]))
    assert wrapped['diagnostics']['data_reject']['reason'] == \
        'trading calendar coverage insufficient'
    assert 'SYNTHETIC_WRAP' not in __import__('json').dumps(wrapped['diagnostics'])
    pipe2.release_writer_lease()
