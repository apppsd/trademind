"""Memory store, outcome maturity, reflection (TASK-18).

- Outcome labels only after horizon maturity or position close; censored
  otherwise.
- Memories carry available_at; retrieval filters by as_of BEFORE any
  similarity search, so replay cannot see future lessons.
- Retraction and supersede are versioned; lessons never modify hard rules.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable

from ..domain.base import new_id, utcnow
from ..domain.enums import MemoryReviewState, MemoryType, ProviderKind
from ..domain.memory import MemoryRecord
from ..domain.modelcall import ModelCall
from ..domain.outcome import Outcome, Reflection
from ..market.base import BEIJING, bar_close_time
from ..models import LLMProvider
from ..storage import Repository
from ..agents.prompts import PROMPT_REGISTRY


class MemoryStore:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def record_event(self, *, instrument_id: str | None, summary: str,
                     evidence_ids: list[str], available_at: datetime,
                     keywords: list[str] | None = None) -> MemoryRecord:
        rec = MemoryRecord(
            id=new_id("mem"), trace_id=new_id("tr"), created_at=utcnow(),
            memory_id=new_id("mem"), type=MemoryType.EVENT,
            instrument_id=instrument_id, evidence_ids=evidence_ids[:10],
            available_at=available_at, summary=summary[:500],
            keywords=(keywords or [])[:10],
        )
        self.repo.save_memory(rec)
        return rec

    def record_trade(self, *, instrument_id: str, decision_id: str,
                     summary: str, available_at: datetime) -> MemoryRecord:
        rec = MemoryRecord(
            id=new_id("mem"), trace_id=new_id("tr"), created_at=utcnow(),
            memory_id=new_id("mem"), type=MemoryType.TRADE,
            instrument_id=instrument_id, decision_ids=[decision_id],
            available_at=available_at, summary=summary[:500],
        )
        self.repo.save_memory(rec)
        return rec

    def visible(self, as_of: datetime, instrument_id: str | None = None) -> list[dict]:
        return self.repo.memories_visible(as_of, instrument_id)

    def retract(self, memory_id: str) -> None:
        rows = self.repo.db.query("SELECT version FROM memories WHERE memory_id=?", (memory_id,))
        if rows:
            self.repo.update_memory_state(memory_id, MemoryReviewState.RETRACTED.value,
                                          int(rows[0]["version"]) + 1)

    def confirm(self, memory_id: str) -> None:
        rows = self.repo.db.query("SELECT version FROM memories WHERE memory_id=?", (memory_id,))
        if rows:
            self.repo.update_memory_state(memory_id, MemoryReviewState.CONFIRMED.value,
                                          int(rows[0]["version"]) + 1)


class OutcomeService:
    """Labels: 5d forward net return vs terminal-day close (v1 quantity).

    Semantics fixed by TASK-20261019-02 (ISSUE-20261019-01): the label lives
    on the Shanghai market calendar — the horizon ends 07:00 Asia/Shanghai on
    the terminal trading day, regardless of the timezone the decision instant
    was expressed in; maturity additionally requires the terminal-day daily
    bar to be visible (available_at <= now), and the label uses only that
    terminal close, never a later day's bar.
    """

    LABEL_DEFINITION = "forward N-trading-day net return of the decision instrument"
    #: v2 semantics (Shanghai horizon + gated maturity + terminal-day close).
    #: Rows registered under lbl-2026-10-07 keep their stored version; the
    #: final row always carries the version it was registered with.
    LABEL_VERSION = "lbl-2026-10-19"

    MARKET_TZ = BEIJING

    def __init__(self, repo: Repository, router, calendar,
                 horizon_days: int = 5, cost_rate: Decimal = Decimal("0.0015"),
                 clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self.router = router
        self.calendar = calendar
        self.horizon_days = horizon_days
        self.cost_rate = cost_rate
        self._clock = clock or utcnow

    def maturity_candidates(self, now: datetime) -> list[str]:
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("maturity_candidates requires a timezone-aware now")
        # Candidate discovery must not open before the terminal-day market
        # close (Task19-08): the terminal close cannot be final evidence
        # before 15:00+08, so rows expiring at 07:00 are not candidates at
        # 08:00/09:30/14:56 on the terminal day. At/after the terminal close
        # they stay discoverable — after close, during the publish window,
        # and on later days at any hour — so a matured row is never lost.
        # This is discovery only: mature() keeps its own independent
        # horizon + available_at double gate.
        rows = self.repo.db.query(
            "SELECT decision_id, payload_json FROM outcomes WHERE is_final=0")
        ready = []
        for r in rows:
            import json

            p = json.loads(r["payload_json"])
            horizon_end = datetime.fromisoformat(p["horizon_end"])
            if horizon_end > now:
                continue
            terminal_date = horizon_end.astimezone(BEIJING).date()
            if bar_close_time(terminal_date) <= now:
                ready.append(r["decision_id"])
        return ready

    def register(self, decision_id: str, instrument_id: str, decided_at: datetime) -> None:
        # Normalize the decision instant to the market calendar day first: the
        # same instant expressed in UTC or +08 must register one horizon.
        decided_bj = decided_at.astimezone(BEIJING)
        horizon_end = self.calendar.add_trading_days(decided_bj.date(), self.horizon_days)
        end_dt = datetime(horizon_end.year, horizon_end.month, horizon_end.day,
                          7, 0, tzinfo=BEIJING)
        outcome = Outcome(
            id=new_id("out"), trace_id=new_id("tr"), created_at=utcnow(),
            decision_id=decision_id, label_definition=self.LABEL_DEFINITION,
            label_version=self.LABEL_VERSION, horizon_end=end_dt,
            available_at=end_dt, censored_reason="not_mature",
        )
        self.repo.save_outcome(outcome)

    def mature(self, decision_id: str, instrument_id: str,
               base_price: Decimal) -> Outcome | None:
        """Compute the final label once the horizon has fully passed AND the
        terminal-day close is actually visible. Returns None — the row stays
        censored — before maturity or when the terminal close is missing or
        delayed; a later day's close is never substituted. Calendar coverage
        errors from the trading calendar propagate (never bypassed)."""
        rows = self.repo.db.query("SELECT payload_json FROM outcomes WHERE decision_id=?",
                                  (decision_id,))
        if not rows:
            return None
        import json

        p = json.loads(rows[0]["payload_json"])
        horizon_end = datetime.fromisoformat(p["horizon_end"])
        now = self._clock()
        if now < horizon_end:
            return None  # not matured yet: keep censored, write nothing final
        start = horizon_end.date()
        end = self.calendar.next(start, 1)
        # as_of=now: visibility filtering must happen BEFORE the router's
        # highest-revision merge (ISSUE-20261019-02, TASK-20261019-10) —
        # otherwise a future revision evicts the currently visible terminal
        # bar and the early viewpoint silently changes. The router returns
        # only rows visible at `now`, merged per trade_date.
        bars, _ = self.router.get_daily_bars(instrument_id, start, end, as_of=now)
        visible = [b for b in bars
                   if b.available_at is None or b.available_at <= now]
        terminal = next((b for b in visible if b.trade_date == horizon_end.date()), None)
        if terminal is None:
            return None  # terminal close missing/delayed: no next-day substitute
        future_close = terminal.close
        ret = (future_close - base_price) / base_price - self.cost_rate
        outcome = Outcome(
            id=new_id("out"), trace_id=p.get("trace_id", new_id("tr")),
            created_at=now, decision_id=decision_id,
            label_definition=p.get("label_definition", self.LABEL_DEFINITION),
            label_version=p.get("label_version", self.LABEL_VERSION),
            horizon_end=horizon_end, available_at=now,
            return_ratio=str(ret.quantize(Decimal("0.000001"))),
            realized_pnl=str((future_close - base_price)),
            fees=str((base_price * self.cost_rate).quantize(Decimal("0.01"))),
            is_final=True,
        )
        self.repo.save_outcome(outcome)
        return outcome


class Reflector:
    """Only matured outcomes feed reflection; luck is flagged explicitly."""

    def __init__(self, llm: LLMProvider, repo: Repository, memory: MemoryStore,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.llm = llm
        self.repo = repo
        self.memory = memory
        # Injectable aware clock so the gate and the lesson's visible time are
        # deterministically testable (TASK-20261018-05).
        self._clock = clock or utcnow

    def reflect(self, *, decision_id: str, outcome: Outcome,
                context_digest: str, trace_id: str) -> Reflection:
        # Gate BEFORE the model call and any persistence (ISSUE-20261018-02):
        # only final outcomes whose usable time has actually arrived may feed
        # reflection. Early-closed finals stay eligible — horizon_end is never
        # re-checked here; "final" plus a passed available_at is the contract.
        now = self._clock()
        if not outcome.is_final:
            raise ValueError(
                f"outcome {decision_id} is not final "
                f"(censored_reason={outcome.censored_reason!r}); reflection refused")
        if outcome.available_at > now:
            raise ValueError(
                f"outcome {decision_id} is not usable until "
                f"{outcome.available_at.isoformat()}; reflection refused")
        user = (
            f"decision: {decision_id}\noutcome: return_ratio={outcome.return_ratio} "
            f"label={outcome.label_definition} v{outcome.label_version}\n"
            f"context at decision: {context_digest[:1200]}\n"
        )
        mout = self.llm.complete(role="reflection",
                                 system=PROMPT_REGISTRY["reflection"],
                                 user=user, trace_id=trace_id)
        parsed = mout.parsed or {}
        completed_at = self._clock()
        refl = Reflection(
            id=new_id("refl"), trace_id=trace_id, created_at=completed_at,
            decision_id=decision_id, outcome_ids=[outcome.id],
            summary=str(parsed.get("summary", ""))[:800],
            lesson=str(parsed.get("lesson", ""))[:800],
            sample_size=1, attributes_luck=bool(parsed.get("attributes_luck", True)),
            confidence=str(parsed.get("confidence", "low")),
            is_stub=self.llm.kind == ProviderKind.STUB, model=self.llm.model,
        )
        self.repo.save_reflection(refl)
        # The lesson did not exist before it was generated: its visible time is
        # at least the generation completion instant, never the older label
        # time — a late run must not backdate the lesson into earlier history.
        rec = self.memory.record_event(
            instrument_id=None, summary=f"reflection[{decision_id}]: {refl.lesson}",
            evidence_ids=[], available_at=max(outcome.available_at, completed_at),
        )
        self.memory.confirm(rec.memory_id)
        return refl
