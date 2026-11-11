"""Read-only intraday health. Split out of paper_run without changing the report."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from ..errors import classify_not_fresh_reason
from ..market.base import BEIJING
from ..reports.locale import BLOCKER_CLASS_TEXT, explanation, failure_category_text, time_text


# Pre-decision blocker buckets (Issue30-01): the two coarse health tiers
# 行情不足/有效策略观望 previously collapsed data rejects, async-pending
# rounds, the global model window and ordinary strategy waits. The classes
# below are additive fields; the seven Chinese health_status tiers and their
# priority order (halt → session → risk → budget → backoff → data → wait)
# stay unchanged for existing consumers.
STRATEGY_WAIT_REASONS = (
    'no actionable strategy conditions',
    'uncertain JEV evidence or plan',
    'entry evidence insufficient',
    'paper new-entry observation only',
)
_PROGRESS_BLOCKERS = ('data_reject', 'async_pending', 'global_cooldown', 'strategy_wait', 'unknown')
_DATA_MARKERS = (
    'incomplete minute features',
    'missing or stale visible minute context',
    'missing visible minute bars',
    'stale visible minute context',
    'no fresh visible minute quote',
    'fresh_complete_minute_data_unverified',
)
_RISK_REASONS = ('pending order; no new model decision', 'risk_exit', 'time_stop', 'stop_loss', 'session_close')
_BUDGET_REASONS = (
    'shared model budget exhausted',
    'T0 daily model count/cost budget exhausted',
    'flash pricing unconfigured',
    'JEV pricing unconfigured; paid request blocked',
)
_OPEN_ORDER_COUNT = (
    "SELECT count(*) FROM orders WHERE account_id=? AND status IN "
    "('NEW','SUBMITTING','ACKNOWLEDGED','PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')"
)


def blocker_class(last_guard_verdict, last_decision_reason, data_insufficient):
    """Classify the last decision's blocker into an additive bucket slug."""
    if last_guard_verdict == 'WAIT_MODEL_PENDING':
        return 'async_pending'
    if data_insufficient or last_guard_verdict == 'WAIT_DATA_QUALITY':
        return 'data_reject'
    if last_decision_reason == 'global model evaluation cooldown':
        return 'global_cooldown'
    if last_decision_reason in STRATEGY_WAIT_REASONS:
        return 'strategy_wait'
    return 'unknown' if last_decision_reason else None


def _reserved_attempts(c: dict) -> int:
    if c.get('failure_category') == 'budget_exhausted':
        return 0
    if c.get('cost_amount') is None and c.get('status') == 'error' and any(
        k in str(c.get('error') or '') for k in ('budget', 'pricing', 'closing')
    ):
        return 0
    raw_att = c.get('attempts')
    return int(raw_att) if isinstance(raw_att, int) else 1


def _bounds(now):
    now_bj = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    day_str = now_bj.date().isoformat()
    start_bj = datetime.combine(now_bj.date(), datetime.min.time(), BEIJING)
    stop_bj = min(start_bj + timedelta(days=1), now_bj)
    # Bounded history window (TASK-20261013-16): ISO-string bounds two days
    # wider than the Shanghai day keep every stored offset form inside, while
    # the per-row Shanghai-day filter below stays the exact semantics.
    window_lo = (start_bj - timedelta(days=2)).astimezone(timezone.utc).isoformat()
    window_hi = (stop_bj + timedelta(days=2)).astimezone(timezone.utc).isoformat()
    return now_bj, day_str, start_bj, stop_bj, window_lo, window_hi


def _session_open(now_bj, session_open):
    if session_open is not None:
        return session_open
    from ..market.calendar import TradingCalendar
    cal = TradingCalendar.load()
    return bool(cal.is_session_open(now_bj) and now_bj.strftime('%H:%M') < '14:57')


def _load_progress(db_path, account_id, day_str, run_id):
    progress_path = db_path.parent / 'intraday-health.json'
    if not progress_path.exists():
        return {}
    try:
        raw_prog = json.loads(progress_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    if (
        isinstance(raw_prog, dict)
        and raw_prog.get('account_id') == account_id
        and raw_prog.get('date') == day_str
        and (run_id is None or raw_prog.get('run_id') in (None, run_id))
    ):
        return raw_prog
    return {}


def _later_success(current, avail, start_bj, stop_bj):
    if not avail:
        return current
    avail_dt = datetime.fromisoformat(avail).astimezone(BEIJING)
    if start_bj <= avail_dt <= stop_bj and (not current or avail > current):
        return avail
    return current


@dataclass
class _Scan:
    open_positions_count: int = 0
    open_orders_count: int = 0
    kill_switch_engaged: bool = False
    budget_meta: dict | None = None
    health_meta: dict | None = None
    active_plan_count: int = 0
    last_strategy_success_at: str | None = None
    requested_count_today: int | None = None
    audit_rows_today: int = 0
    audit_attempts_today: int = 0
    strategy_calls_today: int = 0
    trailing_call_failures: int = 0
    latest_failed_call: dict | None = None
    last_decision_reason: str | None = None
    cooldown_last_run_at: str | None = None
    cooldown_claim: dict = field(default_factory=dict)


def _positions(conn, names, account_id):
    if 'account_state' not in names:
        return 0
    row = conn.execute(
        'SELECT payload_json FROM account_state WHERE account_id=?', (account_id,)
    ).fetchone()
    if not row:
        return 0
    snap = json.loads(row['payload_json'])
    return sum(1 for p in snap.get('positions', []) if Decimal(str(p.get('quantity', '0'))) > 0)


def _budget_and_window(conn, scan, account_id, day_str):
    b_rows = conn.execute(
        'SELECT key, meta_json FROM coalesce_state WHERE key=? OR key=?',
        ('model-budget:' + account_id, 'model-budget:' + account_id + ':history:' + day_str),
    ).fetchall()
    for r in b_rows:
        meta = json.loads(r['meta_json'])
        if meta.get('day') == day_str:
            scan.budget_meta = meta
            break
    if scan.budget_meta is None and b_rows:
        # Prior-day budget row exists; today's usage has not started yet (0).
        scan.requested_count_today = 0
    h_row = conn.execute(
        'SELECT meta_json FROM coalesce_state WHERE key=?',
        ('strategy-health:' + account_id,),
    ).fetchone()
    if h_row:
        hm = json.loads(h_row['meta_json'])
        if hm.get('day') == day_str and hm.get('account_id') in (None, account_id):
            scan.health_meta = hm
    # Persisted fast-chain global window claim (cooldown diagnostics;
    # additive, never gates anything here).
    w_row = conn.execute(
        'SELECT last_run_at, meta_json FROM coalesce_state WHERE key=?',
        ('fast-eval:' + account_id,),
    ).fetchone()
    if w_row:
        scan.cooldown_last_run_at = w_row['last_run_at']
        try:
            scan.cooldown_claim = json.loads(w_row['meta_json'])
        except ValueError:
            scan.cooldown_claim = {}


def _plans(conn, scan, account_id, start_bj, stop_bj, now_bj):
    plan_prefix = 'strategy-plan:' + account_id + ':'
    p_rows = conn.execute(
        'SELECT key, meta_json FROM coalesce_state WHERE substr(key,1,length(?))=?',
        (plan_prefix, plan_prefix),
    ).fetchall()
    for r in p_rows:
        if r['key'].endswith(':attempt'):
            continue
        pm = json.loads(r['meta_json']).get('plan')
        if not isinstance(pm, dict):
            continue
        scan.last_strategy_success_at = _later_success(
            scan.last_strategy_success_at, pm.get('available_at'), start_bj, stop_bj)
        vu = pm.get('valid_until')
        if not vu:
            continue
        try:
            if datetime.fromisoformat(vu).astimezone(BEIJING) > now_bj:
                scan.active_plan_count += 1
        except ValueError:
            pass


def _history(conn, names, scan, account_id, start_bj, stop_bj, window_lo, window_hi):
    if 'strategy_plan_history' not in names:
        return
    hist_rows = conn.execute(
        'SELECT available_at FROM strategy_plan_history '
        'WHERE account_id=? AND available_at>=? AND available_at<=? ORDER BY available_at',
        (account_id, window_lo, window_hi),
    ).fetchall()
    for r in hist_rows:
        scan.last_strategy_success_at = _later_success(
            scan.last_strategy_success_at, r['available_at'], start_bj, stop_bj)


def _calls(conn, names, scan, scoped, start_bj, stop_bj, window_lo, window_hi):
    if 'model_calls' not in names or not scoped:
        return
    c_rows = conn.execute(
        'SELECT payload_json FROM model_calls WHERE created_at>=? AND created_at<=? ORDER BY rowid',
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
    scan.audit_rows_today = len(calls_today)
    scan.audit_attempts_today = sum(_reserved_attempts(c) for c in calls_today)
    strat_calls = [c for c in calls_today if c.get('role') == 'strategy']
    scan.strategy_calls_today = len(strat_calls)
    for c in strat_calls:
        if c.get('status') in ('ok', 'repaired') and c.get('failure_category') in (None, 'none'):
            scan.trailing_call_failures = 0
        else:
            scan.trailing_call_failures += 1
            scan.latest_failed_call = c


def _intent_reason(conn, names, scan, scoped, account_id, start_bj, stop_bj, window_lo, window_hi):
    if 'intents' not in names or not scoped or scan.last_decision_reason:
        return
    # Latest intent of the Shanghai day: bounded window read, then
    # the exact day/account pick in Python (margin rows skipped).
    # Under write-order storage the first candidate already wins.
    from .paper_run import sanitize_evidence
    cand_rows = conn.execute(
        'SELECT payload_json FROM intents WHERE created_at>=? AND created_at<=? ORDER BY rowid DESC LIMIT 8',
        (window_lo, window_hi),
    ).fetchall()
    for i_row in cand_rows:
        intent = json.loads(i_row['payload_json'])
        iat = datetime.fromisoformat(intent['created_at']).astimezone(BEIJING)
        if start_bj <= iat <= stop_bj and intent.get('account_id') in (None, account_id):
            scan.last_decision_reason = sanitize_evidence(intent.get('reason'), 160)
            break


def _scan_database(db_path, account_id, day_str, start_bj, stop_bj, now_bj, window_lo, window_hi, reason):
    scan = _Scan(last_decision_reason=reason)
    if not db_path.exists():
        return scan
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
        scan.open_positions_count = _positions(conn, names, account_id)
        if 'orders' in names:
            scan.open_orders_count = conn.execute(_OPEN_ORDER_COUNT, (account_id,)).fetchone()[0]
        if 'control_state' in names:
            ks_row = conn.execute("SELECT value_json FROM control_state WHERE key='kill_switch'").fetchone()
            if ks_row:
                scan.kill_switch_engaged = bool(json.loads(ks_row['value_json']).get('engaged'))
        if 'coalesce_state' in names:
            _budget_and_window(conn, scan, account_id, day_str)
            _plans(conn, scan, account_id, start_bj, stop_bj, now_bj)
        _history(conn, names, scan, account_id, start_bj, stop_bj, window_lo, window_hi)
        _calls(conn, names, scan, scoped, start_bj, stop_bj, window_lo, window_hi)
        _intent_reason(conn, names, scan, scoped, account_id, start_bj, stop_bj, window_lo, window_hi)
    finally:
        conn.close()
    if scan.budget_meta is not None:
        scan.requested_count_today = int(scan.budget_meta.get('used', 0))
    return scan


def _failure_fields(scan):
    from .paper_run import sanitize_evidence
    health_meta = scan.health_meta
    if health_meta is not None:
        consecutive = int(health_meta.get('consecutive_failures') or 0)
        success = scan.last_strategy_success_at
        if health_meta.get('last_success_at') and (not success or health_meta['last_success_at'] > success):
            success = health_meta['last_success_at']
        return {
            'consecutive_failures': consecutive,
            'last_strategy_success_at': success,
            'last_failure_at': health_meta.get('last_failure_at'),
            'last_failure_category': health_meta.get('last_failure_category'),
            'last_failure_reason': health_meta.get('last_failure_reason'),
            'backoff_seconds': int(health_meta.get('backoff_seconds') or 0),
            'next_allowed_request_at': health_meta.get('next_allowed_request_at'),
        }
    latest = scan.latest_failed_call
    if latest is None:
        category = reason = failed_at = None
    else:
        failed_at = latest.get('created_at')
        # Never guess missing categories on old records.
        raw_cat = latest.get('failure_category')
        category = raw_cat if isinstance(raw_cat, str) and raw_cat else 'unknown'
        reason = sanitize_evidence(latest.get('error'), 160)
    return {
        'consecutive_failures': scan.trailing_call_failures,
        'last_strategy_success_at': scan.last_strategy_success_at,
        'last_failure_at': failed_at,
        'last_failure_category': category,
        'last_failure_reason': reason,
        'backoff_seconds': 0,
        'next_allowed_request_at': None,
    }


def _cooldown(last_run_at, fast_eval_seconds, now_bj):
    remaining = next_at = None
    if last_run_at and fast_eval_seconds:
        try:
            elapsed = (now_bj - datetime.fromisoformat(last_run_at).astimezone(BEIJING)).total_seconds()
            remaining = max(0, int(fast_eval_seconds - elapsed))
        except ValueError:
            remaining = None
        try:
            next_at = (datetime.fromisoformat(last_run_at) + timedelta(seconds=fast_eval_seconds)).isoformat()
        except ValueError:
            next_at = None
    return remaining, next_at


def _backoff_active(consecutive_failures, next_allowed, now_bj):
    if not (consecutive_failures > 0 and next_allowed):
        return False
    try:
        return now_bj < datetime.fromisoformat(next_allowed).astimezone(BEIJING)
    except ValueError:
        return False


def _failure_reason_zh(category, reason):
    if not category:
        return None
    if category == 'unknown':
        return '历史记录缺少失败分类'
    text = failure_category_text(category)
    if reason:
        detail_zh = explanation(reason)
        if detail_zh and detail_zh not in text and not detail_zh.startswith('历史说明尚未中文化'):
            text = f'{text}（{detail_zh}）'
    return text


def _budget_view(budget_meta, t0_cfg, requested_count_today, failure_category, last_decision_reason):
    budget_limit = getattr(t0_cfg, 'daily_decision_call_budget', None)
    strategy_limit = getattr(t0_cfg, 'strategy_request_limit', None)
    cost_limit = getattr(t0_cfg, 'daily_model_cost_limit_cny', None)
    settled = str(budget_meta.get('cost_settled_cny', '0')) if budget_meta else '0'
    pending = str(budget_meta.get('cost_pending_cny', '0')) if budget_meta else '0'
    reserved = str(budget_meta.get('cost_reserved_cny', '0')) if budget_meta else '0'
    strategy_used = int(budget_meta.get('strategy', 0)) if budget_meta else 0
    exhausted = bool(
        (budget_limit is not None and requested_count_today is not None and requested_count_today >= budget_limit)
        or (strategy_limit is not None and strategy_used >= strategy_limit)
        or (cost_limit is not None and Decimal(reserved) >= Decimal(str(cost_limit)))
        or failure_category in ('budget_exhausted', 'budget_exceeded')
        or last_decision_reason in _BUDGET_REASONS
    )
    return {
        'budget_limit': budget_limit,
        'strategy_limit': strategy_limit,
        'settled_cny': settled,
        'pending_cny': pending,
        'reserved_cny': reserved,
        'strategy_used': strategy_used,
        'probe_used': int(budget_meta.get('probe', 0)) if budget_meta else 0,
        'jev_used': int(budget_meta.get('jev', 0)) if budget_meta else 0,
        'exhausted': exhausted,
    }


def _data_insufficient(progress, verdict, reason):
    return bool(
        progress.get('data_insufficient')
        or verdict == 'WAIT_DATA_QUALITY'
        or any(k in str(reason or '') for k in _DATA_MARKERS)
    )


def _health_status(halted, kill_switch, unconsumed, session_open, scan, risk_exit_today,
                   reason, consecutive, exhausted, data_insufficient):
    if halted or kill_switch or unconsumed:
        return '需要人工核对'
    if not session_open:
        return '等待开市'
    if (
        scan.open_orders_count > 0
        or risk_exit_today
        or reason in _RISK_REASONS
        or (scan.open_positions_count > 0 and (consecutive > 0 or exhausted))
    ):
        return '持仓风险处理'
    if exhausted:
        return '预算限制'
    if consecutive > 0:
        return '策略请求失败退避'
    if data_insufficient:
        return '行情不足'
    return '有效策略观望'


def _status_message(health_status, scan, failure, budget, reason_zh):
    positions = scan.open_positions_count
    orders = scan.open_orders_count
    requested = scan.requested_count_today
    if health_status == '需要人工核对':
        return '需要人工核对：存在锁定暂停、kill switch 或未消费模型结果'
    if health_status == '等待开市':
        return '等待开市：当前非连续竞价时段，开市后自动检查并启动'
    if health_status == '持仓风险处理':
        return f'持仓风险处理：持仓 {positions} 只，未成交订单 {orders} 笔，确定性止损/期限退出与订单轮询优先执行'
    if health_status == '预算限制':
        shown = requested if requested is not None else '未确认'
        return f'预算限制：今日预算请求 {shown} 次，已结算估值 {budget["settled_cny"]} 元'
    if health_status == '策略请求失败退避':
        next_txt = time_text(failure['next_allowed_request_at']) if failure['next_allowed_request_at'] else '待定'
        why = failure['last_failure_reason_zh'] or '原因未知'
        return f'策略请求失败退避：连续失败 {failure["consecutive_failures"]} 次（{why}），下次允许请求时间 {next_txt}'
    if health_status == '行情不足':
        return '行情不足：分钟行情缺失或未达新鲜度门禁，暂不发起付费模型调用'
    succ_txt = time_text(failure['last_strategy_success_at']) if failure['last_strategy_success_at'] else '今日尚无'
    return f'有效策略观望：有效计划 {scan.active_plan_count} 份（最近成功：{succ_txt}），{reason_zh or "等待可执行条件"}'


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
    now_bj, day_str, start_bj, stop_bj, window_lo, window_hi = _bounds(now)
    session_open = _session_open(now_bj, session_open)
    db_path = Path(database).resolve()
    progress = _load_progress(db_path, account_id, day_str, run_id)
    reason = progress.get('last_decision_reason')
    verdict = progress.get('last_guard_verdict')
    progress_blocker = progress.get('last_blocker_class')
    if progress_blocker == 'data_reject':
        # Health boundary re-classification (review R2 round 3): progress
        # files written before the closed whitelist may carry raw subtype
        # text; data-channel reasons collapse to the whitelist constants.
        reason = classify_not_fresh_reason(reason)
    scan = _scan_database(
        db_path, account_id, day_str, start_bj, stop_bj, now_bj, window_lo, window_hi, reason)
    failure = _failure_fields(scan)
    failure['last_failure_reason_zh'] = _failure_reason_zh(
        failure['last_failure_category'], failure['last_failure_reason'])
    fast_eval_seconds = getattr(t0_cfg, 'fast_eval_seconds', None)
    remaining, next_eligible = _cooldown(scan.cooldown_last_run_at, fast_eval_seconds, now_bj)
    backoff_active = _backoff_active(
        failure['consecutive_failures'], failure['next_allowed_request_at'], now_bj)
    budget = _budget_view(
        scan.budget_meta, t0_cfg, scan.requested_count_today,
        failure['last_failure_category'], scan.last_decision_reason)
    insufficient = _data_insufficient(progress, verdict, scan.last_decision_reason)
    if progress_blocker in _PROGRESS_BLOCKERS:
        blocker = progress_blocker
    else:
        blocker = blocker_class(verdict, scan.last_decision_reason, insufficient)
    blocker_text = BLOCKER_CLASS_TEXT.get(blocker) if blocker else None
    risk_exit_today = bool(progress.get('risk_exit_active'))
    unconsumed = bool(progress.get('unconsumed_result') or progress.get('worker_active'))
    health_status = _health_status(
        halted, scan.kill_switch_engaged, unconsumed, session_open, scan, risk_exit_today,
        scan.last_decision_reason, failure['consecutive_failures'], budget['exhausted'], insufficient)
    reason_zh = explanation(scan.last_decision_reason) if scan.last_decision_reason else None
    status_message_zh = _status_message(health_status, scan, failure, budget, reason_zh)
    claim = scan.cooldown_claim or None
    return {
        'account_id': account_id,
        'date': day_str,
        'as_of': now_bj.isoformat(),
        'run_id': run_id or progress.get('run_id'),
        'session_open': bool(session_open),
        'health_status': health_status,
        'status_message_zh': status_message_zh,
        'last_strategy_success_at': failure['last_strategy_success_at'],
        'active_plan_count': scan.active_plan_count,
        'consecutive_failures': failure['consecutive_failures'],
        'last_failure_at': failure['last_failure_at'],
        'last_failure_category': failure['last_failure_category'],
        'last_failure_reason': failure['last_failure_reason'],
        'last_failure_reason_zh': failure['last_failure_reason_zh'],
        'backoff_active': backoff_active,
        'backoff_seconds': failure['backoff_seconds'],
        'next_allowed_request_at': failure['next_allowed_request_at'],
        'requested_count_today': scan.requested_count_today,
        'audit_rows_today': scan.audit_rows_today,
        'audit_attempts_today': scan.audit_attempts_today,
        'strategy_calls_today': scan.strategy_calls_today,
        'count_semantics': 'requested_count_today=http_budget_requests;audit_rows_today=logical_calls;audit_attempts_today=sum_of_call_attempts',
        'budget': {
            'day': day_str,
            'used': scan.requested_count_today,
            'limit': budget['budget_limit'],
            'strategy_used': budget['strategy_used'],
            'strategy_limit': budget['strategy_limit'],
            'probe_used': budget['probe_used'],
            'jev_used': budget['jev_used'],
            'cost_settled_cny': budget['settled_cny'],
            'cost_pending_cny': budget['pending_cny'],
            'cost_reserved_cny': budget['reserved_cny'],
        },
        'open_positions_count': scan.open_positions_count,
        'open_orders_count': scan.open_orders_count,
        'last_decision_reason': scan.last_decision_reason,
        'last_decision_reason_zh': reason_zh,
        'last_blocker_class': blocker,
        'last_blocker_class_zh': blocker_text,
        'last_instrument_id': progress.get('last_instrument_id'),
        'last_data_age_seconds': progress.get('last_data_age_seconds'),
        'last_age_threshold_seconds': progress.get('last_age_threshold_seconds'),
        'last_missing_fields': progress.get('last_missing_fields'),
        'last_continuous_1m_bars': progress.get('last_continuous_1m_bars'),
        'fast_eval_seconds': fast_eval_seconds,
        'global_cooldown_last_claim_at': scan.cooldown_last_run_at,
        'global_cooldown_seconds_remaining': remaining,
        'global_cooldown_claim': claim,
        # Additive cooldown-claim completeness + no-actionable evidence
        # (t279u). The key/scope name the persisted claim row; all values are
        # read-only reports, none of them gate anything here.
        'global_cooldown_key': 'fast-eval:' + account_id,
        'global_cooldown_scope': 'account',
        'global_cooldown_next_eligible_at': next_eligible,
        'global_cooldown_claim_role': claim.get('role') if claim else None,
        'last_action_gate': progress.get('last_action_gate'),
        'last_model_path_claimed': progress.get('last_model_path_claimed'),
    }
