import json
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest

from test_fast_decision import make_pipe, minute, I, NOW
from test_fast_comparison import contexts
from trademind.evaluation import fast_archives as archives
from trademind.evaluation.fast_comparison import compare_bundle_file, compare_archives, facts_hash
from trademind.pipeline import Pipeline


def test_closed_collection_never_loads_credentials_or_constructs_pipeline(tmp_path, monkeypatch):
    source_pipe, _ = make_pipe(tmp_path/'source')
    loader = Mock(return_value=source_pipe.cfg)
    monkeypatch.setattr(archives, 'load_config', loader)
    monkeypatch.setattr(archives.TradingCalendar, 'load', lambda: Mock(is_session_open=Mock(return_value=False)))
    constructor = Mock(side_effect=AssertionError('no model providers before free gates'))
    monkeypatch.setattr(archives, 'Pipeline', constructor)
    output = tmp_path/'empty.json'
    bundle = archives.collect('config.example', output, probe_models=True)
    assert bundle['current_model_calls'] == 0 and bundle['contexts'] == []
    loader.assert_called_once_with('config.example', load_credentials=False)
    constructor.assert_not_called()
    report = compare_bundle_file(output)
    assert report['status'] == 'incomplete_archives' and report['current_model_calls'] == 0


def test_archive_destination_is_not_overwritten(tmp_path):
    output = tmp_path/'existing.json'
    output.write_text('keep', encoding='utf-8')
    with pytest.raises(ValueError, match='already exists'):
        archives.collect('unused', output)
    assert output.read_text(encoding='utf-8') == 'keep'


def test_shared_market_inputs_keep_two_actual_accounts_separate(tmp_path):
    source, _ = make_pipe(tmp_path/'source')
    observations = {I: ([minute(NOW-timedelta(minutes=i)) for i in reversed(range(20))], None)}
    frozen = archives.FrozenMinutes(observations)
    common = source.context_builder.build_minute(I, NOW)
    cfg_before = source.cfg.account_id
    accounts, contexts_by_group = [], []
    for label in ('old', 'new'):
        cfg = archives.group_config(source.cfg, 'test-run', label, tmp_path)
        pipe = Pipeline(cfg, clock=lambda: NOW)
        pipe.ensure_account(at=NOW)
        archives.bind_shared_context(pipe, frozen, {I: common})
        context = pipe.context_builder.build_minute(I, NOW)
        contexts_by_group.append(context)
        accounts.append(context.portfolio.account_id)
        assert context.portfolio.cash == 500
        assert pipe.model_budget.snapshot()['used'] == 0
        pipe.db.close()
    assert accounts == ['test-run-old', 'test-run-new']
    assert facts_hash(contexts_by_group[0]) == facts_hash(contexts_by_group[1]) == facts_hash(common)
    assert source.cfg.account_id == cfg_before
    assert source.cfg.storage.url not in [archives.group_config(source.cfg, 'test-run', k, tmp_path).storage.url
                                         for k in ('old', 'new')]


def test_real_intent_export_keeps_account_time_plan_and_public_call_fields(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    result = pipe.run_decision(I, NOW)
    context = pipe.repo.get_context(result.context_id)
    from trademind.domain.context import ContextSnapshot
    context = ContextSnapshot.model_validate(context)
    row = archives.archive_result(pipe, context, result, NOW, NOW+timedelta(seconds=1), 0)
    assert row['facts_hash'] == facts_hash(context)
    assert row['account']['cash'] == '500'
    assert row['account_version'] == context.portfolio.portfolio_version
    assert row['strategy_plan']['model'] == 'deepseek-flash'
    assert row['available_at'] == (NOW+timedelta(seconds=1)).isoformat()
    assert all('model_params' not in call and 'error' not in call for call in row['model_calls'])
    assert 'api_key' not in json.dumps(row)


def test_missing_archive_cannot_pass_even_if_quality_short_circuits(tmp_path):
    samples = contexts(tmp_path)
    for context in samples:
        context.features.missing_fields['ret_15m'] = 'warmup missing'
    with patch('requests.Session.request', side_effect=AssertionError('no current HTTP in replay')):
        report = compare_archives(samples, {})
    assert report['status'] == 'incomplete_archives'
    assert report['accounts']['new']['archive_gaps']


def test_unsupported_bundle_version_rejected(tmp_path):
    output = tmp_path/'bundle.json'
    output.write_text(json.dumps({'schema_version': 999, 'contexts': []}), encoding='utf-8')
    with pytest.raises(ValueError, match='schema'):
        compare_bundle_file(output)


def test_sanitize_evidence_redacts_credentials_and_bounds_length():
    from trademind.execution.paper_run import sanitize_evidence
    raw = 'Error: Bearer my-secret-token at https://api.openai.com/v1/chat?api_key=sk-1234567890abcdef. DO_NOT_EXPORT'
    sanitized = sanitize_evidence(raw)
    assert 'my-secret-token' not in sanitized
    assert 'sk-1234567890abcdef' not in sanitized
    assert 'DO_NOT_EXPORT' not in sanitized
    assert '[REDACTED]' in sanitized
    long_text = 'A' * 300
    assert len(sanitize_evidence(long_text)) <= 165
    assert sanitize_evidence(None) is None


def test_classify_failure_categories():
    assert archives.classify_failure('JSONDecodeError: invalid format', None, None) == ('model_call', 'format_error')
    assert archives.classify_failure('TimeoutError: timed out', None, None) == ('model_call', 'timeout')
    assert archives.classify_failure('ConnectionRefusedError: network down', None, None) == ('model_call', 'network_error')
    assert archives.classify_failure(None, 'VETO', 'BUY') == ('guard', 'guard_veto')
    assert archives.classify_failure(None, 'HOLD_NO_TRADE', 'WAIT') == ('strategy', 'conservative_wait')
    assert archives.classify_failure(None, None, 'BUY') == ('none', 'none')


# -- TASK-20261013-07: configurable initial cash (ISSUE-20261012-04) -------------

def _paper_cfg(cash):
    from decimal import Decimal
    from trademind.config import AppConfig
    cfg = AppConfig()
    cfg.t0.enabled = True
    cfg.mode = 'paper'
    cfg.t0.initial_cash = Decimal(cash)
    return cfg


def test_collect_accepts_configured_cash_and_records_source(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, 'load_config', Mock(return_value=_paper_cfg('1000')))
    monkeypatch.setattr(archives.TradingCalendar, 'load',
                        lambda: Mock(is_session_open=Mock(return_value=False)))
    bundle = archives.collect('cfg', tmp_path/'a.json')
    assert bundle['initial_cash'] == '1000'
    assert bundle['initial_cash_source'] == 'config.t0.initial_cash'
    assert bundle['status'] == 'incomplete_archives'  # closed session: gates passed, no accounts


def test_collect_rejects_non_positive_cash(tmp_path, monkeypatch):
    from decimal import Decimal
    monkeypatch.setattr(archives.TradingCalendar, 'load',
                        lambda: Mock(is_session_open=Mock(return_value=False)))
    for bad in (Decimal('0'), Decimal('-5'), Decimal('NaN'), Decimal('Infinity')):
        monkeypatch.setattr(archives, 'load_config', Mock(return_value=_paper_cfg(bad)))
        with pytest.raises(ValueError, match='positive'):
            archives.collect('cfg', tmp_path/'b.json')


def test_prompt_paper_account_uses_configured_cash(tmp_path):
    from decimal import Decimal
    from test_dual_model_pipeline import MockLLM, MockJEV
    from trademind.evaluation.prompt_paper import PromptPaperAccount
    pipe, _ = make_pipe(tmp_path/'src')
    ctx = pipe.context_builder.build_minute(I, NOW)
    acct = PromptPaperAccount(tmp_path/'p.db', 'pp-1000', [ctx], [minute(NOW)],
                              MockLLM([]), MockJEV(), 'v', initial_cash='1000')
    try:
        report = acct.report()
    finally:
        acct.close()
    assert report['initial_cash'] == '1000' and report['equity'] == '1000'
    assert report['net_pnl'] == str(Decimal('0'))
    assert report['initial_cash_source'] == 'explicit_parameter'
    with pytest.raises(ValueError, match='positive finite'):
        PromptPaperAccount(tmp_path/'bad.db', 'pp-bad', [ctx], [minute(NOW)],
                           MockLLM([]), MockJEV(), 'v', initial_cash='0')


def test_audit_archive_failures_schema_v2_and_backward_compatibility():
    # Schema 1 bundle with missing failure fields
    v1_bundle = {
        'schema_version': 1,
        'run_id': 'v1-test',
        'status': 'incomplete_archives',
        'contexts': [{'id': 'c1'}],
        'archives': {
            'old': {'c1': {'action': 'WAIT', 'guard': 'HOLD_NO_TRADE'}},
            'new': {},
        },
        'gaps': [],
    }
    audit1 = archives.audit_archive_failures(v1_bundle)
    assert audit1['schema_version'] == 1
    assert audit1['unknown_field_count'] > 0
    assert audit1['audited_records'][0]['failure_category'] == 'unknown'
    assert audit1['audit_verdict'] == 'incomplete_archives'

    # Schema 2 bundle with explicit failure fields
    v2_bundle = {
        'schema_version': 2,
        'run_id': 'v2-test',
        'status': 'collected',
        'contexts': [{'id': 'c1'}],
        'archives': {
            'old': {'c1': {
                'action': 'WAIT',
                'guard': 'HOLD_NO_TRADE',
                'failure_stage': 'strategy',
                'failure_category': 'conservative_wait',
                'model_suggestion': 'WAIT',
                'program_verdict': 'HOLD_NO_TRADE',
                'completion_status': 'complete',
                'sanitized_evidence': 'No setup',
            }},
            'new': {},
        },
        'failure_audits': [{
            'context_id': 'c2',
            'group': 'new',
            'failure_stage': 'model_call',
            'failure_category': 'format_error',
            'model_suggestion': None,
            'program_verdict': 'ERROR',
            'completion_status': 'failed',
            'sanitized_evidence': 'Invalid JSON',
        }],
        'gaps': [],
    }
    audit2 = archives.audit_archive_failures(v2_bundle)
    assert audit2['schema_version'] == 2
    assert audit2['unknown_field_count'] == 0
    assert 'conservative_wait' in audit2['observed_failure_categories']
    assert 'format_error' in audit2['observed_failure_categories']
    assert 'timeout' in audit2['unobserved_failure_categories']
    assert audit2['audit_verdict'] == 'pass'
