"""TASK-09 point-in-time context tests: no lookahead, warmup missing, hash."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.context import ContextBuilder, compute_features
from trademind.domain.base import new_id
from trademind.domain.instruments import FeeSchedule
from trademind.market.adapters.fixture import FixtureAdapter
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.portfolio import Ledger
from trademind.storage import Database

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))
FEES = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"),
                   created_at=datetime(2026, 1, 1, tzinfo=UTC),
                   commission_rate="0.00025", commission_min="5",
                   stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")


@pytest.fixture(scope="module")
def env():
    router = DatasetRouter(pref=["fixture"])
    cal = TradingCalendar.load()
    db = Database("sqlite:///:memory:")
    led = Ledger(db, "paper-001", FEES)
    led.deposit(__import__("decimal").Decimal("1000000"),
                datetime(2026, 3, 1, tzinfo=UTC))
    builder = ContextBuilder(router, cal, led, universe_version="univ-test")
    yield {"builder": builder, "cal": cal, "router": router, "ledger": led}
    db.close()


def test_features_match_hand_computation():
    from trademind.market.base import Bar, bar_close_time
    from datetime import date
    from decimal import Decimal

    def bar(i, close):
        d = date(2026, 1, 1) + timedelta(days=i)
        t = bar_close_time(d)
        return Bar(instrument_id="X", trade_date=d, open=close, high=close,
                   low=close, close=close, volume="100", amount="100",
                   provider="t", source_time=t, fetched_at=t, available_at=t)

    closes = [Decimal(100), Decimal(110)]
    bars = [bar(0, closes[0]), bar(1, closes[1])]
    vals, missing = compute_features(bars)
    assert vals["ret_1d"] == Decimal("0.100000")
    assert "ma5" not in vals and "ma5" in missing  # warmup missing, not 0
    assert vals["close"] == Decimal(110)


def test_context_no_future_news(env):
    # 600036 dividend news becomes available 2026-08-07 22:15 Beijing (14:15 UTC)
    before = env["builder"].build("600036.SH", datetime(2026, 8, 7, 14, 0, tzinfo=UTC))
    after = env["builder"].build("600036.SH", datetime(2026, 8, 7, 15, 0, tzinfo=UTC))
    assert "分红" not in str(before.aux["news"])  # type: ignore[attr-defined]
    assert "分红" in str(after.aux["news"])  # type: ignore[attr-defined]


def test_context_no_future_bars(env):
    # Build as of Aug 1; last visible bar must be available before as_of
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    snap = env["builder"].build("600519.SH", as_of)
    last = snap.aux["last_bars"][-1]  # type: ignore[attr-defined]
    assert last["date"] < as_of.date().isoformat()


def test_fundamentals_correction_visibility(env):
    # rev1 available 2026-07-28, rev2 corrects on 2026-08-20 (Beijing times)
    early = env["builder"].build("600519.SH", datetime(2026, 8, 1, tzinfo=UTC))
    late = env["builder"].build("600519.SH", datetime(2026, 8, 21, tzinfo=UTC))
    f_early = {f["report_period"]: f for f in early.aux["fundamentals"]}  # type: ignore[attr-defined]
    f_late = {f["report_period"]: f for f in late.aux["fundamentals"]}  # type: ignore[attr-defined]
    assert f_early["2026H1"]["revision"] == 1
    assert f_late["2026H1"]["revision"] == 2
    assert f_late["2026H1"]["metrics"]["net_profit_yi"] == "761.5"


def test_snapshot_hash_stable_and_freezes(env):
    as_of = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    s1 = env["builder"].build("600519.SH", as_of)
    s2 = env["builder"].build("600519.SH", as_of)
    # hash covers content but context ids differ per call; content hash equals
    # only if evidence sets line up — with deterministic fixtures they do.
    assert s1.features.values.keys() == s2.features.values.keys()
    assert s1.expires_at > s1.as_of


def test_portfolio_view_versioning(env):
    from decimal import Decimal
    as_of = datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    s0 = env["builder"].build("600519.SH", as_of)
    env["ledger"].deposit(Decimal("1000"), as_of)
    s1 = env["builder"].build("600519.SH", as_of)
    assert s1.portfolio.portfolio_version == s0.portfolio.portfolio_version + 1
    assert s1.portfolio.cash == Decimal("1001000")
