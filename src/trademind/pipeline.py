"""End-to-end decision pipeline: context → research → JEV → trader →
AI risk → portfolio AI → hard guard → paper OMS (ARCH-001 data flow).

Ablation switches (no_jev / no_memory) exist for evaluation only; production
runs use the full chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from copy import deepcopy
import threading
from decimal import Decimal
from typing import Any

from .agents import AIRiskReviewer, JEVGate, PortfolioAI, ResearchPipeline, Trader
from .config import AppConfig, _validate_mode_isolation
from .orchestrator.scheduler import Orchestrator
from .context import ContextBuilder
from .domain.base import new_id, utcnow
from .domain.context import ContextSnapshot
from .domain.enums import TradeAction, RunMode
from .domain.intents import PortfolioIntent, RiskReview, TradeIntent, create_exit_portfolio_intent
from .domain.judgments import JudgmentBatch
from .domain.research import TradeThesis
from .errors import CalendarCoverageError, GuardVeto, NotFresh, ProviderError
from .execution import OMS, PaperBroker, PaperConfig
from .guard import Guard
from .guard.guard import check_stop_loss_exit, check_session_close_exit
from .market.base import BEIJING
from .market.adapters.fixture import FixtureAdapter
from .market.calendar import TradingCalendar
from .market.profiles import get_profile
from .market.router import DatasetRouter, MinuteRoutingPolicy
from .memory import MemoryStore, OutcomeService, Reflector
from .models import build_jev, build_llm
from .portfolio import Ledger
from .storage import AuditSink, Database, Repository


@dataclass
class DecisionResult:
    trace_id: str
    instrument_id: str
    as_of: datetime
    context_id: str
    thesis: TradeThesis | None = None
    judgments: JudgmentBatch | None = None
    intent: TradeIntent | None = None
    review: RiskReview | None = None
    portfolio_intent: PortfolioIntent | None = None
    guard_verdict: str | None = None
    guard_reasons: list[str] = field(default_factory=list)
    order_id: str | None = None
    qty: Decimal | None = None
    error: str | None = None
    is_stub_chain: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "instrument": self.instrument_id,
            "as_of": self.as_of.isoformat(),
            "action": self.intent.action.value if self.intent else self.portfolio_intent.action.value if self.portfolio_intent else None,
            "target_weight": str(self.intent.target_weight) if self.intent and self.intent.target_weight is not None else str(self.portfolio_intent.target_weight) if self.portfolio_intent and self.portfolio_intent.target_weight is not None else None,
            "guard": self.guard_verdict,
            "guard_reasons": self.guard_reasons,
            "order_id": self.order_id,
            "stub": self.is_stub_chain,
            "error": self.error,
            "diagnostics": self.diagnostics,
        }


class Pipeline:
    def __init__(self, cfg: AppConfig, db: Database | None = None,
                 mode: str | None = None, ablation: str = "full", clock=None) -> None:
        cfg = deepcopy(cfg)
        cfg.mode = mode or cfg.mode
        if cfg.mode == "replay" and cfg.data.provider_pref == AppConfig().data.provider_pref:
            cfg.data.provider_pref = ["fixture"]
        _validate_mode_isolation(cfg)
        if ablation != "full" and cfg.mode != "replay":
            raise ValueError("ablations are restricted to replay")
        self.clock = clock
        self.orchestrator = Orchestrator(max_concurrent=1)
        self._drain_lock = threading.RLock()
        self.cfg = cfg
        # Single cross-process writer lease per (database, account). Taken
        # before the database exists, so the losing writer makes zero calls
        # and zero writes; kernel release on process death covers crashes
        # (ISSUE-20261013-06). Callers injecting their own Database coordinate
        # themselves (in-process evaluation/test scenarios only).
        self._writer_lease = None
        if db is None:
            from .execution.writer_lock import acquire_writer_lease
            self._writer_lease = acquire_writer_lease(cfg.storage.url, cfg.account_id)
        self.db = db or Database(cfg.storage.url)
        self.repo = Repository(self.db)
        self.audit = AuditSink(self.db)
        self.mode = mode or cfg.mode
        self.ablation = ablation  # full | no_jev | no_memory (evaluation only)
        self.calendar = TradingCalendar.load()
        self.router = DatasetRouter(
            pref=cfg.data.provider_pref, fixture_root=cfg.data.fixtures_dir or None, tushare_token=cfg.data.tushare_token,
            minute_policy=MinuteRoutingPolicy.from_data_config(cfg.data),
            audit_cb=lambda what, note: self.audit.record("data", "route", what))
        fees = get_profile(cfg.universe[0]).fees if cfg.universe else None
        self.ledger = Ledger(self.db, cfg.account_id, fees)
        self.memory = MemoryStore(self.repo)
        self.outcomes = OutcomeService(self.repo, self.router, self.calendar)
        self.llm, self.llm_is_stub = build_llm(cfg, self.repo)
        self.jev, self.jev_is_stub = build_jev(cfg, self.repo)
        if cfg.t0.enabled:
            from .models.daily_budget import DailyModelBudget
            self.model_budget = DailyModelBudget(self.repo, cfg.t0, cfg.account_id,
                                                 clock=self.clock or utcnow)
            self.llm.budget = self.model_budget
            self.jev.budget = self.model_budget
        if ablation == "no_memory":
            memory_view = None
        else:
            memory_view = lambda iid, t: self.memory.visible(t, iid)  # noqa: E731
        self.context_builder = ContextBuilder(
            self.router, self.calendar, self.ledger,
            ttl_seconds=cfg.risk.context_ttl_seconds,
            universe_version=cfg.universe_version, memory_view=memory_view,
        )
        self.research = ResearchPipeline(self.llm, self.repo)
        self.jev_gate = JEVGate(self.jev, self.repo,
                                question_version=cfg.jev.question_version)
        self.trader = Trader(self.llm, self.repo,
                             context_ttl_seconds=cfg.risk.intent_ttl_seconds)
        self.risk_reviewer = AIRiskReviewer(self.llm, self.repo,
                                            max_position_weight=cfg.risk.max_position_weight)
        self.portfolio_ai = PortfolioAI(self.llm, self.repo, mode=self.mode)
        self.guard = Guard(
            self.repo, self.ledger, cfg.risk, self.mode, cfg.universe, self.audit,
            day_start_equity_provider=self._adjusted_day_start,
            valuation_complete_provider=lambda: not self._valuation_failures,
            t0_config=getattr(cfg, "t0", None),
        )
        self.broker = PaperBroker(self.router, self.calendar, fees,
                                  PaperConfig(slippage_bps=cfg.paper.slippage_bps,
                                              fill_lag_bars=cfg.paper.fill_lag_bars,
                                              minute_only=cfg.t0.enabled))
        self.oms = OMS(self.repo, self.ledger, self.broker, self.audit, mode=self.mode)
        self.oms.recover()
        if cfg.t0.enabled and cfg.t0.profile == 'jev_fast_decision':
            self._recover_holding_deadlines()
        self._day_start_equity = Decimal("0")
        self._day_start_seq = 0
        self._valuation_failures: list[str] = []
        if cfg.t0.enabled:
            self.guard.sync_counters((self.clock or utcnow)())

    def release_writer_lease(self) -> None:
        """Give up the account writer lease (e.g. before a same-process
        restart hands the account to a new Pipeline)."""
        if self._writer_lease is not None:
            self._writer_lease.release()
            self._writer_lease = None

    def _adjusted_day_start(self):
        flows = sum((Decimal(e['amount']) * (1 if e['kind'] == 'deposit' else -1)
                     for e in self.repo.account_events(self.cfg.account_id)
                     if e['seq'] > self._day_start_seq and e['kind'] in ('deposit', 'withdraw')), Decimal('0'))
        return self._day_start_equity + flows

    # -- account boot ---------------------------------------------------------
    def ensure_account(self, initial_cash: Decimal | None = None,
                       at: datetime | None = None) -> None:
        if self.repo.load_account_state(self.cfg.account_id) is None:
            self.ledger.deposit(initial_cash or self.cfg.paper.initial_cash, at)

    def on_new_trading_day(self, d, prices: dict[str, Decimal] | None = None,
                           prices_complete: bool = True) -> None:
        if self.cfg.t0.enabled:
            self.guard.sync_counters((self.clock or utcnow)())
        previous = self.repo.get_coalesce("day-baseline:" + self.cfg.account_id)
        if previous and previous["meta"].get("day") == d.isoformat():
            self._day_start_equity = Decimal(previous["meta"]["equity"])
            self._day_start_seq = previous['meta'].get('seq', 0)
            self.ledger.on_new_trading_day(d)
            return
        if not prices_complete:
            # Incomplete quotes must not fabricate an equity baseline from cost
            # or stale marks (ISSUE-20261013-05); keep the prior baseline and
            # still perform the calendar T+1 rollover, which needs no prices.
            self.ledger.on_new_trading_day(d)
            return
        snap = self.ledger.snapshot(at=utcnow(), prices=prices)
        self._day_start_equity = snap.equity
        self.ledger.on_new_trading_day(d)
        self._day_start_seq = max((e['seq'] for e in self.repo.account_events(self.cfg.account_id)), default=0)
        self.repo.save_coalesce("day-baseline:" + self.cfg.account_id, utcnow(),
                                {"day": d.isoformat(), "equity": str(snap.equity), 'seq': self._day_start_seq})

    # -- the decision chain ------------------------------------------------------
    def reference_price(self, instrument_id: str, as_of: datetime) -> Decimal:
        if self.cfg.t0.enabled:
            from .context.features import minute_bars_visible_at
            bars, report = self.router.get_minute_bars(
                instrument_id, as_of - timedelta(days=2), as_of, period="1m",
                as_of=as_of, quote_max_age_seconds=min(180, self.cfg.risk.quote_max_age_seconds))
            if getattr(report, 'limit_violations', None):
                raise NotFresh('minute reference quote quality validation failed')
            bars = minute_bars_visible_at(bars, as_of)
            max_age = min(180, self.cfg.risk.quote_max_age_seconds)
            if not bars or (as_of - bars[-1].bar_time).total_seconds() > max_age:
                raise NotFresh(f"no fresh visible minute quote for {instrument_id}")
            return bars[-1].close
        try:
            end = self.calendar.last_on_or_before(as_of)
            start = self.calendar.prev(end, 5)
        except CalendarCoverageError as exc:
            # Outside verified calendar coverage no reference price can be
            # proven current; treat like any other freshness failure.
            raise NotFresh(f"trading calendar coverage insufficient: {exc}") from exc
        bars, _ = self.router.get_daily_bars(instrument_id, start, end)
        bars = [b for b in bars if b.available_at <= as_of and b.source_time <= as_of]
        if not bars:
            raise NotFresh(f"no reference price for {instrument_id}")
        return bars[-1].close

    def latest_valid_thesis(self, instrument_id: str, as_of: datetime) -> TradeThesis | None:
        rows = self.repo.db.query(
            "SELECT t.payload_json, t.valid_until FROM theses t JOIN context_snapshots c ON t.context_id=c.context_id "
            "WHERE t.instrument_id=? AND c.as_of<=? ORDER BY t.rowid DESC", (instrument_id, as_of.isoformat()))
        for r in rows:
            if datetime.fromisoformat(r["valid_until"]) >= as_of:
                thesis = TradeThesis.model_validate_json(r["payload_json"])
                if thesis.is_valid_at(as_of):
                    return thesis
        return None

    def run_decision(self, instrument_id: str, as_of: datetime,
                     force_research: bool = False) -> DecisionResult:
        payload = {"as_of": as_of, "force_research": force_research,
                   "deadline": as_of + timedelta(seconds=self.cfg.risk.context_ttl_seconds)}
        kind = "holding_review" if any(p.instrument_id == instrument_id for p in self.ledger.snapshot(as_of).positions) else "research"
        accepted = self.orchestrator.submit(kind, instrument_id, payload, now=self._now(as_of))
        if accepted is None:
            return DecisionResult(new_id("tr"), instrument_id, as_of, "", error="trigger coalesced or queue full")
        def handler(task):
            p = task.payload
            p["result"] = self._run_decision(task.instrument_id, p["as_of"], p["force_research"])
            return p["result"].error is None
        with self._drain_lock:
            while "result" not in payload and not payload.get("dropped"):
                if not self.orchestrator.run_once(handler, now=self._now(as_of)):
                    break
        if "result" in payload:
            return payload["result"]
        return DecisionResult(new_id("tr"), instrument_id, as_of, "", error="queue deadline expired")

    def _now(self, as_of):
        return self.clock() if self.clock else (as_of if self.mode == "replay" else utcnow())

    def enable_async_models(self):
        """One model worker; caller remains the only Guard/OMS writer."""
        from concurrent.futures import ThreadPoolExecutor
        self._model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='fast-model')
        self._model_pending = None

    def close_model_worker(self, timeout_s=35):
        """Drain the async model worker into an explicit, audited terminal state.

        The drain never executes a result. A finished result gets a terminal
        classification so a normal close no longer latches the protective
        halt (ISSUE-20261013-03); only an active worker or an outcome that
        cannot be classified keeps ``unconsumed_result`` set.
        """
        from concurrent.futures import wait
        pending = getattr(self, '_model_pending', None)
        budget = getattr(self, 'model_budget', None)
        if budget is not None:
            budget.close_requests()
        active = False
        terminal, detail = 'no_pending_result', None
        if pending:
            future, source = pending
            if future.cancel():
                # Never started: no model call was made and no result exists.
                terminal = 'cancelled_before_start'
            else:
                _, unfinished = wait([future], timeout=max(0, min(timeout_s, 35)))
                active = bool(unfinished)
                terminal, detail = (('worker_still_active', None) if active
                                    else self._classify_drained_result(future, source))
            self._model_pending = None
        executor = getattr(self, '_model_executor', None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if pending and not self._audit_drain(terminal, detail):
            # An unaudited recovery is not a safe recovery; keep the halt.
            terminal = 'audit_write_failed'
        unknown = terminal in ('worker_still_active', 'unknown_result_shape',
                               'unknown_result_state', 'audit_write_failed')
        return {'unconsumed_result': bool(unknown), 'worker_active': active,
                'new_requests_closed': budget is not None,
                'drain_terminal_state': terminal, 'drain_detail': detail}

    def _classify_drained_result(self, future, source):
        """Terminal state of a finished model result; classification only."""
        exc = future.exception()
        if exc is not None:
            return 'model_error', {'error_type': type(exc).__name__}
        outcome = future.result()
        if not (isinstance(outcome, tuple) and len(outcome) == 2
                and hasattr(outcome[0], 'action') and hasattr(outcome[0], 'valid_until')):
            return 'unknown_result_shape', {'result_type': type(outcome).__name__}
        intent, _meta = outcome
        detail = {'instrument_id': source.instrument_id, 'action': intent.action.value,
                  'trace_id': intent.trace_id, 'valid_until': intent.valid_until.isoformat()}
        try:
            now = self.clock() if self.clock else utcnow()
            if now >= intent.valid_until or self.ledger.snapshot(now).version != intent.portfolio_version:
                detail['reason'] = 'expired_or_account_changed'
                return 'expired_result_not_executed', detail
            # Still valid: the drain never executes it. Deterministic risk exits
            # keep covering any holding and the cached plan re-evaluates next poll.
            detail['reason'] = 'drain_never_executes'
            return 'valid_result_not_executed', detail
        except Exception as exc:  # noqa: BLE001
            detail['reason'] = type(exc).__name__
            return 'unknown_result_state', detail

    def _audit_drain(self, terminal, detail):
        try:
            target = str((detail or {}).get('trace_id') or 'drain')
            self.audit.record('decision', 'model_drain', target,
                              {'terminal_state': terminal, **({'detail': detail} if detail else {})})
            return True
        except Exception:  # noqa: BLE001
            return False

    def _fast_model_result(self, snap, trace_id):
        if not hasattr(self, '_model_executor'):
            return self._fast_pipeline.run(snap, trace_id), snap
        pending = self._model_pending
        if pending:
            future, source = pending
            if not future.done() or source.instrument_id != snap.instrument_id:
                return None, snap
            self._model_pending = None
            intent, meta = future.result()
            now = self._now(snap.as_of)
            if now >= intent.valid_until or self.ledger.snapshot(now).version != intent.portfolio_version:
                raise NotFresh('async model result expired or account changed')
            if intent.action in (TradeAction.BUY, TradeAction.SELL):
                from .domain.strategy import StrategyPlan
                from .context.fast_state import conditions_allow
                plan = StrategyPlan.model_validate(meta['state']['plan'])
                profile = get_profile(snap.instrument_id)
                if (not plan.valid_at(now, snap.instrument_id, profile.profile_id)
                    or any(c.matches(snap.features.values) for c in plan.invalidation_conditions)
                    or not conditions_allow(intent.action, plan, snap.features.values)):
                    raise NotFresh('async strategy conditions changed before execution')
            return (intent, meta), source
        self._model_pending = (self._model_executor.submit(self._fast_pipeline.run, snap.model_copy(deep=True), trace_id), snap)
        return None, snap

    def _run_decision(self, instrument_id, as_of, force_research=False):
        trace_id = new_id("tr")
        result = DecisionResult(trace_id=trace_id, instrument_id=instrument_id,
                                as_of=as_of, context_id="")
        result.is_stub_chain = self.llm_is_stub or self.jev_is_stub
        try:
            if self.repo.get_kill_switch()["engaged"]:
                result.guard_verdict = "VETO"
                result.guard_reasons = ["kill_switch"]
                return result
            if self._now(as_of) < as_of:
                raise NotFresh("context as_of is in the future")
            if any(datetime.fromisoformat(e['effective_at']) > as_of for e in self.repo.account_events(self.cfg.account_id)):
                raise NotFresh("account contains future events; historical decisions require a fresh ReplayRunner")
            self.oms.expire_approvals(self._now(as_of))
            # Value each position independently: one symbol's stale or failed
            # quote must not block deterministic risk exits for the healthy
            # symbols (ISSUE-20261013-05). New risk (BUY) is vetoed by the
            # Guard while any position lacks a fresh legal price.
            valuation: dict[str, Decimal] = {}
            valuation_failures: list[str] = []
            for p in self.ledger.snapshot(as_of).positions:
                try:
                    valuation[p.instrument_id] = self.reference_price(p.instrument_id, as_of)
                except (NotFresh, ProviderError) as exc:
                    valuation_failures.append(f"{p.instrument_id} ({type(exc).__name__}: {exc})")
            self._valuation_failures = valuation_failures
            if valuation_failures:
                # Attach before the context build so the record survives any
                # later failure in this decision (ISSUE-20261013-05).
                result.diagnostics['valuation_failures'] = valuation_failures
            self.on_new_trading_day(as_of.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date(),
                                    valuation, prices_complete=not valuation_failures)
            if self.cfg.t0.enabled:
                snap = self.context_builder.build_minute(
                    instrument_id, as_of,
                    max_stale_seconds=min(180, self.cfg.risk.quote_max_age_seconds))
                last = (snap.aux.get('minute_bars') or [{}])[-1]
                result.diagnostics = {'data_age_seconds': (self._now(as_of)-datetime.fromisoformat(last['time'])).total_seconds() if last.get('time') else None,
                                      'context_expires_at':snap.expires_at.isoformat(), 'missing_fields':snap.missing_fields,
                                      'budget':self.model_budget.snapshot()}
                if "bars" in snap.missing_fields or "freshness" in snap.missing_fields:
                    raise NotFresh("missing or stale visible minute context")
                # The context builder may have fallen back to avg cost for a
                # holding the valuation loop priced successfully moments
                # earlier; union both views so the Guard BUY veto and the
                # baseline rule always see the worst case (ISSUE-20261013-05).
                for iid in snap.aux.get("valuation_incomplete", []):
                    if not any(f.startswith(iid + " ") for f in valuation_failures):
                        valuation_failures.append(f"{iid} (context cost fallback)")
                self._valuation_failures = valuation_failures
            else:
                snap = self.context_builder.build(instrument_id, as_of)
            result.context_id = snap.context_id
            self.repo.save_context(snap)
            prices = {instrument_id: Decimal(str(snap.features.values.get("close", 0)))}
            if prices[instrument_id] == 0:
                prices = None
            if valuation_failures:
                result.diagnostics['valuation_failures'] = valuation_failures

            if getattr(self.cfg, "t0", None) and self.cfg.t0.enabled:
                ref = self.reference_price(instrument_id, as_of)
                exited = self._risk_exit(snap, ref, as_of, result)
                if exited:
                    return result
                if self.cfg.t0.profile == 'jev_fast_decision':
                    # Deterministic risk exits above remain available. Incomplete
                    # rolling facts must not launch a paid strategy/action worker.
                    if snap.features.missing_fields:
                        result.guard_verdict = 'WAIT_DATA_QUALITY'
                        result.guard_reasons = ['incomplete minute features']
                        return result
                    from .agents.fast_pipeline import FastDecisionPipeline
                    if not hasattr(self, '_fast_pipeline'):
                        self._fast_pipeline = FastDecisionPipeline(
                            self.llm, self.jev, self.repo, self.cfg.t0, self.cfg.account_id,
                            self.ledger, self.calendar, self.model_budget,
                            lambda: self._now(as_of) if self.clock else utcnow(),
                            lambda: {'daily_buy_orders': self.guard.daily_buy_orders_count,
                                     'consecutive_losses': self.guard.consecutive_losses,
                                     'day_start_equity': str(self._adjusted_day_start()),
                                     'slippage_bps': self.cfg.paper.slippage_bps})
                    prepared, snap = self._fast_model_result(snap, trace_id)
                    if prepared is None:
                        result.guard_verdict = 'WAIT_MODEL_PENDING'
                        result.diagnostics.update(reason='model worker pending', budget=self.model_budget.snapshot())
                        return result
                    intent, meta = prepared
                    trace_id = intent.trace_id
                    result.trace_id = trace_id
                    result.context_id = snap.context_id
                    result.diagnostics = meta.get('diagnostics', {})
                    if valuation_failures:
                        # The fast pipeline replaces diagnostics wholesale; keep
                        # the incomplete-valuation record visible (ISSUE-20261013-05).
                        result.diagnostics['valuation_failures'] = valuation_failures
                else:
                    cooldown = self.repo.get_coalesce('t0-candidate:' + self.cfg.account_id + ':' + instrument_id)
                    if cooldown and (as_of - cooldown['last_run_at']).total_seconds() < self.cfg.t0.candidate_cooldown_seconds:
                        result.guard_verdict = 'WAIT_COOLDOWN'
                        self.audit.record('decision', 'cooldown', trace_id, {'account_id': self.cfg.account_id})
                        return result
                    self.repo.save_coalesce('t0-candidate:' + self.cfg.account_id + ':' + instrument_id, as_of)
                    from .agents.pipeline import DualModelPipeline
                    from .models.jev import TypeSafeJEVProvider
                    if not hasattr(self, "_dual_model_pipeline"):
                        self._dual_model_pipeline = DualModelPipeline(
                            llm_provider=self.llm,
                            jev_provider=self.jev if isinstance(self.jev, TypeSafeJEVProvider) else None,
                            t0_config=self.cfg.t0,
                            account_id=self.cfg.account_id,
                        )
                    intent, meta = self._dual_model_pipeline.run(snap, trace_id=trace_id)
                result.intent = intent
                self.repo.save_intent(intent, 'generated')
                self.audit.record_critical('decision', 't0_chain', trace_id,
                                          {'account_id': self.cfg.account_id, 'decision_id': intent.decision_id, 'meta': meta})
                if intent.action in (TradeAction.HOLD, TradeAction.WAIT):
                    result.guard_verdict = f"{intent.action.value}_NO_TRADE"
                    return result
            else:
                thesis = None if force_research else self.latest_valid_thesis(instrument_id, as_of)
                fast_path = thesis is not None
                if thesis is None:
                    thesis = self.research.run(snap, trace_id, now=self._now(as_of))
                    thesis.available_at = self._now(as_of)
                    from .storage.db import dumps
                    self.db.execute('UPDATE theses SET payload_json=? WHERE thesis_id=?', (dumps(thesis), thesis.thesis_id))
                result.thesis = thesis

                holding = bool(snap.portfolio and snap.portfolio.positions.get(instrument_id))
                if self.ablation == "no_jev":
                    result.judgments = None
                else:
                    result.judgments = self.jev_gate.ask(snap, thesis, holding, trace_id, now=self._now(as_of))

                intent = self.trader.decide(snap, thesis, result.judgments, trace_id,
                                            now=self._now(as_of)) if result.judgments else self._no_jev_fallback(
                    snap, thesis, trace_id, as_of)
                result.intent = intent
                if intent.action.value == "HOLD":
                    result.guard_verdict = "HOLD_NO_TRADE"
                    return result

            if self.cfg.t0.enabled:
                # Final dual-model intent already contains risk/critic analysis.
                # Convert without additional AI calls or rewriting its target.
                pi = PortfolioIntent(
                    id=new_id('pi'), trace_id=trace_id, created_at=utcnow(),
                    intent_id=intent.id, decision_id=intent.decision_id,
                    account_id=self.cfg.account_id, portfolio_version=intent.portfolio_version,
                    instrument_id=instrument_id, action=intent.action,
                    target_weight=intent.target_weight, current_weight=intent.current_weight,
                    valid_until=intent.valid_until, mode=RunMode(self.mode), limit_price=intent.limit_price)
            else:
                review = self.risk_reviewer.review(intent, snap, trace_id)
                result.review = review
                pi, revised = self.portfolio_ai.finalize(intent, review, snap, trace_id)
                if revised is not None:
                    result.intent = revised
                if pi is None:
                    result.guard_verdict = review.verdict.value
                    result.guard_reasons = [review.reason[:100]]
                    return result
            result.portfolio_intent = pi

            ref_price = self.reference_price(instrument_id, as_of)
            guard_result, qty = self.guard.check(pi, snap, ref_price, now=self._now(as_of))
            result.guard_verdict = guard_result.verdict.value
            result.guard_reasons = guard_result.reason_codes
            if guard_result.verdict.value == "VETO":
                return result

            limit = pi.limit_price or ref_price
            order = self.oms.submit(pi, guard_result, qty, limit, now=self._now(as_of))
            result.order_id = order.order_id
            result.qty = qty
            self.outcomes.register(intent.decision_id, instrument_id, as_of)
            return result
        except (ProviderError, NotFresh, GuardVeto) as exc:
            result.error = f"{type(exc).__name__}: {exc}"[:300]
            return result
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"[:300]
            return result

    def _no_jev_fallback(self, snap: ContextSnapshot, thesis: TradeThesis,
                         trace_id: str, as_of: datetime) -> TradeIntent:
        """Ablation-only path: trader without JEV batch. Never used in production."""
        empty = JudgmentBatch(
            id=new_id("jb"), trace_id=trace_id, created_at=as_of,
            context_id=snap.context_id, thesis_id=thesis.thesis_id,
            instrument_id=snap.instrument_id, provider=self.jev.kind,
            model="ablation-empty", question_version="ablation",
            judgments=[], latency_ms=0, status="error", is_stub=True, as_of=as_of,
        )
        return self.trader.decide(snap, thesis, empty, trace_id, now=as_of)

    # -- fills ---------------------------------------------------------------
    def _risk_exit(self, snap, ref, as_of, result):
        account = self.ledger.snapshot(as_of, {snap.instrument_id: ref})
        position = next((p for p in account.positions if p.instrument_id == snap.instrument_id), None)
        if position is None:
            return False
        holding = self.repo.get_coalesce('fast-holding:' + self.cfg.account_id + ':' + snap.instrument_id) if self.cfg.t0.profile == 'jev_fast_decision' else None
        timed_out = bool(holding and holding['meta'].get('deadline') and as_of >= datetime.fromisoformat(holding['meta']['deadline']))
        reason = ('time_stop' if timed_out else 'stop_loss' if check_stop_loss_exit(position.avg_cost, ref, self.cfg.t0.stop_loss_ratio)
                  else 'session_close' if check_session_close_exit(as_of, self.cfg.t0.close_out_time) else None)
        if reason is None:
            return False
        pi = create_exit_portfolio_intent(
            self.cfg.account_id, account.version, snap.instrument_id,
            account.position_weight(snap.instrument_id),
            as_of + timedelta(seconds=self.cfg.risk.intent_ttl_seconds),
            mode=RunMode(self.mode), trace_id=result.trace_id)
        result.portfolio_intent = pi
        self.audit.record_critical('decision', 'risk_exit', result.trace_id,
                                   {'account_id': self.cfg.account_id, 'reason': reason, 'intent': pi.model_dump(mode='json')})
        verdict, qty = self.guard.check(pi, snap, ref, now=self._now(as_of))
        result.guard_verdict, result.guard_reasons = verdict.verdict.value, verdict.reason_codes
        if verdict.verdict.value == 'ALLOW':
            order = self.oms.submit(pi, verdict, qty, ref, now=self._now(as_of))
            result.order_id, result.qty = order.order_id, qty
        return True

    def _recover_holding_deadlines(self):
        """Rebuild missing markers from the current position's fill cycle."""
        import json
        for position in self.ledger.snapshot((self.clock or utcnow)()).positions:
            key = 'fast-holding:' + self.cfg.account_id + ':' + position.instrument_id
            if (row := self.repo.get_coalesce(key)) and row['meta'].get('deadline'):
                continue
            rows = self.db.query(
                'SELECT f.payload_json AS fill, o.side, o.intent_id FROM fills f JOIN orders o ON f.order_id=o.order_id '
                'WHERE o.account_id=? AND o.instrument_id=? ORDER BY f.executed_at, f.rowid',
                (self.cfg.account_id, position.instrument_id))
            quantity, first = Decimal('0'), None
            for row in rows:
                fill = json.loads(row['fill'])
                if row['side'] == 'BUY':
                    if quantity == 0:
                        first = (fill, row['intent_id'])
                    quantity += Decimal(fill['quantity'])
                else:
                    quantity -= Decimal(fill['quantity'])
                    if quantity == 0:
                        first = None
            payloads = [json.loads(r['payload_json']) for r in self.db.query('SELECT payload_json FROM intents')]
            payload = next((p for p in payloads if first and p['id'] == first[1]), None)
            # Missing provenance expires immediately; never unlimited holding.
            at = datetime.fromisoformat(first[0]['executed_at']) if first else (self.clock or utcnow)()
            self.repo.save_coalesce(key, at, {'deadline': (at + timedelta(seconds=payload['holding_horizon_seconds'] if payload else 0)).isoformat(), 'recovered': True})

    def poll_fills(self, now: datetime) -> list[str]:
        # Marker and memory share the fill transaction. Ledger.atomic restores
        # memory state as well as SQLite on failure; OMS nesting uses savepoints.
        with self.ledger.atomic():
            filled = self._poll_fills(now)
        if self.cfg.t0.enabled:
            self.guard.sync_counters(now)
        return filled

    def _poll_fills(self, now: datetime) -> list[str]:
        filled = []
        if self.cfg.t0.enabled:
            # Intraday order lifecycle (ARCH-021): day-order BUYs expire at the
            # trading-day change / no-new-entry cutoff before any fill attempt.
            self.oms.expire_stale_orders(now, self.cfg.t0.no_new_entry_time)
        for o in self.repo.open_orders():
            if o['account_id'] != self.cfg.account_id:
                continue
            fill = self.oms.poll_fill(o["order_id"], now)
            if fill is not None:
                filled.append(fill.fill_id)
                if self.cfg.t0.enabled and self.cfg.t0.profile == 'jev_fast_decision':
                    import json
                    key = 'fast-holding:' + self.cfg.account_id + ':' + o['instrument_id']
                    if o['side'] == 'BUY':
                        previous = self.repo.get_coalesce(key)
                        if not previous or not previous['meta'].get('deadline'):
                            rows = self.db.query('SELECT payload_json FROM intents WHERE action=? ORDER BY rowid DESC', ('BUY',))
                            payload = next((v for r in rows if (v := json.loads(r['payload_json'])).get('id') == o['intent_id']), None)
                            if payload:
                                self.repo.save_coalesce(key, now, {'deadline': (fill.executed_at+timedelta(seconds=payload['holding_horizon_seconds'])).isoformat(), 'decision_id': payload['decision_id']})
                    elif not any(p.instrument_id == o['instrument_id'] for p in self.ledger.snapshot(now).positions):
                        self.repo.save_coalesce(key, now, {})
                self.memory.record_trade(
                    instrument_id=o["instrument_id"], decision_id=o["intent_id"],
                    summary=f"fill {fill.quantity}@{fill.price}",
                    available_at=fill.available_at)
        return filled
