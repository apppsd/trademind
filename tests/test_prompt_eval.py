import json
from copy import deepcopy
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import requests

from trademind.agents.fast_prompts import strategy_examples
from trademind.evaluation.prompt_eval import evaluate, fingerprint, compare_reports, write_report, validate_dataset


def dataset():
    return json.loads((Path(__file__).parent/'fixtures/prompt_eval/cases.json').read_text(encoding='utf-8'))


def bundle(data):
    records = []
    for case in data['cases']:
        action = case['expected']['allowed_actions'][0]
        output = strategy_examples()[0]['output'] if case['stage'] == 'strategy' else {
            'answers': {'action': {'choice': action, 'confidence': '0.95'},
                        'evidence_sufficient': {'noul': '0.95'},
                        'plan_applicable': {'noul': '0.95'},
                        'entry_suitable': {'noul': '0.95' if action == 'BUY' else '0.01'}}}
        records.append({'case_id': case['case_id'], 'input_hash': fingerprint(case['input']),
                        'status': 'ok', 'output': output, 'latency_ms': 42,
                        'cost': {'kind': 'estimated', 'amount': '0.001', 'currency': 'CNY'},
                        'usage': {'input_tokens': 100, 'output_tokens': 10},
                        'program': {'admitted_action': case['expected']['guard_actions'][0],
                                    'orders_created': int(action in ('BUY', 'SELL'))}})
    return {'schema_version': 1, 'dataset_hash': fingerprint(data), 'source': 'synthetic',
            'prompt_versions': {'strategy': 'strategy-3', 'fast': 'fast-2'},
            'model_versions': {'strategy': 'deepseek-flash', 'fast': 'jev-1.13.0'},
            'config_version': 'synthetic-policy-1', 'records': records}


def test_saved_outputs_are_network_free_and_complete(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    data = dataset()
    source = bundle(data)
    before = deepcopy(source)
    report = evaluate(data, source)
    assert source == before
    assert report['status'] == 'pass'
    assert report['total_attempts'] == 24
    assert report['metrics']['buy_coverage']['value'] == 1
    assert report['metrics']['exit_coverage']['value'] == 1
    assert report['latency']['p95'] == 42
    path = tmp_path/'report.json'
    write_report(path, report)
    assert json.loads(path.read_text()) == report
    assert path.with_suffix('.md').exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_all_wait_cannot_pass_positive_cases():
    data = dataset()
    source = bundle(data)
    for row in source['records']:
        if 'answers' in row['output']:
            row['output']['answers']['action']['choice'] = 'WAIT'
    report = evaluate(data, source)
    assert report['status'] == 'fail'
    assert report['metrics']['buy_coverage']['value'] == 0
    assert report['metrics']['exit_coverage']['value'] == 0


def test_dangerous_suggestion_and_guard_failure_are_separate():
    data = dataset()
    source = bundle(data)
    row = next(r for r in source['records'] if r['case_id'] == 'no_position_1')
    row['output']['answers']['action']['choice'] = 'SELL'
    report = evaluate(data, source)
    assert report['status'] == 'fail'
    assert report['metrics']['dangerous_suggestions']['numerator'] == 1
    assert report['metrics']['guard_failures']['numerator'] == 0
    row['program']['admitted_action'] = 'SELL'
    row['program']['orders_created'] = 1
    report = evaluate(data, source)
    assert report['metrics']['guard_failures']['numerator'] == 1
    assert report['metrics']['order_failures']['numerator'] == 1


def test_missing_failed_and_malformed_responses_have_distinct_counts():
    data = dataset()
    source = bundle(data)
    source['records'].pop()
    source['records'][0]['status'] = 'error'
    source['records'][1]['output'] = '{bad'
    report = evaluate(data, source)
    assert report['status'] == 'fail'
    assert report['missing_responses'] == report['request_errors'] == 1
    assert report['metrics']['contract_failures']['numerator'] == 1
    assert report['total_attempts'] == 23


def test_unknown_fee_latency_usage_and_program_are_not_zero():
    data = dataset()
    source = bundle(data)
    row = source['records'][0]
    for key in ('cost', 'latency_ms', 'usage', 'program'):
        row.pop(key)
    report = evaluate(data, source)
    assert report['status'] == 'incomplete'
    assert report['cost']['complete_total'] is None
    assert report['cost']['unknown_attempts'] == 1
    assert report['latency']['samples'] == 23
    assert report['usage']['unknown_attempts'] == 1


@pytest.mark.parametrize('change', ['dataset_hash', 'input_hash', 'duplicate', 'unknown_case', 'schema', 'identifier'])
def test_mismatched_or_unsupported_bundles_rejected(change):
    data = dataset()
    source = bundle(data)
    if change == 'dataset_hash': source['dataset_hash'] = 'wrong'
    if change == 'input_hash': source['records'][0]['input_hash'] = 'wrong'
    if change == 'duplicate': source['records'].append(source['records'][0])
    if change == 'unknown_case': source['records'][0]['case_id'] = 'unknown'
    if change == 'schema': source['schema_version'] = 99
    if change == 'identifier': source['config_version'] = 'Bearer YOUR_TOKEN_HERE'
    with pytest.raises(ValueError):
        evaluate(data, source)


@pytest.mark.parametrize('problem', ['out_of_range', 'low_entry', 'missing_question', 'bad_order_count'])
def test_typed_answers_and_program_counts_are_checked(problem):
    data = dataset()
    source = bundle(data)
    row = next(r for r in source['records'] if r['case_id'] == 'entry_1')
    if problem == 'out_of_range': row['output']['answers']['action']['confidence'] = 'NaN'
    if problem == 'low_entry': row['output']['answers']['entry_suitable']['noul'] = '0.01'
    if problem == 'missing_question': del row['output']['answers']['plan_applicable']
    if problem == 'bad_order_count': row['program']['orders_created'] = True
    report = evaluate(data, source)
    assert report['status'] != 'pass'


def test_duplicate_execution_not_hidden_by_valid_model_output():
    data = dataset()
    source = bundle(data)
    row = next(r for r in source['records'] if r['case_id'] == 'duplicate_1')
    row['program']['orders_created'] = 2
    report = evaluate(data, source)
    assert report['metrics']['order_failures']['numerator'] == 1


def test_comparison_requires_identical_dataset_models_and_config():
    data = dataset()
    first = evaluate(data, bundle(data))
    source = bundle(data)
    source['prompt_versions']['strategy'] = 'strategy-candidate'
    second = evaluate(data, source)
    assert compare_reports(first, second)['status'] == 'compared'
    second['model_versions']['fast'] = 'different-model'
    with pytest.raises(ValueError):
        compare_reports(first, second)


def test_empty_responses_and_invalid_labels_are_not_success():
    data = dataset()
    source = bundle(data)
    source['records'] = []
    report = evaluate(data, source)
    assert report['status'] == 'incomplete'
    assert report['metrics']['contract_failures']['value'] is None
    assert report['metrics']['buy_coverage']['value'] == 0
    data['cases'][0]['expected']['forbidden_actions'] = ['WAIT']
    with pytest.raises(ValueError):
        validate_dataset(data)


def test_cli_incomplete_exit_and_input_protection(tmp_path):
    data = dataset()
    source = bundle(data)
    source['records'] = []
    cases = tmp_path/'cases.json'
    responses = tmp_path/'responses.json'
    cases.write_text(json.dumps(data), encoding='utf-8')
    responses.write_text(json.dumps(source), encoding='utf-8')
    command = [sys.executable, 'scripts/evaluate_prompt_cases.py', '--cases', str(cases),
               '--responses', str(responses), '--output', str(tmp_path/'report.json')]
    # src layout: the child interpreter needs the repo's src directory on its path.
    env = {**__import__('os').environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    assert run.returncode == 2 and json.loads(run.stdout)['status'] == 'incomplete'
    before = cases.read_bytes()
    command[-1] = str(cases)
    run = subprocess.run(command, capture_output=True, text=True, env=env)
    assert run.returncode == 1 and cases.read_bytes() == before
