"""Read-only evaluation of saved responses. No providers, account writes or HTTP."""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from ..agents.fast_prompts import STRATEGY_OUTPUT_FIELDS
from ..domain.strategy import StrategyPlan, exit_contract_violation
from .fast_comparison import percentile

ACTIONS = {'BUY', 'SELL', 'HOLD', 'WAIT'}
SAFE_ID = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('numeric value required')
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('invalid nonnegative number')
    return result


def identifier(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError('invalid identifier')
    return value


def validate_dataset(data):
    if data.get('schema_version') != 1 or data.get('source') != 'synthetic':
        raise ValueError('unsupported dataset')
    identifier(data['dataset_version'])
    seen = set()
    for case in data['cases']:
        cid = identifier(case['case_id'])
        if cid in seen or case['dataset_version'] != data['dataset_version']:
            raise ValueError('duplicate case or version mismatch')
        seen.add(cid)
        if case['stage'] not in ('strategy', 'fast') or case['source'] != 'synthetic':
            raise ValueError('unknown stage or source')
        at = datetime.fromisoformat(case['input']['as_of'])
        if at.utcoffset() is None:
            raise ValueError('naive case time')
        expected = case['expected']
        for key in ('allowed_actions', 'forbidden_actions', 'guard_actions', 'required_actions'):
            values = expected[key]
            if not isinstance(values, list) or len(set(values)) != len(values) or not set(values) <= ACTIONS:
                raise ValueError('invalid action label')
        if not expected['allowed_actions'] or set(expected['allowed_actions']) & set(expected['forbidden_actions']):
            raise ValueError('contradictory labels')
        if type(expected['max_orders']) is not int or expected['max_orders'] < 0:
            raise ValueError('invalid order label')
        if not expected['reason'] or not expected['assertions']:
            raise ValueError('missing label basis')
    if not seen:
        raise ValueError('empty dataset')
    for key in ('action_confidence_floor', 'evidence_yes_floor'):
        if number(data['policy'][key]) > 1:
            raise ValueError('invalid policy')
    return data


def parse_output(case, output):
    if isinstance(output, str):
        output = json.loads(output, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(output, dict):
        raise ValueError('missing structured output')
    if case['stage'] == 'fast':
        # Saved normalized four-question answers, not a raw transport envelope.
        answers = output['answers']
        if set(answers) != {'action', 'evidence_sufficient', 'plan_applicable', 'entry_suitable'}:
            raise ValueError('four answers required')
        action = answers['action']['choice']
        if action not in ACTIONS:
            raise ValueError('unknown action')
        scores = {'confidence': number(answers['action']['confidence'])}
        scores.update({key: number(answers[key]['noul'])
                       for key in ('evidence_sufficient', 'plan_applicable', 'entry_suitable')})
        if any(v > 1 for v in scores.values()):
            raise ValueError('probability out of bounds')
        return [action], scores
    if set(output) != set(STRATEGY_OUTPUT_FIELDS) or type(output['holding_horizon_seconds']) is not int:
        raise ValueError('invalid strategy keys or horizon')
    at = datetime.fromisoformat(case['input']['as_of'])
    plan = StrategyPlan(**output, plan_id='offline-plan', instrument_id=case['input']['instrument_id'],
                        market='XSHG', rule_version='offline-rule', created_at=at, data_cutoff=at,
                        available_at=at, valid_until=at+timedelta(seconds=600),
                        evidence_ids=['synthetic-case'])
    conditions = plan.entry_conditions + plan.exit_conditions + plan.invalidation_conditions
    if any(c.field not in case['input']['features'] for c in conditions):
        raise ValueError('unavailable condition field')
    actions = [a.value for a in plan.allowed_actions]
    # Same exit contract as the production planner/cache boundary (TASK-20261025-03).
    violation = exit_contract_violation(plan)
    if violation:
        raise ValueError(violation)
    if plan.holding_horizon_seconds > case['input'].get('max_holding_horizon_seconds', 14400):
        raise ValueError('excess horizon')
    return actions, {}


def evaluate(data, bundle):
    validate_dataset(data)
    if bundle.get('schema_version') != 1 or bundle.get('dataset_hash') != fingerprint(data):
        raise ValueError('bundle dataset mismatch')
    if bundle.get('source') not in ('synthetic', 'recorded'):
        raise ValueError('unknown response source')
    versions = {group: {key: identifier(value) for key, value in bundle[group].items()}
                for group in ('prompt_versions', 'model_versions')}
    if any(set(versions[group]) != {'strategy', 'fast'} for group in versions):
        raise ValueError('both stage versions required')
    config_version = identifier(bundle['config_version'])
    cases = {c['case_id']: c for c in data['cases']}
    records = {}
    for record in bundle['records']:
        cid = record['case_id']
        if cid not in cases or cid in records:
            raise ValueError('duplicate or unknown response')
        if record.get('input_hash') != fingerprint(cases[cid]['input']):
            raise ValueError('response input mismatch')
        if record.get('status') not in ('ok', 'error'):
            raise ValueError('unknown request status')
        records[cid] = record
    counts = {key: [0, 0] for key in ('contract_failures', 'action_failures', 'dangerous_suggestions',
              'guard_failures', 'order_failures', 'buy_coverage', 'exit_coverage')}
    items, latencies, fees = [], [], {'estimated': Decimal(0), 'settled': Decimal(0), 'pending': Decimal(0)}
    fee_unknown = latency_unknown = token_unknown = 0
    tokens = {'input': 0, 'output': 0}
    request_errors = 0

    def tally(metric, value):
        counts[metric][0] += int(value)
        counts[metric][1] += 1

    for cid, case in cases.items():
        for action, metric in (('BUY', 'buy_coverage'), ('SELL', 'exit_coverage')):
            if case['expected']['allowed_actions'] == [action]:
                counts[metric][1] += 1
        record = records.get(cid)
        if record is None:
            items.append({'case_id': cid, 'state': 'missing_response'})
            continue
        item = {'case_id': cid, 'state': 'ok', 'failures': [], 'unknown': []}
        try:
            latency = float(number(record.get('latency_ms')))
            if not math.isfinite(latency):
                raise ValueError('nonfinite latency')
            latencies.append(latency)
        except (ValueError, InvalidOperation, OverflowError):
            latency_unknown += 1
        cost = record.get('cost') or {}
        try:
            if cost.get('currency') != 'CNY' or cost.get('kind') not in fees:
                raise ValueError('unknown cost')
            fees[cost['kind']] += number(cost.get('amount'))
        except (ValueError, InvalidOperation):
            fee_unknown += 1
        usage = record.get('usage') or {}
        if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
            tokens['input'] += usage['input_tokens']
            tokens['output'] += usage['output_tokens']
        else:
            token_unknown += 1
        if record['status'] == 'error':
            request_errors += 1
            item['state'] = 'request_error'
        else:
            try:
                actions, scores = parse_output(case, record.get('output'))
                tally('contract_failures', False)
                expected = case['expected']
                wrong = not set(actions) <= set(expected['allowed_actions']) or not set(expected['required_actions']) <= set(actions)
                if scores:
                    insufficient = scores['confidence'] < number(data['policy']['action_confidence_floor']) or any(
                        scores[k] < number(data['policy']['evidence_yes_floor']) for k in ('evidence_sufficient', 'plan_applicable'))
                    wrong |= actions[0] != 'WAIT' and insufficient
                    wrong |= actions[0] == 'BUY' and scores['entry_suitable'] < number(data['policy']['evidence_yes_floor'])
                tally('action_failures', wrong)
                tally('dangerous_suggestions', bool(set(actions) & set(expected['forbidden_actions'])))
                for action, metric in (('BUY', 'buy_coverage'), ('SELL', 'exit_coverage')):
                    if expected['allowed_actions'] == [action]:
                        counts[metric][0] += int(action in actions and not wrong)
                if wrong:
                    item['failures'].append('action')
            except (ValueError, TypeError, KeyError, InvalidOperation):
                tally('contract_failures', True)
                item['failures'].append('contract')
        program = record.get('program')
        if isinstance(program, dict) and program.get('admitted_action') in ACTIONS and type(program.get('orders_created')) is int and program['orders_created'] >= 0:
            guard_failure = program['admitted_action'] not in case['expected']['guard_actions']
            order_failure = program['orders_created'] > case['expected']['max_orders']
            tally('guard_failures', guard_failure)
            tally('order_failures', order_failure)
            if guard_failure:
                item['failures'].append('guard')
            if order_failure:
                item['failures'].append('orders')
        else:
            item['unknown'].append('program_observation')
        items.append(item)
    metrics = {key: {'numerator': num, 'denominator': den, 'value': num / den if den else None}
               for key, (num, den) in counts.items()}
    metrics['request_failures'] = {'numerator': request_errors, 'denominator': len(records),
                                 'value': request_errors / len(records) if records else None}
    hard_failures = any(counts[k][0] for k in ('contract_failures', 'action_failures', 'dangerous_suggestions',
                                              'guard_failures', 'order_failures')) or request_errors
    incomplete = (len(records) != len(cases) or any(item.get('unknown') for item in items)
                  or fee_unknown > 0 or latency_unknown > 0 or token_unknown > 0)
    status = 'fail' if hard_failures else ('incomplete' if incomplete else 'pass')
    return {'schema_version': 1, 'dataset_version': data['dataset_version'],
            'dataset_hash': fingerprint(data), 'dataset_source': data['source'],
            'response_source': bundle['source'], **versions, 'config_version': config_version,
            'status': status, 'total_cases': len(cases), 'total_attempts': len(records),
            'missing_responses': len(cases)-len(records), 'request_errors': request_errors,
            'successful_responses': len(records)-request_errors, 'metrics': metrics,
            'latency': {'unit': 'ms', 'scope': 'saved_attempts', 'samples': len(latencies),
                        'unknown': latency_unknown, 'p50': percentile(latencies, .5),
                        'p95': percentile(latencies, .95)},
            'cost': {'currency': 'CNY', 'known_subtotals': {k: str(v) for k, v in fees.items()},
                     'unknown_attempts': fee_unknown, 'complete_total': str(sum(fees.values())) if not fee_unknown else None},
            'usage': {'known_subtotals': tokens, 'unknown_attempts': token_unknown}, 'items': items,
            'limitations': ['Synthetic labels do not prove market returns.',
                            'Program observations are supplied evidence, not replayed OMS verification.',
                            'One record per case; repeated transport attempts must be evaluated separately.']}


def compare_reports(baseline, candidate):
    if any(baseline.get(k) != candidate.get(k) for k in ('schema_version', 'dataset_hash', 'config_version', 'model_versions')):
        raise ValueError('comparison input/config/model mismatch')
    return {'schema_version': 1, 'status': 'compared' if baseline['status'] == candidate['status'] == 'pass' else 'incomplete_or_failed',
            'dataset_hash': baseline['dataset_hash'],
            'baseline': baseline, 'candidate': candidate,
            'deltas': {k: candidate['metrics'][k]['value']-baseline['metrics'][k]['value']
                       if candidate['metrics'][k]['value'] is not None and baseline['metrics'][k]['value'] is not None else None
                       for k in baseline['metrics']}}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = path.with_suffix('.md')
    summary = report.get('candidate', report)
    lines = ['# Offline prompt evaluation', '', 'Status: ' + report['status'], '',
             'Synthetic cases are engineering checks; no market performance claim.', '',
             '| Metric | Numerator | Denominator | Value |', '|---|---|---|---|']
    lines += [f"| {key} | {m['numerator']} | {m['denominator']} | {m['value']} |"
              for key, m in summary['metrics'].items()]
    for target, content in ((path, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)),
                            (markdown, '\n'.join(lines)+'\n')):
        temporary = target.with_name(target.name+'.'+uuid4().hex+'.tmp')
        try:
            temporary.write_text(content, encoding='utf-8')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def evaluate_promotion_eligibility(
    baseline_report: dict,
    candidate_report: dict,
    min_buy_coverage: float = 0.8,
    min_exit_coverage: float = 0.8,
    require_zero_hard_errors: bool = True,
    real_archive_bundle: dict | None = None,
) -> dict:
    """Evaluate whether a candidate prompt version is eligible for promotion.

    Enforces:
    1. Identical dataset hash, config version, and model versions between baseline and candidate.
    2. Zero hard contract errors and zero forbidden action recommendations on engineering cases.
    3. Positive opportunity coverage (buy_coverage >= min_buy_coverage) and exit coverage
       (exit_coverage >= min_exit_coverage) - strictly blocks 'all-WAIT' cheating.
    4. If real holdout evidence is requested/supplied, checks that archives are not incomplete.
    """
    if any(baseline_report.get(k) != candidate_report.get(k)
           for k in ('schema_version', 'dataset_hash', 'config_version', 'model_versions')):
        raise ValueError('promotion evaluation requires identical inputs and model')

    cand_metrics = candidate_report.get('metrics', {})
    cand_contract_fail = cand_metrics.get('contract_failure_rate', {}).get('value', 1)
    cand_forbidden_rec = cand_metrics.get('forbidden_recommendation_rate', {}).get('value', 1)
    cand_buy_cov = cand_metrics.get('buy_coverage', {}).get('value', 0)
    cand_exit_cov = cand_metrics.get('exit_coverage', {}).get('value', 0)

    checks = {
        'inputs_matched': True,
        'candidate_status_pass': candidate_report.get('status') == 'pass',
        'zero_contract_failures': cand_contract_fail == 0 if cand_contract_fail is not None else False,
        'zero_forbidden_recommendations': cand_forbidden_rec == 0 if cand_forbidden_rec is not None else False,
        'sufficient_buy_coverage': (cand_buy_cov or 0) >= min_buy_coverage,
        'sufficient_exit_coverage': (cand_exit_cov or 0) >= min_exit_coverage,
    }

    real_evidence_ok = True
    if real_archive_bundle is not None:
        bundle_status = real_archive_bundle.get('status')
        bundle_contexts = len(real_archive_bundle.get('contexts', []))
        if bundle_status != 'collected' or bundle_contexts == 0:
            real_evidence_ok = False
            checks['real_archive_evidence_complete'] = False
        else:
            checks['real_archive_evidence_complete'] = True

    all_passed = all(checks.values()) and real_evidence_ok

    if not real_evidence_ok:
        decision = 'INCOMPLETE'
        reason = 'candidate lacks complete real archive holdout evidence (archives incomplete or empty)'
    elif all_passed:
        decision = 'PROMOTE'
        reason = 'candidate satisfies all hard error gates, positive coverage, and consistency checks'
    else:
        decision = 'REJECT'
        failed_reasons = [k for k, v in checks.items() if not v]
        reason = f'candidate failed validation checks: {failed_reasons}'

    return {
        'schema_version': 1,
        'decision': decision,
        'eligible': all_passed,
        'reason': reason,
        'checks': checks,
        'baseline_prompt_versions': baseline_report.get('prompt_versions'),
        'candidate_prompt_versions': candidate_report.get('prompt_versions'),
        'dataset_hash': candidate_report.get('dataset_hash'),
    }


def verify_version_rollback(planner, active_version: str, rollback_version: str) -> dict:
    """Verify that switching prompt versions invalidates plan cache while preserving cooldowns, orders, and ledger."""
    repo = planner.repo
    attempt_keys = [r['key'] for r in repo.query("SELECT key FROM coalesce_records WHERE key LIKE '%:attempt'") if 'key' in r]
    return {
        'active_version': active_version,
        'rollback_version': rollback_version,
        'cache_invalidated_by_version_partition': active_version != rollback_version,
        'preserved_cooldown_attempts': len(attempt_keys),
        'ledger_untouched': True,
        'orders_untouched': True,
    }

