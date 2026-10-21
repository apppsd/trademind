"""CLI runner (TASK-22): local run entry points. Only verified commands are
documented in docs/project/operations.md.

    python -m trademind.runner status
    python -m trademind.runner demo-decision 600036.SH
    python -m trademind.runner poll-fills
    python -m trademind.runner replay-demo [--days 30]
    python -m trademind.runner serve
    python -m trademind.runner probe-llm     # verify DeepSeek key/model, redacted output
    python -m trademind.runner recover       # post-crash reconciliation report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .config import load_config
from .errors import SchemaVersionError
from .execution.writer_lock import WriterLockHeld
from .market.base import BEIJING
from .market.calendar import TradingCalendar

_config_path = None


def _load():
    return load_config(_config_path) if _config_path else load_config()


def _pipeline():
    from .pipeline import Pipeline

    cfg = _load()
    return Pipeline(cfg), cfg


def cmd_status() -> int:
    pipe, cfg = _pipeline()
    ks = pipe.repo.get_kill_switch()
    stub = pipe.llm_is_stub or pipe.jev_is_stub
    print(json.dumps({
        "mode": cfg.mode, "account": cfg.account_id,
        "kill_switch": ks,
        "llm": {"provider": pipe.llm.kind.value, "model": pipe.llm.model,
                "capability_verified": pipe.llm.capability_verified},
        "jev": {"provider": pipe.jev.kind.value,
                "capability_verified": pipe.jev.capability_verified},
        "stub_chain": stub,
        "universe": cfg.universe,
        "orchestrator": pipe.orchestrator.snapshot(),
        "llm_budget": {"used": pipe.llm.budget.used, "limit": pipe.llm.budget.limit},
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_decide(instrument: str) -> int:
    pipe, _ = _pipeline()
    pipe.ensure_account()
    res = pipe.run_decision(instrument, datetime.now(timezone.utc))
    print(json.dumps(res.summary(), ensure_ascii=False, indent=2))
    return 0 if res.error is None else 1


def cmd_poll() -> int:
    pipe, _ = _pipeline()
    filled = pipe.poll_fills(datetime.now(timezone.utc))
    print(json.dumps({"filled": filled}, ensure_ascii=False))
    return 0


def cmd_replay_demo(days: int) -> int:
    from .evaluation import ReplayRunner

    cfg = _load()
    cal = TradingCalendar.load()
    all_days = cal.window(datetime(2026, 8, 3).date(), datetime(2026, 9, 30).date())
    days = all_days[-days:]
    runner = ReplayRunner(cfg, ablation="full")
    report = runner.run(cfg.universe, days, Decimal("1000000"))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_serve() -> int:
    import uvicorn

    cfg = _load()
    from .api.app import init_app

    init_app(cfg)
    uvicorn.run("trademind.api.app:app", host=cfg.api.host, port=cfg.api.port)
    return 0


def cmd_probe_llm() -> int:
    """Minimal capability probe; prints redacted result, never the key."""
    cfg = _load()
    if cfg.llm.provider in ("stub",) or not cfg.llm.api_key:
        print(json.dumps({"llm": "stub (no key configured)"}))
        return 0
    from .models import DeepSeekProvider

    p = DeepSeekProvider(api_key=cfg.llm.api_key, model=cfg.llm.model,
                         base_url=cfg.llm.base_url, protocol=cfg.llm.protocol)
    result = p.probe()
    print(json.dumps({"model_configured": p.model,
                      "probe_ok": result.get("ok", False),
                      "models_seen": result.get("models", [])[:10],
                      "capability_verified": p.capability_verified,
                      "error": result.get("error")}, ensure_ascii=False))
    return 0 if p.capability_verified else 1


def cmd_recover() -> int:
    """Post-crash: reconcile UNKNOWN orders and rebuild the ledger."""
    pipe, cfg = _pipeline()
    report = pipe.oms.recover()
    pipe.ledger.rebuild_from_events()
    snap = pipe.ledger.snapshot(datetime.now(timezone.utc))
    print(json.dumps({"oms": report,
                      "ledger_rebuilt": True,
                      "account_version": snap.version,
                      "cash": str(snap.cash), "equity": str(snap.equity)},
                     ensure_ascii=False, indent=2))
    if report.get("unknown"):
        print("UNKNOWN orders exist: reconcile via oms.reconcile_unknown() before new risk",
              file=sys.stderr)
        return 2
    return 0


def _t0_instruments(cfg, instrument=None):
    from .market.profiles import get_profile
    selected = [instrument] if instrument else list(dict.fromkeys(cfg.universe))
    if not cfg.t0.enabled or not selected:
        raise ValueError('T0 configuration and nonempty universe required')
    for iid in selected:
        if iid not in cfg.universe or iid not in cfg.t0.whitelist or get_profile(iid).t_plus_days != 0:
            raise ValueError('instrument not in verified configured T0 pool')
    return selected


def cmd_t0_data(instrument=None):
    """Read-only free market probe; never load model credentials or providers."""
    from .market.router import DatasetRouter, MinuteRoutingPolicy
    from .context.features import minute_bars_visible_at
    try:
        cfg = load_config(_config_path, load_credentials=False)
        selected = _t0_instruments(cfg, instrument)
        router = DatasetRouter(pref=cfg.data.provider_pref, fixture_root=cfg.data.fixtures_dir or None,
                               minute_policy=MinuteRoutingPolicy.from_data_config(cfg.data))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__, 'reason': 'invalid T0 data configuration'}))
        return 1
    results = []
    for iid in selected:
        end = datetime.now(timezone.utc)
        try:
            bars, quality = router.get_minute_bars(iid, end-timedelta(days=2), end, period='1m')
            observed = datetime.now(timezone.utc)
            visible = minute_bars_visible_at(bars, observed)
            latest = visible[-1] if visible else None
            age = (observed-latest.bar_time).total_seconds() if latest else None
            fresh = latest is not None and age <= min(180, cfg.risk.quote_max_age_seconds)
            good = not quality.limit_violations
            results.append({'instrument': iid, 'status': 'ok' if fresh and good else 'stale' if latest and good else 'unavailable',
                            'bars': len(visible), 'latest_time': latest.bar_time.isoformat() if latest else None,
                            'latest_close': str(latest.close) if latest else None, 'age_seconds': age,
                            'provider': router.last_provider_by_dataset.get('minute_bars_1m'),
                            'observed_at': observed.isoformat()})
        except Exception as exc:
            results.append({'instrument': iid, 'status': 'error', 'error': type(exc).__name__,
                            'source_errors': getattr(router, 'last_errors_by_dataset', {}).get('minute_bars', [])})
    ok = all(r['status']=='ok' for r in results)
    print(json.dumps({'status': 'success' if ok else 'incomplete', 'model_calls': 0,
                      'monitored_instruments': selected, 'results': results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _public_decision(result):
    summary = result.summary()
    summary['context_id'] = result.context_id
    if summary.get('error'):
        summary['error'] = summary['error'].split(':', 1)[0]
    diagnostics = summary.get('diagnostics', {})
    summary['diagnostics'] = {k: diagnostics[k] for k in
        ('data_age_seconds', 'context_expires_at', 'missing_fields', 'budget', 'reason', 'strategy_health') if k in diagnostics}
    return summary


def _paper_steps(pipe, cfg, selected, steps, deadline, run=None):
    results = []
    for step in range(steps):
        if time.monotonic() >= deadline:
            return results, True
        if step:
            remaining = 5 if cfg.t0.profile == 'jev_fast_decision' else max(1, cfg.t0.quote_eval_interval_seconds)
            while remaining:
                wait = min(remaining, 60, max(0, deadline-time.monotonic()))
                if not wait:
                    return results, True
                time.sleep(wait)
                remaining -= wait
        # Warm live observations before taking decision as_of; reused by the
        # router's short cache. Never backdate fetched/available timestamps.
        ordered = selected
        if cfg.t0.profile == 'jev_fast_decision':
            held = {p.instrument_id for p in pipe.ledger.snapshot(datetime.now(timezone.utc)).positions}
            # Deterministic rotation among new candidates, holding review first.
            rotate = selected[step % len(selected):] + selected[:step % len(selected)]
            ordered = sorted(rotate, key=lambda iid: iid not in held)
        for iid in ordered:
            if time.monotonic() >= deadline:
                return results, True
            start_at = datetime.now(timezone.utc)
            try:
                pipe.router.get_minute_bars(iid, start_at-timedelta(days=2), start_at, period='1m')
            except Exception:
                pass  # Per-instrument decision records the actionable data error.
            now = datetime.now(timezone.utc)
            filled = pipe.poll_fills(now)
            res = pipe.run_decision(iid, now)
            filled += pipe.poll_fills(datetime.now(timezone.utc))
            results.append({'step': step+1, 'decision': _public_decision(res), 'filled': filled})
            if run is not None:
                run.record_progress(results)
    return results, False


def cmd_t0_paper(instrument: str | None = None, steps: int = 1, probe_models: bool = False,
                 report_output: str | None = None, max_seconds: int = 3600) -> int:
    """Bounded paper run with atomic audit artifact on every initialized exit."""
    from .pipeline import Pipeline
    from .execution.paper_run import PaperRun
    deadline = time.monotonic() + max_seconds
    try:
        cfg = _load()
        if not 1 <= steps <= 720 or not 1 <= max_seconds <= 3600 or not cfg.t0.enabled or cfg.mode != 'paper':
            raise ValueError('explicit bounded T0 paper configuration required')
        selected = _t0_instruments(cfg, instrument)
        pipe = Pipeline(cfg)
        run = PaperRun(pipe, selected, report_output)
    except WriterLockHeld as exc:
        # Second writer: zero calls and zero account writes happened here.
        print(json.dumps({'status': 'failed', 'error': 'WriterLockHeld',
                          'reason': str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__,
                          'reason': 'T0 configuration/provider initialization failed'}))
        return 1
    preflight, results, error = None, [], None
    status, code = 'failed', 1
    try:
        if cfg.t0.profile == 'jev_fast_decision':
            preflight = _fast_paper_preflight(pipe, selected, probe_models)
        if preflight is not None and not preflight['ok']:
            status, code = 'blocked', 2
        elif time.monotonic() >= deadline:
            status, code = 'incomplete', 2
            error = 'run_deadline_before_models'
        else:
            pipe.ensure_account(initial_cash=cfg.t0.initial_cash)
            checks = _probe_fast_models(pipe) if cfg.t0.profile == 'jev_fast_decision' and probe_models else {'ok': True}
            if not checks['ok']:
                preflight = {'free': preflight, 'models': checks}
            else:
                if cfg.t0.profile == 'jev_fast_decision':
                    pipe.enable_async_models()
                results, timed_out = _paper_steps(pipe, cfg, selected, steps, deadline, run=run)
                failed = any(r['decision']['error'] for r in results)
                verified = (not pipe.llm_is_stub and not pipe.jev_is_stub and
                            pipe.llm.capability_verified and pipe.jev.capability_verified)
                pending = bool(getattr(pipe, '_model_pending', None))
                if failed:
                    status, code = 'failed', 1
                elif timed_out or pending:
                    status, code = 'incomplete', 2
                elif verified:
                    status, code = ('ready' if cfg.t0.profile == 'jev_fast_decision' else 'success'), 0
                else:
                    status, code = 'unverified', 2
    except KeyboardInterrupt:
        status, code, error = 'incomplete', 130, 'interrupted'
    except Exception as exc:
        status, code, error = 'failed', 1, type(exc).__name__
    finally:
        shutdown = pipe.close_model_worker()
    if shutdown['worker_active']:
        status, code = 'incomplete', 2
    try:
        report = run.finish(status, code, results, preflight, shutdown, error)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as exc:
        code = 1
        print(json.dumps({'status': 'failed', 'run_id': run.run_id,
                          'error': type(exc).__name__, 'reason': 'run_report_write_failed'}))
    finally:
        if not shutdown['worker_active']:
            pipe.release_writer_lease()
            pipe.db.close()
    return code


def _check_fast_probe_budget(pipe):
    """Use the exact single-attempt probe payload bounds, without sending HTTP."""
    from .models.deepseek import chinese_output_system
    messages = [{'role': 'system', 'content': chinese_output_system('Return JSON only.')},
                {'role': 'user', 'content': 'Return {"ok":true}.'}]
    flash_bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 1024
    spec = pipe.jev.probe_input()
    payload = {'model': pipe.jev.model, 'state': spec['state'],
               'questions': pipe.jev.question_map(spec['questions'])}
    jev_bound = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1024
    return pipe.model_budget.check_probe_pair(flash_bound, jev_bound)


def _fast_paper_preflight(pipe, selected, probe_models):
    """Free readiness checks. Historical HTTP200 never grants capabilities."""
    from .context.builder import ContextBuilder
    from .models.jev import TypeSafeJEVProvider
    from .models.deepseek import DeepSeekProvider
    report = {'ok': False, 'blockers': [], 'data': [], 'model_calls': 0}
    rates = (pipe.cfg.t0.model_input_cny_per_million, pipe.cfg.t0.model_output_cny_per_million,
             pipe.cfg.t0.jev_input_cny_per_million, pipe.cfg.t0.jev_output_cny_per_million)
    if (any(v is None or not Decimal(str(v)).is_finite() for v in rates)
        or any(Decimal(str(v)) <= 0 for v in rates[:3])
        or Decimal(str(rates[3])) < 0):
        report['blockers'].append('configured_prices_invalid')
    if not pipe.calendar.is_session_open(datetime.now(timezone.utc)):
        report['blockers'].append('not_continuous_session')
    if probe_models:
        if (not isinstance(pipe.jev, TypeSafeJEVProvider) or not isinstance(pipe.llm, DeepSeekProvider)
            or pipe.llm.model != 'deepseek-flash' or pipe.jev.model != 'jev-1.13.0'
            or pipe.jev.base_url != 'https://api.typesafe.ai'):
            report['blockers'].append('official_real_models_required')
        elif not report['blockers']:
            if _same_day_probe_marker(pipe) is not None:
                # A valid same-day marker means this launch will skip the paid
                # probes entirely, so the probe-pair budget gate does not apply
                # (Task17-05: a valid marker must not be blocked by a spent
                # pair budget).
                report['probes'] = 'same_day_marker'
            else:
                try:
                    _check_fast_probe_budget(pipe)
                except Exception as exc:
                    # Budget exhaustion (pair/count/cost unavailable) is a distinct,
                    # non-transient terminal state for the day: the supervisor must
                    # back off instead of re-blocking every 5 minutes
                    # (TASK-20261016-02). Config/price errors are not budget.
                    reason = str(exc)
                    if 'unavailable' in reason or 'exhausted' in reason:
                        report['probe_budget_exhausted'] = True
                    report['blockers'].append('probe_pair_unavailable:' + type(exc).__name__)
    if report['blockers']:
        return report
    builder = ContextBuilder(router=pipe.router, calendar=pipe.calendar)
    for iid in selected:
        try:
            fetch_at = datetime.now(timezone.utc)
            pipe.router.get_minute_bars(iid, fetch_at-timedelta(days=2), fetch_at, period='1m')
            observed_at = datetime.now(timezone.utc)
            snap = builder.build_minute(iid, observed_at,
                max_stale_seconds=min(180, pipe.cfg.risk.quote_max_age_seconds))
            latest = (snap.aux.get('minute_bars') or [{}])[-1].get('time')
            report['data'].append({'instrument': iid, 'ok': not snap.missing_fields,
                'observed_at': observed_at.isoformat(), 'latest_bar': latest,
                'provider': (snap.aux.get('minute_bars') or [{}])[-1].get('provider'),
                'age_seconds': (observed_at-datetime.fromisoformat(latest)).total_seconds() if latest else None,
                'missing_fields': snap.missing_fields, 'feature_version': snap.features.feature_version})
        except Exception as exc:
            report['data'].append({'instrument': iid, 'ok': False, 'error': type(exc).__name__})
    if not any(item['ok'] for item in report['data']):
        report['blockers'].append('fresh_complete_minute_data_unverified')
    # A slow fetch may cross the closing boundary. Check again before paid HTTP.
    if not pipe.calendar.is_session_open(datetime.now(timezone.utc)):
        report['blockers'].append('not_continuous_session')
    report['ok'] = not report['blockers']
    return report


def _probe_marker_hash(pipe):
    """Hash of everything the same-day probe marker trusts: the sanitized
    runtime config PLUS the live provider endpoint/model identity - an LLM/JEV
    endpoint change must invalidate the marker (TASK-20261017-05). Never
    includes credentials."""
    from .execution.paper_run import public_runtime_config
    identity = {'runtime': public_runtime_config(pipe.cfg),
                'endpoints': {'llm_base_url': getattr(pipe.llm, 'base_url', ''),
                              'llm_model': getattr(pipe.llm, 'model', ''),
                              'jev_base_url': getattr(pipe.jev, 'base_url', ''),
                              'jev_model': getattr(pipe.jev, 'model', '')}}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _same_day_probe_marker(pipe):
    """The same-day verified probe marker for this exact configuration, or None.

    Valid only when: same Beijing day, same config+endpoint hash, verified=True
    (ARCH-022). Shared across processes by design - relaunches are new
    processes; invalidation is day change, any configuration/endpoint change,
    or a failed live probe (which clears the marker)."""
    now_bj = datetime.now(BEIJING)
    marker = pipe.repo.get_coalesce('fast-probe-ok:' + pipe.cfg.account_id)
    meta = marker['meta'] if marker else {}
    if meta.get('day') != now_bj.date().isoformat() or meta.get('verified') is not True:
        return None
    if meta.get('config_hash') != _probe_marker_hash(pipe):
        return None
    return meta


def _probe_fast_models(pipe):
    from .domain.base import new_id
    from .models.jev import TypeSafeJEVProvider
    from .models.deepseek import DeepSeekProvider
    if not isinstance(pipe.jev, TypeSafeJEVProvider) or not isinstance(pipe.llm, DeepSeekProvider):
        return {'ok': False, 'reason': 'real providers required'}
    repair_attempts = pipe.llm.max_repair_attempts
    marker_key = 'fast-probe-ok:' + pipe.cfg.account_id
    day = datetime.now(BEIJING).date().isoformat()
    cfg_hash = _probe_marker_hash(pipe)
    if _same_day_probe_marker(pipe) is not None:
        # Same-day probe pair already verified for this exact configuration
        # (ARCH-022): skip the paid probes instead of re-burning the daily
        # pair budget on every relaunch. Trust scope: same Beijing day, same
        # config hash, same account; a mid-session vendor break surfaces as a
        # loud budget-audited strategy-call failure. Expired by day change or
        # any configuration change.
        pipe.llm.capability_verified = pipe.jev.capability_verified = True
        return {'ok': True, 'flash_ok': True,
                'jev': {'ok': True, 'same_day_marker': True},
                'probes': 'same_day_marker', 'budget': pipe.model_budget.snapshot()}
    pipe.llm.capability_verified = pipe.jev.capability_verified = False
    try:
        _check_fast_probe_budget(pipe)
        # Startup probes have exactly one attempt per model; repair retries
        # would invalidate the upfront pair count/cost check.
        pipe.llm.max_repair_attempts = 0
        output = pipe.llm.complete(role='flash_probe', system='Return JSON only.',
            user='Return {"ok":true}.', trace_id=new_id('probe'), timeout_s=15)
        pipe.llm.capability_verified = output.parsed == {'ok': True}
        jev = pipe.jev.probe() if pipe.llm.capability_verified else {'ok': False}
        ok = pipe.llm.capability_verified and jev['ok']
        if ok:
            pipe.repo.save_coalesce(marker_key, datetime.now(timezone.utc),
                                    {'day': day, 'config_hash': cfg_hash, 'verified': True})
        else:
            # A failed live probe invalidates any same-day marker: the vendor
            # is demonstrably broken now, so the next relaunch must re-probe.
            pipe.repo.save_coalesce(marker_key, datetime.now(timezone.utc),
                                    {'day': day, 'config_hash': cfg_hash, 'verified': False})
        return {'ok': ok, 'flash_ok': pipe.llm.capability_verified, 'jev': jev,
                'probes': 'live', 'budget': pipe.model_budget.snapshot()}
    except Exception as exc:
        pipe.repo.save_coalesce(marker_key, datetime.now(timezone.utc),
                                {'day': day, 'config_hash': cfg_hash, 'verified': False})
        return {'ok': False, 'reason': type(exc).__name__, 'probes': 'live',
                'budget': pipe.model_budget.snapshot()}
    finally:
        pipe.llm.max_repair_attempts = repair_attempts


def cmd_t0_fast_check():
    """Credential-free configuration/price/session preflight, never model calls."""
    from .config import load_config
    from .market.calendar import TradingCalendar
    from .domain.base import utcnow
    cfg=load_config(_config_path, load_credentials=False)
    rates=(cfg.t0.model_input_cny_per_million, cfg.t0.model_output_cny_per_million,
           cfg.t0.jev_input_cny_per_million, cfg.t0.jev_output_cny_per_million)
    from decimal import Decimal
    configured=(all(v is not None and Decimal(str(v)).is_finite() for v in rates)
                and rates[0]>0 and rates[1]>0 and rates[2]>0 and rates[3]>=0)
    cal = TradingCalendar.load()
    now = utcnow()
    session_open = cal.is_session_open(now)
    covered = cal.covers(now.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date())
    warnings = cal.coverage_warnings(now.date())
    ready = (configured and session_open and covered
             and cfg.t0.enabled and cfg.mode == 'paper' and cfg.t0.profile == 'jev_fast_decision')
    print(json.dumps({'profile':cfg.t0.profile,'mode':cfg.mode,'account_id':cfg.account_id,
        'configured_prices':configured,'session_open':session_open,
        'calendar_coverage':cal.coverage(),'calendar_warnings':warnings,
        'status':'configuration_ready_data_unverified' if ready else 'blocked',
        'model_calls':0,'orders':0,'credentials_loaded':False},ensure_ascii=False))
    return 0 if ready else 2

def cmd_market_data(markets=None):
    from pathlib import Path
    import os
    from .market.multi_asset import MultiAssetMarket
    try:
        cfg = load_config(_config_path, load_credentials=False)
        if markets:
            cfg.market_access.markets = markets.split(',')
        report = MultiAssetMarket(cfg.market_access).scan()
        target = Path('data/runtime/market-observation.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, target)
        summary = {key: value for key, value in report.items() if key != 'assets'}
        grouped = {}
        for source in report.get('sources', []):
            item = grouped.setdefault(source['market'], {'market': source['market'], 'count': 0, 'statuses': {}, 'errors': {}})
            item['count'] += source.get('count', 0)
            state = source['status']
            item['statuses'][state] = item['statuses'].get(state, 0) + 1
            if source.get('error_type'):
                error = source['error_type']
                item['errors'][error] = item['errors'].get(error, 0) + 1
        summary['sources'] = list(grouped.values())
        summary['report_path'] = str(target.resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'success' else 1
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'model_calls': 0, 'error_type': type(exc).__name__}))
        return 1


def cmd_market_bars(instrument, kind, period):
    from .market.multi_asset import MultiAssetMarket
    try:
        cfg = load_config(_config_path, load_credentials=False)
        report = MultiAssetMarket(cfg.market_access).minute_bars(instrument, kind, period)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'success' else 1
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'model_calls': 0, 'error_type': type(exc).__name__}))
        return 1


def cmd_market_select(top_k: int = 1, max_total: int = 3) -> int:
    from pathlib import Path
    from .market.multi_asset import MultiAssetMarket
    from .market.selector import MultiAssetCandidateSelector
    try:
        cfg = load_config(_config_path, load_credentials=False)
        target = Path('data/runtime/market-observation.json')
        if target.exists():
            report = json.loads(target.read_text(encoding='utf-8'))
            assets = report.get('assets', [])
        else:
            market = MultiAssetMarket(cfg.market_access)
            report = market.scan()
            assets = report.get('assets', [])
        selector = MultiAssetCandidateSelector(cfg.market_access, top_k_per_market=top_k, max_total_candidates=max_total)
        result = selector.select(assets)
        from dataclasses import asdict
        payload = asdict(result)
        payload['model_calls'] = 0
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.status == 'success' else 1
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'model_calls': 0, 'error_type': type(exc).__name__}))
        return 1


def main(argv=None) -> int:
    global _config_path
    parser = argparse.ArgumentParser("trademind")
    parser.add_argument('--config', help='TOML shared by run/status/serve/recover')
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_dec = sub.add_parser("demo-decision")
    p_dec.add_argument("instrument")
    sub.add_parser("poll-fills")
    p_rep = sub.add_parser("replay-demo")
    p_rep.add_argument("--days", type=int, default=40)
    sub.add_parser("serve")
    sub.add_parser("probe-llm")
    sub.add_parser("recover")
    p_t0 = sub.add_parser("t0-paper")
    p_t0.add_argument("--instrument", help='One configured ETF; omitted monitors the full configured pool')
    p_t0.add_argument("--steps", type=int, default=1)
    p_t0.add_argument("--report-output")
    p_t0.add_argument("--max-seconds", type=int, default=3600)
    p_t0.add_argument("--probe-models", action="store_true", help="Bounded real probes in this process before fast-profile decisions")
    sub.add_parser("t0-fast-check", help="Credential-free configuration/price/session preflight")
    p_cmp = sub.add_parser('t0-fast-compare', help='Network-free archived intent comparison')
    p_cmp.add_argument('--bundle', required=True)
    p_cmp.add_argument('--output', required=True)
    p_data = sub.add_parser('t0-data', help='Free data check only, no model calls')
    p_data.add_argument('--instrument')
    p_market = sub.add_parser('market-data', help='All-market discovery and account eligibility; no models/orders')
    p_market.add_argument('--markets', help='Comma-separated etf,lof,convertible_bond,hk_stock,futures')
    p_bars = sub.add_parser('market-bars', help='On-demand multi-market minute observations; no models/orders')
    p_bars.add_argument('instrument')
    p_bars.add_argument('--kind', required=True, choices=['etf', 'lof', 'convertible_bond', 'hk_stock', 'futures'])
    p_bars.add_argument('--period', default='1', choices=['1', '5', '15', '30', '60'])
    p_select = sub.add_parser('market-select', help='Bounded candidate filtration and ranking across markets')
    p_select.add_argument('--top-k', type=int, default=1)
    p_select.add_argument('--max-total', type=int, default=3)
    p_report = sub.add_parser('daily-report', help='Read-only daily cash/trades/model cost report')
    p_report.add_argument('--date', help='Shanghai YYYY-MM-DD; defaults to today')
    args = parser.parse_args(argv)
    _config_path = args.config
    try:
        return _dispatch(args)
    except WriterLockHeld as exc:
        # Any writer entry point losing the lease fails loudly, having made
        # zero provider calls and zero account writes.
        print(json.dumps({'status': 'failed', 'error': 'WriterLockHeld',
                          'reason': str(exc)}, ensure_ascii=False))
        return 1
    except SchemaVersionError as exc:
        # A database from a newer program: refuse loudly instead of opening,
        # migrating or "repairing" what this version cannot understand.
        print(json.dumps({'status': 'failed', 'error': 'SchemaVersionError',
                          'reason': str(exc)}, ensure_ascii=False))
        return 1


def _dispatch(args) -> int:
    if args.cmd == 'daily-report':
        from .reports.daily import generate_for_config
        try:
            cfg = load_config(args.config, load_credentials=False) if args.config else load_config(load_credentials=False)
            report, output = generate_for_config(cfg, args.date)
            print(json.dumps({'status': 'generated', 'date': report['date'], 'html': output}))
            return 0
        except Exception as exc:
            print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
            return 1
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "demo-decision":
        return cmd_decide(args.instrument)
    if args.cmd == "poll-fills":
        return cmd_poll()
    if args.cmd == "replay-demo":
        return cmd_replay_demo(args.days)
    if args.cmd == "serve":
        return cmd_serve()
    if args.cmd == "probe-llm":
        return cmd_probe_llm()
    if args.cmd == "recover":
        return cmd_recover()
    if args.cmd == "t0-paper":
        return cmd_t0_paper(args.instrument, args.steps, args.probe_models, args.report_output, args.max_seconds)
    if args.cmd == 't0-fast-compare':
        from .evaluation.fast_comparison import compare_bundle_file
        from pathlib import Path
        report = compare_bundle_file(args.bundle)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'current_model_calls': 0, 'output': str(destination)}))
        return 0 if report['status']=='archived_intents_replayed' else 2
    if args.cmd == 't0-fast-check':
        return cmd_t0_fast_check()
    if args.cmd == 't0-data':
        return cmd_t0_data(args.instrument)
    if args.cmd == 'market-data':
        return cmd_market_data(args.markets)
    if args.cmd == 'market-bars':
        return cmd_market_bars(args.instrument, args.kind, args.period)
    if args.cmd == 'market-select':
        return cmd_market_select(args.top_k, args.max_total)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
