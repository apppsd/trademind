"""TASK-20261018-05 / ISSUE-20261018-02: reflection maturity/availability
gates and generated-lesson visibility timing. Synthetic outcomes, a Mock LLM
and an injected aware clock only — no network, model calls or credentials."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trademind.domain.base import new_id
from trademind.domain.enums import ProviderKind
from trademind.domain.outcome import Outcome
from trademind.memory import MemoryStore, Reflector
from trademind.storage import Database, Repository

UTC = timezone.utc
#: generation instant for the "late reflection" repro: label usable the day before
GEN_AT = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def make_outcome(decision_id: str = "d1", *, is_final: bool = True,
                 available_at: datetime, horizon_end: datetime | None = None) -> Outcome:
    return Outcome(
        id=new_id("out"), trace_id="tr", created_at=available_at,
        decision_id=decision_id,
        label_definition="forward N-trading-day net return", label_version="lbl-test",
        horizon_end=horizon_end or (available_at - timedelta(days=2)),
        available_at=available_at,
        return_ratio=None if not is_final else "0.01",
        censored_reason=None if is_final else "not_mature",
        is_final=is_final)


def stub_llm(calls: list):
    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            parsed={"summary": "s", "lesson": "l", "attributes_luck": False,
                    "confidence": "low"})
    return SimpleNamespace(kind=ProviderKind.STUB, model="synthetic-only",
                           complete=complete)


def make_reflector(repo: Repository, clock: FakeClock) -> tuple[Reflector, list]:
    calls: list = []
    return Reflector(stub_llm(calls), repo, MemoryStore(repo), clock=clock), calls


def counts(repo: Repository) -> tuple[int, int]:
    refl = repo.db.query("SELECT COUNT(*) c FROM reflections")[0]["c"]
    mem = repo.db.query("SELECT COUNT(*) c FROM memories")[0]["c"]
    return refl, mem


@pytest.fixture()
def repo():
    db = Database("sqlite:///:memory:")
    try:
        yield Repository(db)
    finally:
        db.close()


def test_unfinal_outcome_refused_before_model_call_or_writes(repo):
    clock = FakeClock(GEN_AT)
    reflector, calls = make_reflector(repo, clock)
    outcome = make_outcome(is_final=False, available_at=clock.now)
    with pytest.raises(ValueError, match="not final"):
        reflector.reflect(decision_id=outcome.decision_id, outcome=outcome,
                          context_digest="synthetic", trace_id="synthetic")
    assert calls == []  # zero model calls, zero budget contact
    assert counts(repo) == (0, 0)  # reflections and memories untouched


def test_not_yet_available_outcome_refused(repo):
    clock = FakeClock(GEN_AT)
    reflector, calls = make_reflector(repo, clock)
    outcome = make_outcome(available_at=clock.now + timedelta(days=1))
    with pytest.raises(ValueError, match="not usable"):
        reflector.reflect(decision_id=outcome.decision_id, outcome=outcome,
                          context_digest="synthetic", trace_id="synthetic")
    assert calls == []
    assert counts(repo) == (0, 0)


def test_late_generated_lesson_not_backdated_into_history(repo):
    # Label was usable 2026-10-17 10:00Z; the reflection actually ran one day
    # later. The lesson must not appear in 10-17 history even though the older
    # label time would allow it.
    clock = FakeClock(GEN_AT)
    reflector, calls = make_reflector(repo, clock)
    outcome = make_outcome(available_at=GEN_AT - timedelta(days=1))
    refl = reflector.reflect(decision_id=outcome.decision_id, outcome=outcome,
                             context_digest="synthetic", trace_id="synthetic")
    assert len(calls) == 1 and refl.created_at == GEN_AT
    store = MemoryStore(repo)
    assert store.visible(GEN_AT - timedelta(hours=12), None) == []  # before generation
    ids = [r["memory_id"] for r in store.visible(GEN_AT, None)]
    assert len(ids) == 1  # visible from the completion instant onward


def test_normal_mature_sample_visible_only_after_completion(repo):
    clock = FakeClock(GEN_AT)
    reflector, _ = make_reflector(repo, clock)
    outcome = make_outcome(available_at=clock.now)
    reflector.reflect(decision_id=outcome.decision_id, outcome=outcome,
                      context_digest="synthetic", trace_id="synthetic")
    store = MemoryStore(repo)
    assert store.visible(clock.now - timedelta(microseconds=1), None) == []
    assert store.visible(clock.now, None)


def test_early_closed_final_outcome_stays_eligible(repo):
    # A position closed early finalizes before the original horizon_end: the
    # gate must not re-impose horizon_end on final outcomes.
    clock = FakeClock(GEN_AT)
    reflector, calls = make_reflector(repo, clock)
    outcome = make_outcome(is_final=True, available_at=clock.now - timedelta(hours=1),
                           horizon_end=clock.now + timedelta(days=30))
    refl = reflector.reflect(decision_id=outcome.decision_id, outcome=outcome,
                             context_digest="synthetic", trace_id="synthetic")
    assert len(calls) == 1 and refl.created_at == clock.now
    assert counts(repo) == (1, 1)
