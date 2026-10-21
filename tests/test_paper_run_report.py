import json
from datetime import timedelta
from decimal import Decimal as D
from threading import Event
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from unittest.mock import Mock

import pytest

from test_fast_decision import make_pipe, I, NOW
from trademind.domain.enums import ConfidenceSemantics, TradeAction
from trademind.domain.intents import TradeIntent
from trademind.execution.paper_run import PaperRun, public_call
from trademind.errors import ProviderError
from trademind import runner


def _drain_intent(pipe, action=TradeAction.WAIT, valid_until=None, version=None, target_weight=D('0')):
    return TradeIntent(
        id='ti-drain-test', trace_id='tr-drain-test', created_at=NOW, decision_id='dec-drain-test',
        context_id='ctx-drain-test', thesis_id='plan-drain-test', instrument_id=I,
        portfolio_version=pipe.ledger.snapshot(NOW).version if version is None else version,
        account_id=pipe.cfg.account_id, action=action, target_weight=target_weight,
        current_weight=D('0'), confidence=D('0'),
        confidence_semantics=ConfidenceSemantics.ANSWER_DISTRIBUTION_CONFIDENCE,
        holding_horizon_seconds=300 if action != TradeAction.WAIT else 0, evidence_ids=[],
        reason='drain test', exit_conditions=[],
        valid_until=valid_until or NOW + timedelta(minutes=3), model='', prompt_id='test')


def _drain_audits(pipe):
    return [json.loads(r['payload_json']) for r in pipe.db.query(
        "SELECT payload_json FROM control_audit WHERE action='model_drain'")]


def test_report_is_whitelisted_atomic_and_does_not_export_credentials(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.cfg.llm.api_key = pipe.cfg.jev.api_key = pipe.cfg.api.token = 'DO_NOT_EXPORT'
    output = tmp_path/'run.json'
    run = PaperRun(pipe, [I], output)
    report = run.finish('blocked', 2, [], {'ok': False}, pipe.close_model_worker())
    raw = output.read_text(encoding='utf-8')
    assert 'DO_NOT_EXPORT' not in raw
    assert 'storage' not in report['runtime_config']
    assert json.loads(raw)['config_hash'] == report['config_hash']
    assert report['model_calls'] == report['orders'] == report['fills'] == 0
    assert report['account']['cash_invariants_ok']
    assert not list(tmp_path.glob('*.tmp'))
    assert 'error' not in public_call({'error': 'DO_NOT_EXPORT'})


def test_shutdown_blocks_followup_http_and_keeps_existing_reservation(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.model_budget.reserve_request(1000, 100, role='strategy')
    before = pipe.model_budget.snapshot()
    entered, release = Event(), Event()
    pipe.enable_async_models()
    def followup():
        entered.set()
        assert release.wait(2)
        pipe.model_budget.reserve_jev(1000)
        pytest.fail('closing worker must not reach followup transport')
    future = pipe._model_executor.submit(followup)
    pipe._model_pending = (future, Mock())
    assert entered.wait(1)
    try:
        shutdown = pipe.close_model_worker(timeout_s=.01)
        assert shutdown['worker_active'] and shutdown['unconsumed_result']
        assert pipe.model_budget.snapshot() == before
    finally:
        release.set()
        with pytest.raises(ProviderError): future.result(timeout=2)
        pipe._model_executor.shutdown(wait=True)
    assert pipe.model_budget.snapshot() == before


def test_finished_unconsumed_intent_is_not_executed_at_shutdown(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    future = pipe._model_executor.submit(lambda: 'discarded intent')
    future.result(timeout=1)
    pipe._model_pending = (future, Mock())
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert shutdown['unconsumed_result'] and not shutdown['worker_active']
    # An unclassifiable result keeps the halt instead of being silently dropped.
    assert shutdown['drain_terminal_state'] == 'unknown_result_shape'
    assert not pipe.repo.open_orders()
    assert _drain_audits(pipe)[-1]['terminal_state'] == 'unknown_result_shape'


def test_drained_wait_result_is_classified_without_lock(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    future = pipe._model_executor.submit(lambda: (_drain_intent(pipe), {}))
    future.result(timeout=1)
    pipe._model_pending = (future, Mock(instrument_id=I))
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert not shutdown['unconsumed_result'] and not shutdown['worker_active']
    assert shutdown['new_requests_closed']
    assert shutdown['drain_terminal_state'] == 'valid_result_not_executed'
    assert shutdown['drain_detail']['action'] == 'WAIT'
    assert not pipe.repo.open_orders()
    assert _drain_audits(pipe)[-1]['terminal_state'] == 'valid_result_not_executed'


def test_drained_expired_result_is_discarded_with_terminal_state(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    intent = _drain_intent(pipe, valid_until=NOW - timedelta(seconds=1))
    future = pipe._model_executor.submit(lambda: (intent, {}))
    future.result(timeout=1)
    pipe._model_pending = (future, Mock(instrument_id=I))
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert not shutdown['unconsumed_result'] and not shutdown['worker_active']
    assert shutdown['drain_terminal_state'] == 'expired_result_not_executed'
    assert shutdown['drain_detail']['reason'] == 'expired_or_account_changed'
    assert not pipe.repo.open_orders()
    assert _drain_audits(pipe)[-1]['terminal_state'] == 'expired_result_not_executed'


def test_drained_account_version_change_marks_result_expired(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    stale = pipe.ledger.snapshot(NOW).version + 1
    intent = _drain_intent(pipe, action=TradeAction.BUY, version=stale)
    future = pipe._model_executor.submit(lambda: (intent, {}))
    future.result(timeout=1)
    pipe._model_pending = (future, Mock(instrument_id=I))
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert not shutdown['unconsumed_result']
    assert shutdown['drain_terminal_state'] == 'expired_result_not_executed'
    assert not pipe.repo.open_orders()


def test_drained_actionable_result_is_never_executed(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    cash_before = pipe.ledger.snapshot(NOW).cash
    intent = _drain_intent(pipe, action=TradeAction.BUY, target_weight=D('0.5'))
    future = pipe._model_executor.submit(lambda: (intent, {}))
    future.result(timeout=1)
    pipe._model_pending = (future, Mock(instrument_id=I))
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert not shutdown['unconsumed_result'] and not shutdown['worker_active']
    assert shutdown['drain_terminal_state'] == 'valid_result_not_executed'
    assert not pipe.repo.open_orders()
    assert pipe.ledger.snapshot(NOW).cash == cash_before


def test_drained_model_error_is_classified_without_lock(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    def boom():
        raise RuntimeError('transport exploded')
    future = pipe._model_executor.submit(boom)
    futures_wait([future], timeout=1)
    pipe._model_pending = (future, Mock(instrument_id=I))
    shutdown = pipe.close_model_worker(timeout_s=.01)
    assert not shutdown['unconsumed_result'] and not shutdown['worker_active']
    assert shutdown['drain_terminal_state'] == 'model_error'
    assert shutdown['drain_detail']['error_type'] == 'RuntimeError'
    assert _drain_audits(pipe)[-1]['terminal_state'] == 'model_error'


def test_cancelled_before_start_does_not_lock(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.enable_async_models()
    release, ran = Event(), []
    try:
        pipe._model_executor.submit(lambda: release.wait(2))  # occupies the single worker
        queued = pipe._model_executor.submit(lambda: ran.append(1))  # still queued
        pipe._model_pending = (queued, Mock(instrument_id=I))
        shutdown = pipe.close_model_worker(timeout_s=.01)
        assert not shutdown['unconsumed_result'] and not shutdown['worker_active']
        assert shutdown['drain_terminal_state'] == 'cancelled_before_start'
        assert not ran  # the cancelled future never produced a model call
    finally:
        release.set()
        pipe._model_executor.shutdown(wait=True)


def test_loop_deadline_prevents_decision_and_order(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    pipe.run_decision = Mock()
    result, expired = runner._paper_steps(pipe, pipe.cfg, [I], 1, deadline=0)
    assert expired and result == []
    pipe.run_decision.assert_not_called()


def test_run_report_exports_persisted_context_and_plan_references(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    run = PaperRun(pipe, [I], tmp_path/'decision-run.json')
    decision = pipe.run_decision(I, NOW)
    report = run.finish('unverified', 0, [{'decision': runner._public_decision(decision)}],
                        {'ok': True}, pipe.close_model_worker())
    assert report['context_records'][0]['context_id'] == decision.context_id
    assert report['context_records'][0]['feature_version']
    assert report['plan_references']
    assert report['trading_acceptance'] == 'not_evaluated'


def test_report_failure_returns_failure_and_closes_worker(tmp_path, monkeypatch, capsys):
    pipe, _ = make_pipe(tmp_path)
    monkeypatch.setattr(runner, '_load', lambda: pipe.cfg)
    monkeypatch.setattr('trademind.pipeline.Pipeline', lambda cfg: pipe)
    monkeypatch.setattr(runner, '_fast_paper_preflight', lambda *a: {'ok': False})
    monkeypatch.setattr('trademind.execution.paper_run.atomic_json', Mock(side_effect=OSError('DO_NOT_EXPORT')))
    assert runner.cmd_t0_paper(I, 1) == 1
    out = capsys.readouterr().out
    assert json.loads(out)['reason'] == 'run_report_write_failed'
    assert 'DO_NOT_EXPORT' not in out

