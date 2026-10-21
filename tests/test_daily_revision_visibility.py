"""TASK-20261019-04 / ISSUE-20261019-02: daily-bar point-in-time revisions —
a future correction must never evict a historically visible row. Real
DatasetRouter + ContextBuilder + Ledger with a synthetic revision adapter;
no network, model calls or real market data."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.context import ContextBuilder
from trademind.domain.base import new_id
from trademind.domain.enums import OrderSide, RunMode
from trademind.domain.instruments import FeeSchedule
from trademind.domain.orders import Fill, Order
from trademind.market.base import BEIJING, Bar, bar_close_time
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.portfolio import Ledger
from trademind.storage import Database

UTC = timezone.utc
MAIN = "513050.SH"
OTHER = "513100.SH"
FEES = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"),
                   created_at=datetime(2026, 1, 1, tzinfo=UTC),
                   commission_rate="0.00025", commission_min="5",
                   stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")

EARLY = datetime(2026, 10, 12, 16, 0, tzinfo=BEIJING)   # r1 visible, r2 not
LATE = datetime(2026, 10, 13, 16, 0, tzinfo=BEIJING)    # both visible


def daily_bar(instrument_id: str, d: date, close: str, revision: int = 1,
              available_at: datetime | None = None) -> Bar:
    t = bar_close_time(d)
    return Bar(instrument_id=instrument_id, trade_date=d,
               open=Decimal(close), high=Decimal(close), low=Decimal(close),
               close=Decimal(close), volume="1000", amount="100000",
               provider="rev", source_time=t, fetched_at=available_at or t,
               available_at=available_at or t, revision=revision)


class RevisionAdapter:
    """Synthetic daily source whose rows can grow mid-test, like a vendor
    publishing a correction after the fact. Minutes/news/fundamentals empty."""

    name = "rev"

    def __init__(self, rows: dict[str, list[Bar]]):
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


def weekday_calendar(start_d: date, end_d: date) -> TradingCalendar:
    days = [start_d + timedelta(days=i) for i in range((end_d - start_d).days + 1)]
    return TradingCalendar([d for d in days if d.weekday() < 5])


def base_rows() -> dict[str, list[Bar]]:
    return {
        MAIN: [daily_bar(MAIN, date(2026, 10, 9), "99"),
               daily_bar(MAIN, date(2026, 10, 12), "100", revision=1)],
        OTHER: [daily_bar(OTHER, date(2026, 10, 9), "300"),
                daily_bar(OTHER, date(2026, 10, 12), "310", revision=1)],
    }


def revision2_rows() -> dict[str, list[Bar]]:
    late = datetime(2026, 10, 13, 16, 0, tzinfo=BEIJING)  # visible after EARLY
    return {MAIN: [daily_bar(MAIN, date(2026, 10, 12), "110", revision=2,
                             available_at=late)],
            OTHER: [daily_bar(OTHER, date(2026, 10, 12), "330", revision=2,
                              available_at=late)]}


@pytest.fixture()
def env():
    rows = base_rows()
    router = DatasetRouter(pref=["rev", "fixture"])
    router._factories = {"rev": (lambda: RevisionAdapter(rows)),
                         "fixture": (lambda: RevisionAdapter(rows))}
    builder = ContextBuilder(router, weekday_calendar(date(2026, 5, 1), date(2026, 10, 23)),
                             None, universe_version="univ-test")
    yield builder, router, rows


def last_bar_dates(snap) -> list[str]:
    return [b["date"] for b in snap.aux["last_bars"]]  # type: ignore[attr-defined]


def test_future_revision_cannot_evict_visible_row(env):
    builder, _router, rows = env
    # before the correction exists: 10-12 close 100 is the known state
    early1 = builder.build(MAIN, EARLY)
    assert early1.features.values["close"] == Decimal("100")
    assert date(2026, 10, 12).isoformat() in last_bar_dates(early1)
    # the vendor publishes the future correction; the early context must not
    # change (previously it degraded to the 10-09 close 99 without 10-12)
    rows[MAIN].extend(revision2_rows()[MAIN])
    rows[OTHER].extend(revision2_rows()[OTHER])
    early2 = builder.build(MAIN, EARLY)
    assert early2.features.values["close"] == Decimal("100")
    assert date(2026, 10, 12).isoformat() in last_bar_dates(early2)
    # the late viewpoint adopts the correction normally
    late = builder.build(MAIN, LATE)
    assert late.features.values["close"] == Decimal("110")


def test_visible_merge_is_input_order_and_duplicate_independent(env):
    builder, _router, rows = env
    rows[MAIN].extend(revision2_rows()[MAIN])
    rows[MAIN].append(daily_bar(MAIN, date(2026, 10, 12), "100", revision=1,
                                available_at=datetime(2026, 10, 12, 18, 0, tzinfo=BEIJING)))
    reversed_snapshot_rows = list(reversed(rows[MAIN]))
    early = builder.build(MAIN, EARLY)
    assert early.features.values["close"] == Decimal("100")
    dates = last_bar_dates(early)
    assert dates.count(date(2026, 10, 12).isoformat()) <= 1  # no date duplication
    # rebuilding from reversed input order gives the same viewpoint
    rows[MAIN] = reversed_snapshot_rows
    assert builder.build(MAIN, EARLY).features.values["close"] == Decimal("100")
    assert builder.build(MAIN, LATE).features.values["close"] == Decimal("110")


def test_router_as_of_and_live_default_contract(env):
    _builder, router, rows = env
    rows[MAIN].extend(revision2_rows()[MAIN])
    start, end = date(2026, 10, 1), date(2026, 10, 23)
    early, _ = router.get_daily_bars(MAIN, start, end, as_of=EARLY)
    assert [b.close for b in early if b.trade_date == date(2026, 10, 12)] == [Decimal("100")]
    late, _ = router.get_daily_bars(MAIN, start, end, as_of=LATE)
    assert [b.close for b in late if b.trade_date == date(2026, 10, 12)] == [Decimal("110")]
    # early -> late -> early stays stable (no daily cache to poison)
    again, _ = router.get_daily_bars(MAIN, start, end, as_of=EARLY)
    assert [b.close for b in again if b.trade_date == date(2026, 10, 12)] == [Decimal("100")]
    # no as_of keeps the live default: latest known revision wins
    live, _ = router.get_daily_bars(MAIN, start, end)
    assert [b.close for b in live if b.trade_date == date(2026, 10, 12)] == [Decimal("110")]


def test_other_position_valuation_keeps_visible_revision():
    rows = base_rows()
    router = DatasetRouter(pref=["rev"])
    router._factories = {"rev": (lambda: RevisionAdapter(rows))}
    db = Database("sqlite:///:memory:")
    try:
        ledger = Ledger(db, "paper-001", FEES)
        at = datetime(2026, 10, 9, 10, 0, tzinfo=BEIJING)
        ledger.deposit(Decimal("1000000"), at)
        order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=at,
                      order_id=new_id("o"), client_order_id=new_id("cli"),
                      intent_id=new_id("i"), guard_id=new_id("g"),
                      account_id="paper-001", instrument_id=OTHER,
                      mode=RunMode.PAPER, side=OrderSide.BUY, quantity="100",
                      limit_price=Decimal("300"), updated_at=at)
        fill = Fill(id=new_id("f"), trace_id="t", created_at=at,
                    fill_id=new_id("f"), order_id=order.order_id,
                    provider_fill_id=new_id("pf"), quantity=Decimal("100"),
                    price=Decimal("300"), fee=Decimal("0"),
                    executed_at=at, available_at=at)
        ledger.apply_fill(fill, order, at)
        builder = ContextBuilder(router, weekday_calendar(date(2026, 5, 1), date(2026, 10, 23)),
                                 ledger, universe_version="univ-test")
        rows[MAIN].extend(revision2_rows()[MAIN])
        rows[OTHER].extend(revision2_rows()[OTHER])
        early = builder.build(MAIN, EARLY)
        assert Decimal(early.portfolio.positions[OTHER]["market_value"]) == Decimal("310") * 100
        late = builder.build(MAIN, LATE)
        assert Decimal(late.portfolio.positions[OTHER]["market_value"]) == Decimal("330") * 100
    finally:
        db.close()
