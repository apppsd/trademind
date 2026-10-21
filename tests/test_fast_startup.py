"""Startup gates must prevent paid transport, without disabling hard exits."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from test_fast_decision import make_pipe, minute, I, NOW, Response
from trademind import runner


@pytest.fixture
def startup(tmp_path, monkeypatch):
    pipe, tick = make_pipe(tmp_path)
    pipe.jev.model = 'jev-1.13.0'

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tick[0].astimezone(tz) if tz else tick[0].replace(tzinfo=None)

    monkeypatch.setattr(runner, 'datetime', Clock)
    return pipe, tick


def test_closed_session_blocks_before_data_or_paid_probe(startup, monkeypatch, capsys):
    pipe, tick = startup
    tick[0] = NOW.replace(hour=22)
    monkeypatch.setattr(runner, '_load', lambda: pipe.cfg)
    monkeypatch.setattr('trademind.pipeline.Pipeline', lambda cfg: pipe)
    probe = Mock()
    monkeypatch.setattr(runner, '_probe_fast_models', probe)
    pipe.ensure_account = Mock()
    assert runner.cmd_t0_paper(I, 1, True) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'blocked'
    assert 'not_continuous_session' in report['preflight']['blockers']
    assert report['model_calls'] == report['orders'] == 0
    probe.assert_not_called()
    pipe.router.get_minute_bars.assert_not_called()
    pipe.ensure_account.assert_not_called()


@pytest.mark.parametrize('bad_rate', [None, Decimal('0'), Decimal('NaN')])
def test_invalid_prices_block_free_preflight(startup, bad_rate):
    pipe, _ = startup
    pipe.cfg.t0.model_input_cny_per_million = bad_rate
    assert not runner._fast_paper_preflight(pipe, [I], True)['ok']
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()


@pytest.mark.parametrize('cause', ['stale', 'gap', 'unavailable'])
def test_bad_market_facts_cannot_enable_probe(startup, cause):
    pipe, _ = startup
    if cause == 'unavailable':
        pipe.router.get_minute_bars = Mock(side_effect=OSError('transport unavailable'))
    else:
        end = NOW-timedelta(minutes=5) if cause == 'stale' else NOW
        offsets = range(20) if cause == 'stale' else [0, 1, *range(3, 21)]
        pipe.router.get_minute_bars = Mock(return_value=(
            [minute(end-timedelta(minutes=i)) for i in reversed(offsets)], None))
    report = runner._fast_paper_preflight(pipe, [I], True)
    assert not report['ok']
    assert 'fresh_complete_minute_data_unverified' in report['blockers']
    assert not report['data'][0]['ok']
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()


@pytest.mark.parametrize('constraint', ['probe_count', 'total_count', 'cost'])
def test_pair_shortage_sends_neither_probe_and_preserves_budget(startup, constraint):
    pipe, _ = startup
    state = pipe.model_budget.snapshot()
    if constraint == 'probe_count':
        state['probe'] = pipe.cfg.t0.probe_request_limit-1
    elif constraint == 'total_count':
        state['used'] = pipe.cfg.t0.daily_decision_call_budget-1
    else:
        state['cost_reserved_cny'] = str(pipe.cfg.t0.daily_model_cost_limit_cny-Decimal('0.000001'))
    pipe.repo.save_coalesce(pipe.model_budget.key, NOW, state)
    before = pipe.model_budget.snapshot()
    report = runner._probe_fast_models(pipe)
    assert not report['ok']
    assert pipe.model_budget.snapshot() == before
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()
    assert not pipe.llm.capability_verified and not pipe.jev.capability_verified


def test_valid_free_preflight_is_read_only_and_does_not_verify_models(startup):
    pipe, _ = startup
    pipe.llm.capability_verified = pipe.jev.capability_verified = False
    before = pipe.model_budget.snapshot()
    report = runner._fast_paper_preflight(pipe, [I], True)
    assert report['ok'] and report['data'][0]['ok']
    assert report['data'][0]['feature_version'] == 'minute-feat-2026-10-09'
    assert pipe.model_budget.snapshot() == before
    assert not pipe.llm.capability_verified and not pipe.jev.capability_verified
    pipe.llm.complete.assert_not_called()


def test_pair_probe_single_attempt_and_restores_flash_settings(startup):
    pipe, _ = startup

    # A minimal object is enough here; transport/usage is covered by provider tests.
    from types import SimpleNamespace
    def complete_reserve(**kw):
        assert pipe.llm.max_repair_attempts == 0
        pipe.model_budget.reserve_request(1200, pipe.cfg.t0.model_max_output_tokens, role=kw['role'])
        return SimpleNamespace(parsed={'ok': True})
    pipe.llm.complete = Mock(side_effect=complete_reserve)
    pipe.jev.session.post = Mock(return_value=Response({
        'model': 'jev-1.13.0', 'answers': {'thesis_supported': {'type': 'noul', 'noul': .95}},
        'usage': {'input_tokens': 100, 'output_tokens': 20}}))
    original = pipe.llm.max_repair_attempts
    report = runner._probe_fast_models(pipe)
    assert report['ok']
    assert pipe.model_budget.snapshot()['probe'] == 2
    assert pipe.llm.max_repair_attempts == original
    pipe.llm.complete.assert_called_once()
    pipe.jev.session.post.assert_called_once()


def test_missing_features_block_worker_but_keep_risk_exit(startup):
    pipe, tick = startup
    pipe.router.get_minute_bars = Mock(return_value=(
        [minute(NOW-timedelta(minutes=i)) for i in [3, 1, 0]], None))
    before = pipe.model_budget.snapshot()
    result = pipe.run_decision(I, NOW)
    assert result.guard_verdict == 'WAIT_DATA_QUALITY'
    assert not result.order_id
    assert pipe.model_budget.snapshot() == before
    pipe.llm.complete.assert_not_called()

    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: (
        [minute(tick[0]-timedelta(minutes=i)) for i in reversed(range(20))], None))
    assert pipe.run_decision(I, NOW).order_id
    tick[0] += timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: (
        [minute(tick[0]-timedelta(minutes=i), '1.45') for i in [3, 1, 0]], None))
    before = pipe.model_budget.snapshot()
    result = pipe.run_decision(I, tick[0])
    assert result.order_id and result.portfolio_intent.action.value == 'SELL'
    assert pipe.model_budget.snapshot() == before
