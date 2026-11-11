"""Credential-free paper run artifacts; no raw configuration or transport data."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from ..errors import classify_not_fresh_reason
from ..market.base import BEIJING
from .intraday_health import _reserved_attempts, blocker_class, read_intraday_health


CALL_FIELDS = ('call_id', 'trace_id', 'role', 'provider', 'model', 'status', 'is_stub',
               'created_at', 'prompt_id', 'prompt_hash', 'input_tokens', 'output_tokens',
               'latency_ms', 'attempts', 'cost_source', 'cost_amount', 'cost_currency',
               'http_status', 'finish_reason', 'failure_category', 'response_diagnostics')


def sanitize_evidence(text: str | None, max_length: int = 160) -> str | None:
    """Sanitize and truncate text to prevent credential leaks in audit artifacts."""
    if text is None:
        return None
    s = str(text)
    # Redact tokens, keys, passwords, bearer credentials
    s = re.sub(
        r'(?i)(?:bearer\s+|api[_-]?key\s*[:=]\s*|token\s*[:=]\s*|secret\s*[:=]\s*|password\s*[:=]\s*|sk-[a-zA-Z0-9_\-]{6,})[^\s,;"\'\]}>]+',
        '[REDACTED]',
        s,
    )
    s = re.sub(r'https?://[^\s<>"]+', lambda m: m.group(0).split('?')[0], s)
    for placeholder in ('DO_NOT_EXPORT', 'SECRET', 'API_KEY', 'PRIVATE_KEY'):
        s = s.replace(placeholder, '[REDACTED]')
    if len(s) > max_length:
        s = s[:max_length] + '...'
    return s


def public_runtime_config(cfg):
    return json.loads(json.dumps({key: asdict(getattr(cfg, key))
                                 for key in ('t0', 'risk', 'paper')}, default=str))


def public_call(payload):
    from ..domain.modelcall import sanitize_response_diagnostics
    out = {key: payload.get(key) for key in CALL_FIELDS}
    out['response_diagnostics'] = sanitize_response_diagnostics(payload.get('response_diagnostics'))
    return out


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PaperRun:
    def __init__(self, pipe, selected, output=None):
        self.pipe = pipe
        self.run_id = 'paper-' + uuid4().hex
        self.started = datetime.now(timezone.utc)
        self.runtime = public_runtime_config(pipe.cfg)
        self.config_hash = hashlib.sha256(json.dumps(self.runtime, sort_keys=True).encode()).hexdigest()
        self.output = Path(output) if output else pipe.db.path.parent / 'paper-runs' / (self.run_id + '.json')
        self.progress_path = pipe.db.path.parent / 'intraday-health.json'
        self.selected = selected
        self.watermarks = {table: pipe.db.query(f'SELECT COALESCE(MAX(rowid),0) AS n FROM {table}')[0]['n']
                           for table in ('model_calls', 'orders', 'fills')}
        self.budget_before = pipe.model_budget.snapshot()

    def record_progress(self, results, preflight=None, shutdown=None, now=None):
        """Publish credential-free intraday health from the single paper writer."""
        now_dt = now or datetime.now(timezone.utc)
        now_bj = now_dt.astimezone(BEIJING)
        last_dec = results[-1]['decision'] if results else {}
        diag = last_dec.get('diagnostics') or {}
        data_window = diag.get('data_window') if isinstance(diag.get('data_window'), dict) else {}
        # The public summary truncates NotFresh errors to the bare class name;
        # the safe subtype recorded in diagnostics.data_reject keeps health
        # specific instead of collapsing every data failure into "NotFresh"
        # (review R2). Non-NotFresh rounds keep the existing reason order.
        # The subtype is re-classified on the closed whitelist (round 3): a
        # summary built by anything other than the current pipeline can never
        # publish a raw exception body as the health reason.
        reject = diag.get('data_reject') if isinstance(diag.get('data_reject'), dict) else {}
        subtype = reject.get('reason') if isinstance(reject.get('reason'), str) and reject.get('reason') else None
        error_class = last_dec.get('error')
        if error_class == 'NotFresh' and subtype:
            last_reason = classify_not_fresh_reason(subtype)
        else:
            last_reason = diag.get('reason') or error_class
        last_verdict = last_dec.get('guard')
        data_insufficient = bool(
            (preflight is not None and not preflight.get('ok') and 'fresh_complete_minute_data_unverified' in preflight.get('blockers', []))
            or last_verdict == 'WAIT_DATA_QUALITY'
            or (last_dec.get('error') == 'NotFresh')
        )
        risk_exit_active = bool(
            last_dec.get('action') == 'SELL' and not last_dec.get('target_weight') and last_dec.get('order_id')
        )
        payload = {
            'account_id': self.pipe.cfg.account_id,
            'date': now_bj.date().isoformat(),
            'run_id': self.run_id,
            'updated_at': now_bj.isoformat(),
            'last_decision_reason': sanitize_evidence(last_reason, 160) if last_reason else None,
            'last_guard_verdict': last_verdict,
            'data_insufficient': data_insufficient,
            'risk_exit_active': risk_exit_active,
            'unconsumed_result': bool(shutdown and shutdown.get('unconsumed_result')),
            'worker_active': bool(shutdown and shutdown.get('worker_active')),
            # Additive pre-decision bucket + minute-bar facts (Issue30-01);
            # unavailable values stay None instead of being fabricated.
            'last_blocker_class': blocker_class(last_verdict, last_reason, data_insufficient),
            'last_instrument_id': last_dec.get('instrument'),
            'last_data_age_seconds': diag.get('data_age_seconds'),
            'last_age_threshold_seconds': diag.get('age_threshold_seconds'),
            'last_missing_fields': diag.get('missing_fields'),
            'last_continuous_1m_bars': data_window.get('continuous_1m_bars'),
            # Additive no-actionable evidence + this-tick window claim fact
            # (t279u); both are credential-free and stay absent when unknown.
            'last_action_gate': diag.get('action_gate'),
            'last_model_path_claimed': (diag.get('model_window') or {}).get('claim_this_tick'),
        }
        if preflight is not None:
            payload['preflight_blockers'] = preflight.get('blockers', [])
        try:
            atomic_json(self.progress_path, payload)
        except OSError:
            pass

    def finish(self, status, exit_code, results, preflight, shutdown, error=None):
        pipe = self.pipe
        self.record_progress(results, preflight=preflight, shutdown=shutdown)
        calls = [public_call(json.loads(row['payload_json'])) for row in pipe.db.query(
            'SELECT payload_json FROM model_calls WHERE rowid>? ORDER BY rowid', (self.watermarks['model_calls'],))]
        orders = [dict(row) for row in pipe.db.query(
            'SELECT order_id,instrument_id,side,status FROM orders WHERE rowid>? AND account_id=?',
            (self.watermarks['orders'], pipe.cfg.account_id))]
        fills = [dict(row) for row in pipe.db.query(
            'SELECT f.fill_id,f.order_id,f.executed_at FROM fills f JOIN orders o ON o.order_id=f.order_id '
            'WHERE f.rowid>? AND o.account_id=?', (self.watermarks['fills'], pipe.cfg.account_id))]
        snap = pipe.ledger.snapshot(datetime.now(timezone.utc))
        account = {'version': snap.version, 'cash': str(snap.cash), 'reserved_cash': str(snap.reserved_cash),
            'equity': str(snap.equity), 'valuation_basis': 'ledger_cost_basis_not_live_marks',
            'positions': [{'instrument_id': p.instrument_id, 'qty': str(p.quantity),
                           'market_value': str(p.market_value)} for p in snap.positions],
            'cash_invariants_ok': 0 <= snap.reserved_cash <= snap.cash,
            'open_orders': len([o for o in pipe.repo.open_orders() if o['account_id'] == pipe.cfg.account_id])}
        contexts = []
        for item in results:
            context = pipe.repo.get_context(item['decision'].get('context_id', ''))
            if context:
                contexts.append({'context_id': context['context_id'], 'as_of': context['as_of'],
                    'content_hash': context['content_hash'], 'feature_version': context['features']['feature_version'],
                    'quality': context['features']['quality'], 'missing_fields': context['missing_fields'],
                    'latest_minute': (context.get('aux', {}).get('minute_bars') or [{}])[-1]})
        plans = []
        for iid in self.selected:
            cached = pipe.repo.get_coalesce('strategy-plan:' + pipe.cfg.account_id + ':' + iid)
            if cached and 'plan' in cached['meta']:
                plans.append({k: cached['meta']['plan'].get(k) for k in
                    ('plan_id', 'instrument_id', 'model', 'prompt_version', 'rule_version',
                     'available_at', 'valid_until', 'holding_horizon_seconds')})
        model_budget_after = pipe.model_budget.snapshot()
        call_attempts = sum(_reserved_attempts(c) for c in calls)
        budget_delta = (
            int(model_budget_after.get('used', 0)) - int(self.budget_before.get('used', 0))
            if self.budget_before.get('day') == model_budget_after.get('day')
            else int(model_budget_after.get('used', 0))
        )
        intraday_health = read_intraday_health(
            pipe.db.path,
            pipe.cfg.account_id,
            halted=bool(shutdown.get('worker_active') or shutdown.get('unconsumed_result') or error),
            run_id=self.run_id,
            t0_cfg=pipe.cfg.t0,
        )
        report = {'schema_version': 1, 'run_id': self.run_id, 'started_at': self.started.isoformat(),
            'completed_at': datetime.now(timezone.utc).isoformat(), 'config_hash': self.config_hash,
            'runtime_config': self.runtime, 'profile': pipe.cfg.t0.profile, 'mode': pipe.cfg.mode,
            'account_id': pipe.cfg.account_id, 'monitored_instruments': self.selected,
            'status': status, 'exit_code': exit_code, 'trading_acceptance': 'not_evaluated',
            'preflight': preflight, 'shutdown': shutdown, 'model_pending': shutdown['unconsumed_result'],
            'model_calls': len(calls), 'model_call_attempts': call_attempts,
            'budget_requests_delta': budget_delta,
            'count_semantics': 'model_calls=logical_audit_rows;model_call_attempts=http_budget_requests',
            'orders': len(orders), 'fills': len(fills),
            'model_call_records': calls, 'order_records': orders, 'fill_records': fills,
            'context_records': contexts, 'plan_references': plans,
            'budget_before': self.budget_before, 'model_budget': model_budget_after,
            'intraday_health': intraday_health,
            'account': account, 'cash': str(snap.cash), 'equity': str(snap.equity),
            'positions': account['positions'], 'results': results, 'error': error,
            'report_path': str(self.output), 'cost_semantics': 'configured_estimates_and_pending_reservations'}
        atomic_json(self.output, report)
        try:
            from ..reports.daily import generate_for_config
            daily, path = generate_for_config(pipe.cfg)
            report['daily_report'] = {'status': 'generated', 'date': daily['date'], 'html': path}
        except Exception as exc:
            report['daily_report'] = {'status': 'failed', 'error_type': type(exc).__name__}
        atomic_json(self.output, report)
        return report
