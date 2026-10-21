"""TASK-20261019-02 / ISSUE-20261019-01: outcome label horizon timezone,
maturity/visibility gating and terminal-close selection. Synthetic fixture
daily bars, a real FixtureAdapter/DatasetRouter pair and an injected aware
clock — no network, model calls or credentials."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trademind.domain.base import new_id
from trademind.domain.outcome import Outcome
from trademind.market.base import (BEIJING, FIXTURE_PUBLISH_DELAY, Bar,
                                   bar_close_time)
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.memory import OutcomeService
from trademind.storage import Database, Repository

UTC = timezone.utc
IID = "513050.SH"
#: Monday..Friday, weekend 10-17/18 excluded
TRADING_DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
HORIZON = datetime(2026, 10, 19, 7, 0, tzinfo=BEIJING)  # 5 trading days after 10-12
PUBLISH = bar_close_time(date(2026, 10, 19)) + FIXTURE_PUBLISH_DELAY  # 15:30 +08


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def write_daily_csv(tmp_path, instrument_id: str, closes: dict[date, str],
                    omit: frozenset[date] = frozenset()) -> None:
    p = tmp_path / "daily" / f"{instrument_id}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["trade_date,open,high,low,close,pre_close,volume,amount"]
    for d in TRADING_DAYS:
        if d in omit:
            continue
        close = closes.get(d, "100")
        lines.append(f"{d},{close},{close},{close},{close},,1000,{int(close)}000")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def env(tmp_path):
    write_daily_csv(tmp_path, IID, {date(2026, 10, 19): "110", date(2026, 10, 20): "200"})
    db = Database("sqlite:///:memory:")
    repo = Repository(db)
    router = DatasetRouter(pref=["fixture"], fixture_root=str(tmp_path))
    clock = FakeClock(HORIZON)
    service = OutcomeService(repo, router, TradingCalendar(TRADING_DAYS), clock=clock)
    try:
        yield service, repo, clock
    finally:
        db.close()


def registered_row(repo: Repository, decision_id: str) -> dict:
    rows = repo.db.query("SELECT is_final, payload_json FROM outcomes WHERE decision_id=?",
                         (decision_id,))
    assert rows
    return {"is_final": rows[0]["is_final"], "payload": json.loads(rows[0]["payload_json"])}


def test_register_same_instant_any_timezone_same_horizon(env):
    service, repo, _ = env
    decided = datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)
    service.register("d-cst", IID, decided)
    service.register("d-utc", IID, decided.astimezone(UTC))
    h_cst = registered_row(repo, "d-cst")["payload"]["horizon_end"]
    h_utc = registered_row(repo, "d-utc")["payload"]["horizon_end"]
    assert datetime.fromisoformat(h_cst) == datetime.fromisoformat(h_utc)
    assert datetime.fromisoformat(h_cst).astimezone(BEIJING).date() == date(2026, 10, 19)
    assert datetime.fromisoformat(h_cst).astimezone(BEIJING).hour == 7


def test_register_skips_weekend(env):
    service, repo, _ = env
    service.register("d-fri", IID, datetime(2026, 10, 16, 10, 0, tzinfo=BEIJING))
    horizon = datetime.fromisoformat(registered_row(repo, "d-fri")["payload"]["horizon_end"])
    assert horizon.astimezone(BEIJING).date() == date(2026, 10, 23)  # 10-17/18 skipped


def test_candidates_share_one_instant_not_tz_skewed(env):
    service, repo, _ = env
    decided = datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)
    service.register("d-cst", IID, decided)
    service.register("d-utc", IID, decided.astimezone(UTC))
    # after the terminal close both timezone representations surface together
    now = datetime(2026, 10, 19, 15, 30, tzinfo=BEIJING)
    assert set(service.maturity_candidates(now)) == {"d-cst", "d-utc"}
    # the same instant in UTC yields the identical set (no tz skew)
    assert set(service.maturity_candidates(now.astimezone(UTC))) == {"d-cst", "d-utc"}
    # before the terminal close: horizon passed but close not happened → none
    assert service.maturity_candidates(datetime(2026, 10, 19, 14, 59, tzinfo=BEIJING)) == []
    with pytest.raises(ValueError):
        service.maturity_candidates(now.replace(tzinfo=None))


def test_candidates_not_ready_before_market_close(env):
    # Task19-06 / Task19-08 re-review contract: candidate discovery never
    # opens before the terminal-day close (15:00+08), and matured rows stay
    # discoverable after close, through the publish window and next day.
    service, repo, _ = env
    decided = datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)
    service.register("d-cst", IID, decided)
    service.register("d-utc", IID, decided.astimezone(UTC))
    both = {"d-cst", "d-utc"}
    # 08:00+08: horizon passed but terminal close far away → no candidates
    assert service.maturity_candidates(datetime(2026, 10, 19, 8, 0, tzinfo=BEIJING)) == []
    # 09:30+08: market open is NOT maturity evidence → still no candidates
    assert service.maturity_candidates(datetime(2026, 10, 19, 9, 30, tzinfo=BEIJING)) == []
    # 14:56+08: minutes before close → still no candidates
    assert service.maturity_candidates(datetime(2026, 10, 19, 14, 56, tzinfo=BEIJING)) == []
    # 15:00 - 1µs: one microsecond before close → no candidates
    assert service.maturity_candidates(
        datetime(2026, 10, 19, 15, 0, tzinfo=BEIJING) - timedelta(microseconds=1)) == []
    # 15:00+08 exactly: terminal close happened → candidates discoverable
    assert set(service.maturity_candidates(datetime(2026, 10, 19, 15, 0, tzinfo=BEIJING))) == both
    # 15:30+08 (fixture publish instant): still discoverable
    assert set(service.maturity_candidates(datetime(2026, 10, 19, 15, 30, tzinfo=BEIJING))) == both
    # 15:30 - 1µs (bar not yet published): candidates may surface — mature()
    # keeps the independent available_at gate and stays censored meanwhile
    assert set(service.maturity_candidates(
        datetime(2026, 10, 19, 15, 30, tzinfo=BEIJING) - timedelta(microseconds=1))) == both
    # next day 08:00+08 (before open): matured rows remain discoverable
    assert set(service.maturity_candidates(datetime(2026, 10, 20, 8, 0, tzinfo=BEIJING))) == both


def test_mature_gates_maturity_publication_and_uses_terminal_close(env):
    service, repo, clock = env
    service.register("d1", IID, datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING))
    base = Decimal("100")
    # before the horizon instant: refused, row stays censored
    clock.now = HORIZON - timedelta(microseconds=1)
    assert service.mature("d1", IID, base) is None
    assert registered_row(repo, "d1")["is_final"] == 0
    # at the horizon the daily bar is not published yet: still censored
    clock.now = HORIZON
    assert service.mature("d1", IID, base) is None
    clock.now = PUBLISH - timedelta(microseconds=1)
    assert service.mature("d1", IID, base) is None
    assert registered_row(repo, "d1")["is_final"] == 0
    # once the terminal-day bar is visible: final label uses 10-19 close 110
    clock.now = PUBLISH
    outcome = service.mature("d1", IID, base)
    assert outcome is not None and outcome.is_final
    assert outcome.return_ratio == Decimal("0.098500")  # (110-100)/100 - 0.0015
    assert outcome.available_at == PUBLISH  # never earlier than the horizon
    row = registered_row(repo, "d1")
    assert row["is_final"] == 1 and row["payload"]["label_version"] == "lbl-2026-10-19"
    assert row["payload"]["return_ratio"] != "0.998500"  # 10-20's close 200 never used


def test_terminal_missing_or_delayed_never_substitutes_next_day(tmp_path):
    write_daily_csv(tmp_path, IID, {date(2026, 10, 20): "200"},
                    omit=frozenset({date(2026, 10, 19)}))
    db = Database("sqlite:///:memory:")
    repo = Repository(db)
    clock = FakeClock(datetime(2026, 10, 21, 8, 0, tzinfo=UTC))  # well after 10-20
    service = OutcomeService(repo, DatasetRouter(pref=["fixture"], fixture_root=str(tmp_path)),
                             TradingCalendar(TRADING_DAYS), clock=clock)
    try:
        service.register("d1", IID, datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING))
        assert service.mature("d1", IID, Decimal("100")) is None
        assert registered_row(repo, "d1")["is_final"] == 0  # no 200-based final
    finally:
        db.close()


def test_missing_decision_and_empty_data_controls(tmp_path):
    write_daily_csv(tmp_path, IID, {})
    db = Database("sqlite:///:memory:")
    repo = Repository(db)
    service = OutcomeService(repo, DatasetRouter(pref=["fixture"], fixture_root=str(tmp_path)),
                             TradingCalendar(TRADING_DAYS), clock=FakeClock(HORIZON))
    try:
        assert service.mature("no-such-decision", IID, Decimal("100")) is None
        service.register("d-empty", "absent.SH",
                         datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING))
        assert service.mature("d-empty", "absent.SH", Decimal("100")) is None
        assert registered_row(repo, "d-empty")["is_final"] == 0
    finally:
        db.close()


def test_legacy_registered_label_keeps_version_and_new_semantics(tmp_path):
    # A legacy lbl-2026-10-07 row registered with a caller-tz horizon keeps
    # its version on maturity; the gate and terminal-day-close semantics of
    # the current code still apply at maturity time.
    write_daily_csv(tmp_path, IID, {date(2026, 10, 19): "110"})
    db = Database("sqlite:///:memory:")
    repo = Repository(db)
    clock = FakeClock(datetime(2026, 10, 19, 16, 0, tzinfo=BEIJING))
    service = OutcomeService(repo, DatasetRouter(pref=["fixture"], fixture_root=str(tmp_path)),
                             TradingCalendar(TRADING_DAYS), clock=clock)
    try:
        legacy = Outcome(
            id=new_id("out"), trace_id="tr", created_at=datetime(2026, 10, 12, 2, 0, tzinfo=UTC),
            decision_id="d-legacy",
            label_definition=service.LABEL_DEFINITION, label_version="lbl-2026-10-07",
            horizon_end=datetime(2026, 10, 19, 7, 0, tzinfo=UTC),  # old caller-tz horizon
            available_at=datetime(2026, 10, 19, 7, 0, tzinfo=UTC),
            censored_reason="not_mature")
        repo.save_outcome(legacy)
        outcome = service.mature("d-legacy", IID, Decimal("100"))
        assert outcome is not None
        assert outcome.label_version == "lbl-2026-10-07"  # never silently re-versioned
        assert outcome.return_ratio == Decimal("0.098500")  # terminal-day close under the new rule
    finally:
        db.close()


# --- TASK-20261019-10 / ISSUE-20261019-02 (Task19-09 counterexample) -------


def _rev_bar(instrument_id: str, d: date, close: str, revision: int,
             available_at: datetime) -> Bar:
    t = bar_close_time(d)
    return Bar(instrument_id=instrument_id, trade_date=d,
               open=Decimal(close), high=Decimal(close), low=Decimal(close),
               close=Decimal(close), volume="1000", amount="100000",
               provider="rev", source_time=t, fetched_at=available_at,
               available_at=available_at, revision=revision)


class _RevisionDailyAdapter:
    """Daily source whose rows can grow mid-test (vendor publishes a
    correction after the fact), like the Issue19-02 revision adapter."""

    name = "rev"

    def __init__(self, rows):
        self.rows = rows

    def capabilities(self):
        return {"minute_bars": False, "daily_bars": True}

    def get_daily_bars(self, instrument_id, start, end):
        return [b for b in self.rows.get(instrument_id, [])
                if start <= b.trade_date <= end]

    def get_minute_bars(self, *a, **k):
        return []

    def get_news(self, *a, **k):
        return []

    def get_fundamentals(self, *a, **k):
        return []

    def get_calendar(self, start, end):
        return []


def test_mature_future_revision_cannot_change_early_viewpoint(tmp_path):
    # Terminal 10-19: r1 close 110 visible at 15:30; a future r2 close 120
    # becomes visible at 10-20 16:00. Before the fix the un-as_of router call
    # let r2 win the revision merge first, evicting r1 — the same early
    # viewpoint degraded from final=0.098500 to None (censored forever).
    r1_at = datetime(2026, 10, 19, 15, 30, tzinfo=BEIJING)
    r2_at = datetime(2026, 10, 20, 16, 0, tzinfo=BEIJING)
    early = datetime(2026, 10, 19, 16, 0, tzinfo=BEIJING)
    late = datetime(2026, 10, 20, 16, 0, tzinfo=BEIJING)
    rows = {IID: [_rev_bar(IID, date(2026, 10, 19), "110", 1, r1_at)]}
    router = DatasetRouter(pref=["rev"])
    router._factories = {"rev": (lambda: _RevisionDailyAdapter(rows))}
    db = Database("sqlite:///:memory:")
    repo = Repository(db)
    clock = FakeClock(early)
    service = OutcomeService(repo, router, TradingCalendar(TRADING_DAYS), clock=clock)
    base = Decimal("100")
    decided = datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)
    try:
        # publish gate kept: one microsecond before r1 is visible → censored
        service.register("d-pre", IID, decided)
        clock.now = r1_at - timedelta(microseconds=1)
        assert service.mature("d-pre", IID, base) is None
        assert registered_row(repo, "d-pre")["is_final"] == 0
        # r1 only: early viewpoint matures to 0.098500
        service.register("d-r1", IID, decided)
        clock.now = early
        out1 = service.mature("d-r1", IID, base)
        assert out1 is not None and out1.return_ratio == Decimal("0.098500")
        # the future r2 appears; a NEW censored row at the same early instant
        # must still mature to 0.098500 (was None before the as_of fix)
        rows[IID].append(_rev_bar(IID, date(2026, 10, 19), "120", 2, r2_at))
        service.register("d-early2", IID, decided)
        clock.now = early
        out2 = service.mature("d-early2", IID, base)
        assert out2 is not None and out2.return_ratio == Decimal("0.098500")
        # the late viewpoint adopts the correction: 0.198500
        service.register("d-late", IID, decided)
        clock.now = late
        out3 = service.mature("d-late", IID, base)
        assert out3 is not None and out3.return_ratio == Decimal("0.198500")
        # early again stays stable at 0.098500
        service.register("d-early3", IID, decided)
        clock.now = early
        out4 = service.mature("d-early3", IID, base)
        assert out4 is not None and out4.return_ratio == Decimal("0.098500")
    finally:
        db.close()
