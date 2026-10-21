"""TASK-20261018-03 / ISSUE-20261018-01: memory visibility must compare
available_at as timezone-aware instants, never as ISO offset strings. All
records are synthetic; the database is in-memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trademind.domain.base import new_id
from trademind.domain.enums import MemoryType
from trademind.domain.memory import MemoryRecord
from trademind.memory import MemoryStore
from trademind.storage import Database, Repository

UTC = timezone.utc
CST = timezone(timedelta(hours=8))
NEG5 = timezone(timedelta(hours=-5))
EAST14 = timezone(timedelta(hours=14))

#: the shared comparison instant: 2026-10-12T02:00:00Z
BASE = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)


@pytest.fixture()
def repo():
    db = Database("sqlite:///:memory:")
    try:
        yield Repository(db)
    finally:
        db.close()


def make_record(memory_id: str, available_at: datetime, *, instrument_id: str | None = "513050.SH",
                valid_until: datetime | None = None) -> MemoryRecord:
    return MemoryRecord(
        id=new_id("m"), trace_id="tr", created_at=available_at,
        memory_id=memory_id, type=MemoryType.EVENT,
        instrument_id=instrument_id, available_at=available_at,
        valid_until=valid_until, summary="synthetic")


def test_issue_repro_future_utc_record_excluded_and_past_beijing_included(repo):
    # The two ISSUE rows: same as_of instant, opposite wrong answers before
    # the fix (the future row was returned, the past row was missed).
    store = MemoryStore(repo)
    future_utc = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)  # 7h after the cutoff
    cutoff = datetime(2026, 10, 12, 10, 0, tzinfo=CST)  # == 02:00Z
    assert future_utc > cutoff
    store.repo.save_memory(make_record("future", future_utc))
    assert store.visible(cutoff, "513050.SH") == []
    assert not make_record("future", future_utc).visible_at(cutoff)

    past_cst = datetime(2026, 10, 12, 9, 0, tzinfo=CST)  # == 01:00Z, before cutoff
    store.repo.save_memory(make_record("past", past_cst))
    visible = store.visible(cutoff, "513050.SH")
    assert [r["memory_id"] for r in visible] == ["past"]
    assert make_record("past", past_cst).visible_at(cutoff)


def test_visible_matches_memory_record_visible_at_across_offsets(repo):
    store = MemoryStore(repo)
    instants = [BASE - timedelta(hours=3), BASE,
                BASE + timedelta(microseconds=1), BASE + timedelta(hours=7)]
    for i, instant in enumerate(instants):
        store.repo.save_memory(make_record(f"m{i}", instant))
    for tz in (UTC, CST, NEG5):
        as_of = BASE.astimezone(tz)
        ids = {r["memory_id"] for r in store.visible(as_of, "513050.SH")}
        assert ids == {f"m{i}" for i, t in enumerate(instants) if t <= as_of}
        for i, t in enumerate(instants):
            assert make_record(f"m{i}", t).visible_at(as_of) == (f"m{i}" in ids)


def test_boundary_microsecond_and_cross_day_rows(repo):
    store = MemoryStore(repo)
    store.repo.save_memory(make_record("exact", BASE.astimezone(CST)))  # == BASE
    store.repo.save_memory(make_record("micro", BASE + timedelta(microseconds=1)))
    store.repo.save_memory(make_record("prevday", datetime(2026, 10, 11, 20, 0, tzinfo=UTC)))
    store.repo.save_memory(make_record("nextday", datetime(2026, 10, 13, 2, 0, tzinfo=CST)))
    for tz in (UTC, CST, NEG5):
        ids = {r["memory_id"] for r in store.visible(BASE.astimezone(tz), None)}
        assert ids == {"exact", "prevday"}  # equal instant is visible; +1us is not


def test_far_offset_row_lexicographically_beyond_as_of_still_visible(repo):
    # A +14:00 row one hour before the cutoff writes a TEXT wall clock on the
    # next calendar day: a string comparison used to miss it.
    store = MemoryStore(repo)
    store.repo.save_memory(make_record("east", datetime(2026, 10, 13, 9, 0, tzinfo=EAST14)))
    as_of = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    assert datetime(2026, 10, 13, 9, 0, tzinfo=EAST14) < as_of  # truly visible
    ids = [r["memory_id"] for r in store.visible(as_of, None)]
    assert ids == ["east"]


def test_mixed_offset_legacy_rows_ordered_by_instant(repo):
    store = MemoryStore(repo)
    rows = [("a", datetime(2026, 10, 12, 18, 0, tzinfo=CST)),   # 10:00Z
            ("b", datetime(2026, 10, 12, 11, 0, tzinfo=UTC)),   # 11:00Z
            ("c", datetime(2026, 10, 12, 7, 0, tzinfo=NEG5))]   # 12:00Z
    for rec_id, instant in rows:
        store.repo.save_memory(make_record(rec_id, instant))
    # TEXT order would be c, b, a; instant order is a, b, c.
    got = [r["memory_id"] for r in store.visible(datetime(2026, 10, 12, 13, 0, tzinfo=UTC), None)]
    assert got == ["a", "b", "c"]


def test_retraction_valid_until_and_scope_filters_preserved(repo):
    store = MemoryStore(repo)
    past = BASE - timedelta(hours=1)
    store.repo.save_memory(make_record("glob", past, instrument_id=None))
    store.repo.save_memory(make_record("other", past, instrument_id="513100.SH"))
    store.repo.save_memory(make_record("expired", past, instrument_id="513100.SH",
                                       valid_until=BASE - timedelta(seconds=1)))
    store.repo.save_memory(make_record("edge", past, valid_until=BASE))  # == as_of stays
    # global records serve every instrument query until retracted
    assert {r["memory_id"] for r in store.visible(BASE, "513050.SH")} == {"glob", "edge"}
    assert {r["memory_id"] for r in store.visible(BASE, "513100.SH")} == {"glob", "other"}
    store.retract("glob")
    assert {r["memory_id"] for r in store.visible(BASE, "513050.SH")} == {"edge"}
    assert {r["memory_id"] for r in store.visible(BASE, "513100.SH")} == {"other"}


def test_naive_as_of_is_rejected(repo):
    with pytest.raises(ValueError):
        repo.memories_visible(datetime(2026, 10, 12, 2, 0))
