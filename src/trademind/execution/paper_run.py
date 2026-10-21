"""Credential-free paper run artifacts; no raw configuration or transport data."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from ..market.base import BEIJING
from ..reports.locale import explanation, failure_category_text, time_text


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


def _reserved_attempts(c: dict) -> int:
    if c.get('failure_category') == 'budget_exhausted':
        return 0
    if c.get('cost_amount') is None and c.get('status') == 'error' and any(
        k in str(c.get('error') or '') for k in ('budget', 'pricing', 'closing')
    ):
        return 0
    raw_att = c.get('attempts')
    return int(raw_att) if isinstance(raw_att, int) else 1


def read_intraday_health(
    database: str | Path,
    account_id: str,
    now: datetime | None = None,
    session_open: bool | None = None,
    halted: bool = False,
    run_id: str | None = None,
    t0_cfg: object | None = None,
) -> dict:
    """Read-only intraday strategy and runtime health summary for a single account and Shanghai date."""
    now_bj = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    day_str = now_bj.date().isoformat()
    start_bj = datetime.combine(now_bj.date(), datetime.min.time(), BEIJING)
    stop_bj = min(start_bj + timedelta(days=1), now_bj)
    # Bounded history window (TASK-20261013-16): ISO-string bounds two days
    # wider than the Shanghai day keep every stored offset form inside, while
    # the per-row Shanghai-day filter below stays the exact semantics.
    window_lo = (start_bj - timedelta(days=2)).astimezone(timezone.utc).isoformat()
    window_hi = (stop_bj + timedelta(days=2)).astimezone(timezone.utc).isoformat()
    if session_open is None:
        from ..market.calendar import TradingCalendar
        cal = TradingCalendar.load()
        session_open = bool(cal.is_session_open(now_bj) and now_bj.strftime('%H:%M') < '14:57')

    db_path = Path(database).resolve()
    progress_path = db_path.parent / 'intraday-health.json'
    progress = {}
    if progress_path.exists():
        try:
            raw_prog = json.loads(progress_path.read_text(encoding='utf-8'))
            if (
                 isinstance(raw_prog, dict)
                and raw_prog.get('account_id') == account_id
                and raw_prog.get('date') == day_str
                and (run_id is None or raw_prog.get('run_id') in (None, run_id))
            ):
                progress = raw_prog
        except (OSError, ValueError, TypeError):
            progress = {}

    open_positions_count = 0
    open_orders_count = 0
    kill_switch_engaged = False
    budget_meta = None
    health_meta = None
    active_plan_count = 0
    last_strategy_success_at = None
    requested_count_today = None
    audit_rows_today = 0
    audit_attempts_today = 0
    strategy_calls_today = 0
    trailing_call_failures = 0
    latest_failed_call = None
    last_decision_reason = progress.get('last_decision_reason')
    last_guard_verdict = progress.get('last_guard_verdict')
    risk_exit_today = bool(progress.get('risk_exit_active'))

    if db_path.exists():
        conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            account_count = (
                conn.execute('SELECT count(*) FROM account_state').fetchone()[0]
                if 'account_state' in names else 0
            )
            scoped = account_count <= 1
            if 'account_state' in names:
                row = conn.execute(
                    'SELECT payload_json FROM account_state WHERE account_id=?', (account_id,)
                ).fetchone()
                if row:
                    snap = json.loads(row['payload_json'])
                    open_positions_count = sum(
                        1 for p in snap.get('positions', [])
                        if Decimal(str(p.get('quantity', '0'))) > 0
                    )
            if 'orders' in names:
                open_orders_count = conn.execute(
                    "SELECT count(*) FROM orders WHERE account_id=? AND status IN ('NEW','SUBMITTING','ACKNOWLEDGED','PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')",
                    (account_id,),
                ).fetchone()[0]
            if 'control_state' in names:
                ks_row = conn.execute("SELECT value_json FROM control_state WHERE key='kill_switch'").fetchone()
                if ks_row:
                    kill_switch_engaged = bool(json.loads(ks_row['value_json']).get('engaged'))
            if 'coalesce_state' in names:
                b_rows = conn.execute(
                    'SELECT key, meta_json FROM coalesce_state WHERE key=? OR key=?',
                    ('model-budget:' + account_id, 'model-budget:' + account_id + ':history:' + day_str),
                ).fetchall()
                for r in b_rows:
                    meta = json.loads(r['meta_json'])
                    if meta.get('day') == day_str:
                        budget_meta = meta
                        break
                if budget_meta is None and b_rows:
                    # Prior-day budget row exists; today's usage has not started yet (0).
                    requested_count_today = 0

                h_row = conn.execute(
                    'SELECT meta_json FROM coalesce_state WHERE key=?',
                    ('strategy-health:' + account_id,),
                ).fetchone()
                if h_row:
                    hm = json.loads(h_row['meta_json'])
                    if hm.get('day') == day_str and hm.get('account_id') in (None, account_id):
                        health_meta = hm

                plan_prefix = 'strategy-plan:' + account_id + ':'
                p_rows = conn.execute(
                    'SELECT key, meta_json FROM coalesce_state WHERE substr(key,1,length(?))=?',
                    (plan_prefix, plan_prefix),
                ).fetchall()
                for r in p_rows:
                    if r['key'].endswith(':attempt'):
                        continue
                    pm = json.loads(r['meta_json']).get('plan')
                    if isinstance(pm, dict):
                        avail = pm.get('available_at')
                        vu = pm.get('valid_until')
                        if avail:
                            avail_dt = datetime.fromisoformat(avail).astimezone(BEIJING)
                            if start_bj <= avail_dt <= stop_bj:
                                if not last_strategy_success_at or avail > last_strategy_success_at:
                                    last_strategy_success_at = avail
                        if vu:
                            try:
                                if datetime.fromisoformat(vu).astimezone(BEIJING) > now_bj:
                                    active_plan_count += 1
                            except ValueError:
                                pass

            if 'strategy_plan_history' in names:
                hist_rows = conn.execute(
                    'SELECT available_at FROM strategy_plan_history '
                    'WHERE account_id=? AND available_at>=? AND available_at<=? ORDER BY available_at',
                    (account_id, window_lo, window_hi),
                ).fetchall()
                for r in hist_rows:
                    avail = r['available_at']
                    if avail:
                        avail_dt = datetime.fromisoformat(avail).astimezone(BEIJING)
                        if start_bj <= avail_dt <= stop_bj:
                            if not last_strategy_success_at or avail > last_strategy_success_at:
                                last_strategy_success_at = avail

            if 'model_calls' in names and scoped:
                c_rows = conn.execute(
                    'SELECT payload_json FROM model_calls '
                    'WHERE created_at>=? AND created_at<=? ORDER BY rowid',
                    (window_lo, window_hi),
                ).fetchall()
                calls_today = []
                for r in c_rows:
                    c = json.loads(r['payload_json'])
                    created = c.get('created_at')
                    if not created:
                        continue
                    cat_dt = datetime.fromisoformat(created).astimezone(BEIJING)
                    if start_bj <= cat_dt <= stop_bj:
                        calls_today.append(c)
                audit_rows_today = len(calls_today)
                audit_attempts_today = sum(_reserved_attempts(c) for c in calls_today)
                strat_calls = [c for c in calls_today if c.get('role') == 'strategy']
                strategy_calls_today = len(strat_calls)
                for c in strat_calls:
                    if c.get('status') in ('ok', 'repaired') and c.get('failure_category') in (None, 'none'):
                        trailing_call_failures = 0
                    else:
                        trailing_call_failures += 1
                        latest_failed_call = c

            if 'intents' in names and scoped and not last_decision_reason:
                # Latest intent of the Shanghai day: bounded window read, then
                # the exact day/account pick in Python (margin rows skipped).
                # Under write-order storage the first candidate already wins.
                cand_rows = conn.execute(
                    'SELECT payload_json FROM intents '
                    'WHERE created_at>=? AND created_at<=? ORDER BY rowid DESC LIMIT 8',
                    (window_lo, window_hi),
                ).fetchall()
                for i_row in cand_rows:
                    intent = json.loads(i_row['payload_json'])
                    iat = datetime.fromisoformat(intent['created_at']).astimezone(BEIJING)
                    if start_bj <= iat <= stop_bj and intent.get('account_id') in (None, account_id):
                        last_decision_reason = sanitize_evidence(intent.get('reason'), 160)
                        break
        finally:
            conn.close()

    if budget_meta is not None:
        requested_count_today = int(budget_meta.get('used', 0))

    if health_meta is not None:
        consecutive_failures = int(health_meta.get('consecutive_failures') or 0)
        if health_meta.get('last_success_at'):
            if not last_strategy_success_at or health_meta['last_success_at'] > last_strategy_success_at:
                last_strategy_success_at = health_meta['last_success_at']
        last_failure_at = health_meta.get('last_failure_at')
        last_failure_category = health_meta.get('last_failure_category')
        last_failure_reason = health_meta.get('last_failure_reason')
        backoff_seconds = int(health_meta.get('backoff_seconds') or 0)
        next_allowed_request_at = health_meta.get('next_allowed_request_at')
    else:
        consecutive_failures = trailing_call_failures
        if latest_failed_call is not None:
            last_failure_at = latest_failed_call.get('created_at')
            # Never guess missing categories on old records.
            raw_cat = latest_failed_call.get('failure_category')
            last_failure_category = raw_cat if isinstance(raw_cat, str) and raw_cat else 'unknown'
            last_failure_reason = sanitize_evidence(latest_failed_call.get('error'), 160)
        else:
            last_failure_at = None
            last_failure_category = None
            last_failure_reason = None
        backoff_seconds = 0
        next_allowed_request_at = None

    backoff_active = False
    if consecutive_failures > 0 and next_allowed_request_at:
        try:
            backoff_active = now_bj < datetime.fromisoformat(next_allowed_request_at).astimezone(BEIJING)
        except ValueError:
            backoff_active = False

    if last_failure_category:
        if last_failure_category == 'unknown':
            last_failure_reason_zh = '历史记录缺少失败分类'
        else:
            last_failure_reason_zh = failure_category_text(last_failure_category)
            if last_failure_reason:
                detail_zh = explanation(last_failure_reason)
                if detail_zh and detail_zh not in last_failure_reason_zh and not detail_zh.startswith('历史说明尚未中文化'):
                    last_failure_reason_zh = f"{last_failure_reason_zh}（{detail_zh}）"
    else:
        last_failure_reason_zh = None

    budget_limit = getattr(t0_cfg, 'daily_decision_call_budget', None)
    strategy_limit = getattr(t0_cfg, 'strategy_request_limit', None)
    cost_limit = getattr(t0_cfg, 'daily_model_cost_limit_cny', None)
    settled_cny = str(budget_meta.get('cost_settled_cny', '0')) if budget_meta else '0'
    pending_cny = str(budget_meta.get('cost_pending_cny', '0')) if budget_meta else '0'
    reserved_cny = str(budget_meta.get('cost_reserved_cny', '0')) if budget_meta else '0'
    strategy_used = int(budget_meta.get('strategy', 0)) if budget_meta else 0
    probe_used = int(budget_meta.get('probe', 0)) if budget_meta else 0
    jev_used = int(budget_meta.get('jev', 0)) if budget_meta else 0

    budget_exhausted = bool(
        (budget_limit is not None and requested_count_today is not None and requested_count_today >= budget_limit)
        or (strategy_limit is not None and strategy_used >= strategy_limit)
        or (cost_limit is not None and Decimal(reserved_cny) >= Decimal(str(cost_limit)))
        or last_failure_category in ('budget_exhausted', 'budget_exceeded')
        or last_decision_reason in (
            'shared model budget exhausted',
            'T0 daily model count/cost budget exhausted',
            'flash pricing unconfigured',
            'JEV pricing unconfigured; paid request blocked',
        )
    )

    unconsumed = bool(progress.get('unconsumed_result') or progress.get('worker_active'))
    data_insufficient = bool(
        progress.get('data_insufficient')
        or last_guard_verdict == 'WAIT_DATA_QUALITY'
        or any(
            k in str(last_decision_reason or '')
            for k in (
                'incomplete minute features',
                'missing or stale visible minute context',
                'no fresh visible minute quote',
                'fresh_complete_minute_data_unverified',
            )
        )
    )

    if halted or kill_switch_engaged or unconsumed:
        health_status = '需要人工核对'
    elif not session_open:
        health_status = '等待开市'
    elif (
        open_orders_count > 0
        or risk_exit_today
        or last_decision_reason in ('pending order; no new model decision', 'risk_exit', 'time_stop', 'stop_loss', 'session_close')
        or (open_positions_count > 0 and (consecutive_failures > 0 or budget_exhausted))
    ):
        health_status = '持仓风险处理'
    elif budget_exhausted:
        health_status = '预算限制'
    elif consecutive_failures > 0:
        health_status = '策略请求失败退避'
    elif data_insufficient:
        health_status = '行情不足'
    else:
        health_status = '有效策略观望'

    last_decision_reason_zh = explanation(last_decision_reason) if last_decision_reason else None

    if health_status == '需要人工核对':
        status_message_zh = '需要人工核对：存在锁定暂停、kill switch 或未消费模型结果'
    elif health_status == '等待开市':
        status_message_zh = '等待开市：当前非连续竞价时段，开市后自动检查并启动'
    elif health_status == '持仓风险处理':
        status_message_zh = f'持仓风险处理：持仓 {open_positions_count} 只，未成交订单 {open_orders_count} 笔，确定性止损/期限退出与订单轮询优先执行'
    elif health_status == '预算限制':
        status_message_zh = f'预算限制：今日预算请求 {requested_count_today if requested_count_today is not None else "未确认"} 次，已结算估值 {settled_cny} 元'
    elif health_status == '策略请求失败退避':
        next_txt = time_text(next_allowed_request_at) if next_allowed_request_at else '待定'
        status_message_zh = (
            f'策略请求失败退避：连续失败 {consecutive_failures} 次'
            f'（{last_failure_reason_zh or "原因未知"}），下次允许请求时间 {next_txt}'
        )
    elif health_status == '行情不足':
        status_message_zh = '行情不足：分钟行情缺失或未达新鲜度门禁，暂不发起付费模型调用'
    else:
        succ_txt = time_text(last_strategy_success_at) if last_strategy_success_at else '今日尚无'
        status_message_zh = f'有效策略观望：有效计划 {active_plan_count} 份（最近成功：{succ_txt}），{last_decision_reason_zh or "等待可执行条件"}'

    return {
        'account_id': account_id,
        'date': day_str,
        'as_of': now_bj.isoformat(),
        'run_id': run_id or progress.get('run_id'),
        'session_open': bool(session_open),
        'health_status': health_status,
        'status_message_zh': status_message_zh,
        'last_strategy_success_at': last_strategy_success_at,
        'active_plan_count': active_plan_count,
        'consecutive_failures': consecutive_failures,
        'last_failure_at': last_failure_at,
        'last_failure_category': last_failure_category,
        'last_failure_reason': last_failure_reason,
        'last_failure_reason_zh': last_failure_reason_zh,
        'backoff_active': backoff_active,
        'backoff_seconds': backoff_seconds,
        'next_allowed_request_at': next_allowed_request_at,
        'requested_count_today': requested_count_today,
        'audit_rows_today': audit_rows_today,
        'audit_attempts_today': audit_attempts_today,
        'strategy_calls_today': strategy_calls_today,
        'count_semantics': 'requested_count_today=http_budget_requests;audit_rows_today=logical_calls;audit_attempts_today=sum_of_call_attempts',
        'budget': {
            'day': day_str,
            'used': requested_count_today,
            'limit': budget_limit,
            'strategy_used': strategy_used,
            'strategy_limit': strategy_limit,
            'probe_used': probe_used,
            'jev_used': jev_used,
            'cost_settled_cny': settled_cny,
            'cost_pending_cny': pending_cny,
            'cost_reserved_cny': reserved_cny,
        },
        'open_positions_count': open_positions_count,
        'open_orders_count': open_orders_count,
        'last_decision_reason': last_decision_reason,
        'last_decision_reason_zh': last_decision_reason_zh,
    }


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
        last_reason = diag.get('reason') or last_dec.get('error')
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
        }
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
