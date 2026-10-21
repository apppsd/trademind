"""Prospective shared-market collection. No past responses are synthesized."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from ..config import load_config
from ..context.builder import ContextBuilder
from ..execution.paper_run import atomic_json, public_call, public_runtime_config, sanitize_evidence
from ..market.calendar import TradingCalendar
from ..market.router import DatasetRouter
from ..pipeline import Pipeline
from .fast_comparison import facts_hash


class FrozenMinutes:
    """One observation shared by both groups; never fetch behind their backs."""
    def __init__(self, observations):
        self.observations = observations

    def get_minute_bars(self, iid, start, end, period='1m',
                        as_of=None, quote_max_age_seconds=None):
        # The builder now forwards the live-routing acceptance policy; a frozen
        # archive is a closed snapshot whose visibility is already fixed by the
        # records below, so those kwargs are accepted and ignored here.
        if period != '1m' or iid not in self.observations:
            raise ValueError('shared observation unavailable')
        bars, quality = self.observations[iid]
        return [b for b in bars if start <= b.bar_time <= end and b.available_at <= end], quality


def group_config(cfg, run_id, label, directory):
    if label not in ('old', 'new'):
        raise ValueError('unknown archive group')
    group = deepcopy(cfg)
    group.t0.profile = 'jev_deepseek' if label == 'old' else 'jev_fast_decision'
    group.account_id = run_id+'-'+label
    group.storage.url = 'sqlite:///' + str(Path(directory)/run_id/(label+'.db'))
    return group


def classify_failure(
    error: str | None,
    guard_verdict: str | None,
    action: str | None,
    calls: list[dict] | None = None,
    reason: str | None = None,
):
    """Classify failure stage and category with explicit evidence semantics."""
    import re
    for call in calls or ():
        if not isinstance(call, dict):
            continue
        diag = call.get('response_diagnostics') if isinstance(call.get('response_diagnostics'), dict) else {}
        cat = call.get('failure_category') or diag.get('failure_category')
        status = call.get('status')
        finish_reason = call.get('finish_reason') or diag.get('finish_reason')
        http_status = call.get('http_status') if call.get('http_status') is not None else diag.get('http_status')
        if status in ('ok', 'repaired', 'stub') and cat in (None, 'none'):
            continue
        if diag.get('explicit_refusal') is True or finish_reason == 'content_filter' or cat == 'explicit_refusal':
            return 'model_call', 'explicit_refusal'
        if cat == 'model_refused':
            return 'model_call', 'model_refused'
        if cat == 'protocol_error' or diag.get('content_type') == 'missing':
            return 'model_call', 'protocol_error'
        if finish_reason == 'length' or cat == 'length_truncated':
            return 'model_call', 'length_truncated'
        if cat == 'reasoning_only' or (diag.get('content_empty') is True and diag.get('reasoning_present') is True):
            return 'model_call', 'reasoning_only'
        if cat == 'empty_output' or (diag.get('content_empty') is True and not diag.get('explicit_refusal')):
            return 'model_call', 'empty_output'
        if cat in ('invalid_json', 'schema_invalid', 'format_error'):
            return 'model_call', 'format_error'
        if cat == 'rate_limited' or http_status == 429:
            return 'model_call', 'rate_limited'
        if cat == 'http_error' or (isinstance(http_status, int) and http_status >= 400):
            return 'model_call', 'http_error'
        if cat == 'network_error':
            return 'model_call', 'network_error'
        if cat == 'timeout' or status == 'timeout':
            return 'model_call', 'timeout'
        if status in ('error', 'refused'):
            if not call.get('error') and not error and not reason:
                return 'model_call', 'unknown'

    candidate = error
    if not candidate and reason:
        low_reason = str(reason).lower()
        if any(k in low_reason for k in (
            'refusal', 'truncat', 'finish_reason=length', 'reasoning_content',
            'empty completion', 'invalid strategy', 'invalid json',
            'protocol structure', 'rate limit', 'model request http', 'timeout',
        )):
            candidate = reason

    if candidate:
        err = str(candidate).lower()
        if 'finish_reason=length' in err or 'length_truncated' in err or 'truncated at max_tokens' in err:
            return 'model_call', 'length_truncated'
        if 'reasoning_only' in err or 'reasoning_content only' in err:
            return 'model_call', 'reasoning_only'
        if 'empty completion' in err or 'empty_output' in err or 'empty content' in err:
            return 'model_call', 'empty_output'
        if 'explicit model refusal' in err or 'content filter refusal' in err or 'explicit_refusal' in err:
            return 'model_call', 'explicit_refusal'
        if 'providerrefused' in err or 'model_refused' in err or 'refusal' in err or ('refus' in err and 'connection' not in err):
            return 'model_call', 'model_refused'
        if 'protocol_error' in err or 'protocol structure error' in err or 'missing choices' in err or 'missing/invalid usage' in err or 'invalid response envelope' in err:
            return 'model_call', 'protocol_error'
        if 'rate limit' in err or 'rate_limited' in err or '429' in err:
            return 'model_call', 'rate_limited'
        if 'http_error' in err or re.search(r'\bhttp\s+[45]\d\d\b', err):
            return 'model_call', 'http_error'
        if 'connection' in err or 'network' in err or 'http' in err or 'proxy' in err:
            return 'model_call', 'network_error'
        if 'timeout' in err or 'timed out' in err:
            return 'model_call', 'timeout'
        if 'json' in err or 'format' in err or 'parse' in err or 'schema' in err or 'validation' in err or 'invalid strategy' in err:
            return 'model_call', 'format_error'
        if 'execution' in err or 'order' in err or 'broker' in err or 'oms' in err:
            return 'execution', 'execution_error'
        return 'unknown', 'unknown'
    if guard_verdict == 'VETO':
        return 'guard', 'guard_veto'
    if action == 'WAIT':
        return 'strategy', 'conservative_wait'
    return 'none', 'none'


def archive_result(pipe, context, result, started, completed, call_watermark):
    """Return provenance only for a real generated intent, not a fabricated WAIT."""
    if result.intent is None:
        return None
    intent = result.intent
    calls = [public_call(json.loads(row['payload_json'])) for row in pipe.db.query(
        'SELECT payload_json FROM model_calls WHERE rowid>? ORDER BY rowid', (call_watermark,))]
    stage, category = classify_failure(
        result.error, result.guard_verdict, intent.action.value,
        calls=calls, reason=intent.reason,
    )
    evidence_text = result.error or (result.guard_reasons[0] if result.guard_reasons else (intent.reason if intent else None))
    row = {'facts_hash': facts_hash(context), 'started_at': started.isoformat(),
        'available_at': completed.isoformat(), 'valid_until': intent.valid_until.isoformat(),
        'action': intent.action.value, 'target_weight': str(intent.target_weight or 0),
        'confidence': str(intent.confidence or 0), 'confidence_semantics': intent.confidence_semantics.value,
        'holding_horizon_seconds': intent.holding_horizon_seconds,
        'account': {'cash': str(context.portfolio.cash), 'reserved_cash': str(context.portfolio.reserved_cash),
                    'positions': deepcopy(context.portfolio.positions)},
        'account_id': context.portfolio.account_id, 'account_version': context.portfolio.portfolio_version,
        'context_id': context.context_id, 'trace_id': intent.trace_id, 'model_calls': calls,
        'guard': result.guard_verdict, 'guard_reasons': result.guard_reasons,
        'order_id': result.order_id, 'error': result.error.split(':', 1)[0] if result.error else None,
        'failure_stage': stage, 'failure_category': category,
        'model_suggestion': intent.action.value,
        'program_verdict': result.guard_verdict or ('PASS' if intent.action.value != 'WAIT' else 'HOLD_NO_TRADE'),
        'completion_status': 'complete' if result.order_id or (intent.action.value in ('WAIT', 'HOLD') and not result.error and stage not in ('model_call', 'unknown')) else ('failed' if result.error or stage in ('model_call', 'unknown') else 'partial'),
        'sanitized_evidence': sanitize_evidence(evidence_text)}
    plan = pipe.repo.get_coalesce('strategy-plan:' + pipe.cfg.account_id + ':' + context.instrument_id)
    if plan and 'plan' in plan['meta']:
        row['strategy_plan'] = plan['meta']['plan']
    return row



def bind_shared_context(pipe, frozen, sources):
    """Keep actual per-group account facts, replace only common market facts."""
    builder = ContextBuilder(frozen, pipe.calendar, pipe.ledger, ttl_seconds=pipe.cfg.risk.context_ttl_seconds)
    used = {}
    def build(iid, as_of, **kwargs):
        source = sources[iid]
        if as_of != source.as_of:
            raise ValueError('shared sample time mismatch')
        context = builder.build_minute(iid, as_of, **kwargs)
        context.context_id = source.context_id
        context.features = source.features.model_copy(deep=True)
        context.aux = deepcopy(source.aux)
        context.evidence_ids = list(source.evidence_ids)
        context.missing_fields = dict(source.missing_fields)
        context.content_hash = context.compute_hash()
        used[iid] = context
        return context
    pipe.router = frozen
    pipe.broker.router = frozen
    pipe.outcomes.router = frozen
    pipe.context_builder.build_minute = build
    return used


def collect(config_path, output, samples=1, interval_seconds=300, max_seconds=300, probe_models=False):
    if not 1 <= samples <= 12 or interval_seconds < 300 or not 1 <= max_seconds <= 3600:
        raise ValueError('bounded samples/time and minimum 300-second interval required')
    output = Path(output)
    if output.exists():
        raise ValueError('archive output already exists; choose a new run path')
    cfg = load_config(config_path, load_credentials=False)
    # TASK-20261013-07 (ISSUE-20261012-04): the isolated fund is the configured
    # t0.initial_cash (default 500 stays compatible); any positive finite value
    # is legal, zero/negative/non-finite is rejected before anything runs.
    cash = cfg.t0.initial_cash
    if not cfg.t0.enabled or cfg.mode != 'paper' or not cash.is_finite() or cash <= 0:
        raise ValueError('isolated T0 paper configuration with a positive initial_cash required')
    bundle = {'schema_version': 2, 'run_id': 'archive-' + uuid4().hex, 'artifact_kind': 'recorded',
        'initial_cash': str(cash), 'initial_cash_source': 'config.t0.initial_cash',
        'contexts': [], 'archives': {'old': {}, 'new': {}}, 'runtime_configs': {},
        'samples': [], 'gaps': [], 'failure_audits': [], 'status': 'incomplete_archives', 'current_model_calls': 0}
    deadline = time.monotonic()+max_seconds
    calendar = TradingCalendar.load()
    pipes = {}
    selected_id = None
    market = None
    try:
        for sample in range(samples):
            if sample:
                remaining = min(interval_seconds, max(0, deadline-time.monotonic()))
                while remaining > 0:
                    pause = min(1, remaining)
                    time.sleep(pause)
                    remaining -= pause
            if time.monotonic() >= deadline or not calendar.is_session_open(datetime.now(timezone.utc)):
                bundle['gaps'].append({'sample': sample+1, 'reason': 'outside_session_or_run_deadline'})
                break
            if market is None:
                market = DatasetRouter(pref=cfg.data.provider_pref, fixture_root=cfg.data.fixtures_dir or None)
            observations = {}
            for iid in cfg.universe:
                at = datetime.now(timezone.utc)
                try:
                    observations[iid] = market.get_minute_bars(iid, at-timedelta(days=2), at, period='1m')
                except Exception as exc:
                    bundle['gaps'].append({'sample': sample+1, 'instrument': iid, 'reason': type(exc).__name__})
            observed = datetime.now(timezone.utc)
            frozen = FrozenMinutes(observations)
            sources = {}
            for iid in observations:
                try:
                    context = ContextBuilder(frozen, calendar).build_minute(iid, observed, max_stale_seconds=180)
                    if context.missing_fields or any(b.provider in ('fixture', 'test', 'recorded-context')
                                                     for b in observations[iid][0]):
                        bundle.setdefault('data_checks', []).append({'instrument': iid,
                            'as_of': observed.isoformat(), 'reason': 'market_quality_unverified'})
                    else:
                        sources[iid] = context
                except Exception as exc:
                    bundle['gaps'].append({'instrument': iid, 'reason': type(exc).__name__})
            if selected_id is None and sources:
                selected_id = next(iter(sources))
            # A comparison sequence has one instrument per sampling instant;
            # keep it fixed for this run rather than invent simultaneous fills.
            sources = {selected_id: sources[selected_id]} if selected_id in sources else {}
            if sources:
                source = sources[selected_id]
                bundle['contexts'].append(source.model_dump(mode='json'))
                rows = [bar.model_dump(mode='json') for bar in observations[selected_id][0]
                        if bar.available_at <= observed and bar.bar_time <= observed]
                bundle.setdefault('market_observations', []).append({'context_id': source.context_id,
                    'observed_at': observed.isoformat(), 'rows': rows,
                    'raw_data_hash': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()})
            if not sources or not probe_models:
                if not sources:
                    bundle['gaps'].append({'sample': sample+1, 'reason': 'selected_market_quality_unverified'})
                if not probe_models:
                    bundle['gaps'].append({'sample': sample+1, 'reason': 'models_not_explicitly_enabled'})
                atomic_json(output, bundle)
                continue
            if time.monotonic() >= deadline or not calendar.is_session_open(datetime.now(timezone.utc)):
                bundle['gaps'].append({'sample': sample+1, 'reason': 'session_closed_before_models'})
                break
            if not pipes:
                # Credentials enter provider memory only after the free gates.
                from ..runner import _probe_fast_models
                private_cfg = load_config(config_path)
                for label in ('old', 'new'):
                    group = group_config(private_cfg, bundle['run_id'], label, output.parent)
                    pipe = Pipeline(group)
                    pipes[label] = pipe
                    pipe.ensure_account(initial_cash=cash, at=observed)
                    bundle['runtime_configs'][label] = public_runtime_config(group)
                    checks = _probe_fast_models(pipe)
                    if not checks['ok']:
                        bundle['gaps'].append({'group': label, 'reason': 'same_process_model_probe_failed'})
            for label, pipe in pipes.items():
                if not pipe.llm.capability_verified or not pipe.jev.capability_verified:
                    continue
                used = bind_shared_context(pipe, frozen, sources)
                pipe.poll_fills(observed)
                for iid, source in sources.items():
                    if time.monotonic() >= deadline or not calendar.is_session_open(datetime.now(timezone.utc)):
                        bundle['gaps'].append({'group': label, 'context_id': source.context_id, 'reason': 'run_deadline'})
                        break
                    watermark = pipe.db.query('SELECT COALESCE(MAX(rowid),0) AS n FROM model_calls')[0]['n']
                    started = datetime.now(timezone.utc)
                    result = pipe.run_decision(iid, observed)
                    completed = datetime.now(timezone.utc)
                    context = used.get(iid)
                    if context:
                        bundle.setdefault('group_contexts', {}).setdefault(label, {})[source.context_id] = context.model_dump(mode='json')
                    row = archive_result(pipe, context, result, started, completed, watermark) if context else None
                    if row:
                        bundle['archives'][label][source.context_id] = row
                        if completed >= result.intent.valid_until or result.error:
                            bundle['gaps'].append({'group': label, 'context_id': source.context_id,
                                                   'reason': 'intent_failed_or_expired_at_completion'})
                    else:
                        stage, category = classify_failure(result.error, result.guard_verdict, None)
                        bundle.setdefault('failure_audits', []).append({
                            'group': label,
                            'context_id': source.context_id,
                            'failure_stage': stage,
                            'failure_category': category,
                            'model_suggestion': None,
                            'program_verdict': result.guard_verdict or 'ERROR',
                            'completion_status': 'failed',
                            'sanitized_evidence': sanitize_evidence(result.error or str(result.guard_reasons)),
                            'available_at': completed.isoformat(),
                        })
                        bundle['gaps'].append({'group': label, 'context_id': source.context_id,
                            'reason': result.error.split(':', 1)[0] if result.error else result.guard_verdict or 'no_intent'})
                    bundle['samples'].append({'group': label, 'context_id': source.context_id,
                        'facts_hash': facts_hash(source), 'started_at': started.isoformat(),
                        'completed_at': completed.isoformat(), 'budget': pipe.model_budget.snapshot()})
                    atomic_json(output, bundle)
    except KeyboardInterrupt:
        bundle['gaps'].append({'reason': 'interrupted'})
    except Exception as exc:
        bundle['gaps'].append({'reason': type(exc).__name__})
    finally:
        for label, pipe in pipes.items():
            pipe.close_model_worker()
            bundle['current_model_calls'] += pipe.db.query('SELECT COUNT(*) AS n FROM model_calls')[0]['n']
            bundle.setdefault('budgets', {})[label] = pipe.model_budget.snapshot()
            pipe.release_writer_lease()
            pipe.db.close()
        complete = bool(bundle['contexts']) and not bundle['gaps'] and all(
            all(c['context_id'] in bundle['archives'][label] for c in bundle['contexts']) for label in ('old', 'new'))
        bundle['status'] = 'collected' if complete else 'incomplete_archives'
        atomic_json(output, bundle)
    return bundle


def audit_archive_failures(bundle: dict) -> dict:
    """Audit failures and provenance across archives in a bundle without synthesizing data."""
    schema_ver = bundle.get('schema_version', 1)
    status = bundle.get('status', 'unknown')
    total_contexts = len(bundle.get('contexts', []))
    all_categories = [
        'none', 'conservative_wait', 'guard_veto', 'format_error',
        'protocol_error', 'empty_output', 'length_truncated', 'reasoning_only',
        'explicit_refusal', 'model_refused', 'rate_limited', 'http_error',
        'network_error', 'timeout', 'execution_error',
    ]
    counts = {cat: 0 for cat in all_categories}
    unknown_counts = 0
    records = []

    for group_name in ('old', 'new'):
        for cid, row in bundle.get('archives', {}).get(group_name, {}).items():
            cat = row.get('failure_category')
            if cat is None:
                cat = 'unknown'
                unknown_counts += 1
            elif cat in counts:
                counts[cat] += 1
            else:
                cat = 'unknown'
                unknown_counts += 1
            records.append({
                'context_id': cid,
                'group': group_name,
                'failure_stage': row.get('failure_stage', 'unknown' if schema_ver == 1 else 'none'),
                'failure_category': cat,
                'model_suggestion': row.get('model_suggestion', row.get('action')),
                'program_verdict': row.get('program_verdict', row.get('guard')),
                'completion_status': row.get('completion_status', 'incomplete' if schema_ver == 1 else 'unknown'),
                'sanitized_evidence': sanitize_evidence(row.get('sanitized_evidence') or row.get('error')),
            })

    for fail_row in bundle.get('failure_audits', []):
        cat = fail_row.get('failure_category', 'unknown')
        if cat in counts:
            counts[cat] += 1
        else:
            unknown_counts += 1
        records.append({
            'context_id': fail_row.get('context_id', 'unknown'),
            'group': fail_row.get('group', 'unknown'),
            'failure_stage': fail_row.get('failure_stage', 'unknown'),
            'failure_category': cat,
            'model_suggestion': fail_row.get('model_suggestion'),
            'program_verdict': fail_row.get('program_verdict', 'ERROR'),
            'completion_status': fail_row.get('completion_status', 'failed'),
            'sanitized_evidence': sanitize_evidence(fail_row.get('sanitized_evidence')),
        })

    observed = [cat for cat, c in counts.items() if c > 0 and cat != 'none']
    unobserved = [cat for cat, c in counts.items() if c == 0 and cat != 'none']

    return {
        'schema_version': schema_ver,
        'run_id': bundle.get('run_id'),
        'bundle_status': status,
        'total_contexts': total_contexts,
        'total_audited_records': len(records),
        'failure_counts': counts,
        'unknown_field_count': unknown_counts,
        'observed_failure_categories': observed,
        'unobserved_failure_categories': unobserved,
        'audited_records': records,
        'audit_verdict': 'pass' if status == 'collected' and not unknown_counts else 'incomplete_archives',
    }
