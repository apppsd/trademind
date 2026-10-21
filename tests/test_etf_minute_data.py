"""Tests for ETF minute data, aggregation, quality, and point-in-time features (TASK-43)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from trademind.market.base import BEIJING, MinuteBar
from trademind.market.quality import (
    aggregate_minute_bars,
    normalize_minute_bars,
    get_minute_bucket,
)
from trademind.context.features import (
    compute_minute_features,
    minute_bars_visible_at,
)
from trademind.market.router import DatasetRouter
from trademind.market.calendar import TradingCalendar
from trademind.context.builder import ContextBuilder


def make_min_bar(
    minute_offset: int,
    hour: int = 9,
    minute: int = 31,
    close: str = "1.500",
    volume: str = "1000",
    day: int = 8,
    month: int = 10,
    year: int = 2026,
    instrument_id: str = "513050.SH",
    provider: str = "fixture",
) -> MinuteBar:
    base_dt = datetime(year, month, day, hour, minute, tzinfo=BEIJING) + timedelta(minutes=minute_offset)
    c = Decimal(close)
    return MinuteBar(
        instrument_id=instrument_id,
        trade_date=base_dt.date(),
        bar_time=base_dt,
        open=c,
        high=c + Decimal("0.01"),
        low=c - Decimal("0.01"),
        close=c,
        volume=Decimal(volume),
        amount=c * Decimal(volume),
        provider=provider,
        source_time=base_dt,
        fetched_at=base_dt + timedelta(seconds=1),
        available_at=base_dt + timedelta(seconds=1),
        period="1m",
    )


def test_minute_bar_model():
    bar = make_min_bar(0)
    assert bar.instrument_id == "513050.SH"
    assert bar.period == "1m"
    assert bar.bar_time.tzinfo == BEIJING


def test_minute_bucket_no_lunch_crossing():
    # Morning: 09:31 -> 09:35, 11:30 -> 11:30
    b_0931 = datetime(2026, 10, 8, 9, 31, tzinfo=BEIJING)
    b_1130 = datetime(2026, 10, 8, 11, 30, tzinfo=BEIJING)
    assert get_minute_bucket(b_0931, "5m").minute == 35
    assert get_minute_bucket(b_1130, "5m").minute == 30
    assert get_minute_bucket(b_1130, "5m").hour == 11

    # Afternoon: 13:01 -> 13:05, never mixed with 11:30
    b_1301 = datetime(2026, 10, 8, 13, 1, tzinfo=BEIJING)
    bucket_1301 = get_minute_bucket(b_1301, "5m")
    assert bucket_1301.hour == 13
    assert bucket_1301.minute == 5


def test_aggregate_minute_bars_5m():
    # 5 bars from 09:31 to 09:35
    bars = [make_min_bar(i, hour=9, minute=31, close=str(1.500 + i * 0.01)) for i in range(5)]
    bars_5m = aggregate_minute_bars(bars, "5m")
    assert len(bars_5m) == 1
    b5 = bars_5m[0]
    assert b5.period == "5m"
    assert b5.bar_time.minute == 35
    assert b5.open == bars[0].open
    assert b5.close == bars[-1].close
    assert b5.volume == sum(b.volume for b in bars)


def test_normalize_minute_bars():
    b1 = make_min_bar(0, close="1.500")
    # Duplicate
    b2 = make_min_bar(0, close="1.500")
    # Bad price
    b_bad = make_min_bar(1, close="-1.000")
    normalized, report = normalize_minute_bars([b1, b2, b_bad])
    assert report.duplicates_removed == 1
    assert len(report.limit_violations) >= 1
    assert len(normalized) == 2


def test_compute_minute_features_and_regime():
    # Warmup < 15 bars -> regime UNKNOWN, atr_14 missing
    bars_short = [make_min_bar(i, close="1.500") for i in range(5)]
    vals, missing, regime = compute_minute_features(bars_short)
    assert regime == "UNKNOWN"
    assert "atr_14" in missing
    assert "vwap" in vals

    # 20 bars ascending -> regime TRENDING_UP
    bars_trend = [make_min_bar(i, close=str(1.500 + i * 0.005)) for i in range(20)]
    vals, missing, regime = compute_minute_features(bars_trend)
    assert regime == "TRENDING_UP"
    assert "atr_14" in vals
    assert "ret_5m" in vals
    assert "ret_15m" in vals
    assert vals["ret_5m"] > 0


def test_point_in_time_visibility():
    t0 = datetime(2026, 10, 8, 9, 35, tzinfo=BEIJING)
    # 10 bars from 09:31 to 09:40
    bars = [make_min_bar(i, hour=9, minute=31) for i in range(10)]
    visible = minute_bars_visible_at(bars, as_of=t0)
    # Only bars with bar_time <= 09:35 and available_at <= 09:35
    # Since available_at is bar_time + 1s, bar at 09:35 is available at 09:35:01 > 09:35:00
    # Bars at 09:31..09:34 available at <= 09:34:01 < 09:35:00
    assert len(visible) == 4
    for b in visible:
        assert b.bar_time <= t0
        assert b.available_at <= t0


@pytest.mark.parametrize("boundary", ["gap", "lunch", "overnight"])
def test_minute_windows_reset_at_discontinuity(boundary):
    previous = [make_min_bar(i, close="1.0") for i in range(20)]
    if boundary == "gap":
        latest = [make_min_bar(i, close="2.0") for i in range(21, 24)]
    elif boundary == "lunch":
        latest = [make_min_bar(i, hour=13, minute=1, close="2.0") for i in range(3)]
    else:
        latest = [make_min_bar(i, day=9, close="2.0") for i in range(3)]
    values, missing, regime = compute_minute_features(previous + latest)
    assert values["ret_1m"] == 0
    for name in ("ret_5m", "ret_15m", "atr_14", "volume_ratio"):
        assert name not in values
        assert "contiguous 1m" in missing[name]
    assert regime == "UNKNOWN"
    if boundary == "overnight":
        assert values["vwap"] == Decimal("2.0")


def test_minute_windows_recover_after_gap():
    bars = [make_min_bar(i, close="1.0") for i in range(20)]
    bars += [make_min_bar(i, close="2.0") for i in range(21, 37)]
    values, missing, regime = compute_minute_features(bars)
    assert not missing
    assert values["ret_15m"] == 0
    assert regime == "RANGING"


def test_aggregated_bars_are_not_one_minute_features():
    bars = aggregate_minute_bars([make_min_bar(i) for i in range(100)], "5m")
    values, missing, regime = compute_minute_features(bars)
    assert "vwap" in values
    assert "ret_1m" not in values
    assert "ret_15m" in missing
    assert regime == "UNKNOWN"


def test_vwap_does_not_fabricate_traded_amount():
    bar = make_min_bar(0).model_copy(update={"amount": Decimal("0")})
    values, missing, _ = compute_minute_features([bar])
    assert "vwap" not in values
    assert "traded amount" in missing["vwap"]


def test_context_builder_minute_snapshot(tmp_path):
    # Setup router with synthetic minute fixture
    min_dir = tmp_path / "fixtures" / "minute"
    min_dir.mkdir(parents=True)
    csv_file = min_dir / "513050.SH_1m.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("trade_date,bar_time,open,high,low,close,volume,amount\n")
        for i in range(20):
            t_str = (datetime(2026, 10, 8, 9, 31, tzinfo=BEIJING) + timedelta(minutes=i)).isoformat()
            f.write(f"2026-10-08,{t_str},1.50,1.52,1.49,1.51,1000,1510.0\n")

    router = DatasetRouter(pref=["fixture"], fixture_root=str(tmp_path / "fixtures"))
    cal = TradingCalendar(days=[date(2026, 10, 8)])
    builder = ContextBuilder(router=router, calendar=cal)

    as_of = datetime(2026, 10, 8, 9, 52, tzinfo=BEIJING)
    snap = builder.build_minute("513050.SH", as_of=as_of, period="1m")
    assert snap.instrument_id == "513050.SH"
    assert "close" in snap.features.values
    assert snap.content_hash
    assert "minute_bars" in snap.aux
