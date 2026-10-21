"""Deterministic feature computation over visible bars (TASK-09).

Facts only. A window that has not warmed up yields a missing_field with a
reason — never 0, never a fabricated value.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, getcontext
from typing import Any

from ..market.base import BEIJING, Bar, MinuteBar

getcontext().prec = 28

FEATURE_VERSION = "feat-2026-10-07"
MINUTE_FEATURE_VERSION = "minute-feat-2026-10-09"
Q = Decimal


def _mean(xs: list[Decimal]) -> Decimal:
    return sum(xs) / Q(len(xs))


def _stdev(xs: list[Decimal]) -> Decimal:
    if len(xs) < 2:
        return Q(0)
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / Q(len(xs) - 1)
    return var.sqrt()


def compute_features(bars: list[Bar]) -> tuple[dict[str, Decimal], dict[str, str]]:
    """bars must be ascending and fully visible at as_of (caller filters)."""
    values: dict[str, Decimal] = {}
    missing: dict[str, str] = {}
    if not bars:
        return values, {"bars": "no visible bars at as_of"}

    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    last = closes[-1]
    n = len(bars)

    def ret(lag: int) -> Decimal | None:
        return (last - closes[-1 - lag]) / closes[-1 - lag] if n > lag else None

    for lag, name in ((1, "ret_1d"), (5, "ret_5d"), (20, "ret_20d"), (60, "ret_60d")):
        r = ret(lag)
        if r is None:
            missing[name] = f"needs {lag + 1} bars, have {n}"
        else:
            values[name] = r.quantize(Q("0.000001"))

    for w in (5, 10, 20):
        if n >= w:
            values[f"ma{w}"] = _mean(closes[-w:]).quantize(Q("0.0001"))
        else:
            missing[f"ma{w}"] = f"needs {w} bars, have {n}"

    if n >= 21:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(n - 20, n)]
        values["vol_20d"] = _stdev(rets).quantize(Q("0.000001"))
    else:
        missing["vol_20d"] = f"needs 21 bars, have {n}"

    if n >= 20:
        base_vol = _mean(vols[-20:])
        if base_vol > 0:
            values["volume_ratio"] = (vols[-1] / base_vol).quantize(Q("0.0001"))
        else:
            missing["volume_ratio"] = "zero average volume"
    else:
        missing["volume_ratio"] = f"needs 20 bars, have {n}"

    if n >= 20:
        ma20 = values.get("ma20")
        if ma20:
            values["dist_ma20"] = ((last - ma20) / ma20).quantize(Q("0.000001"))

    hi, lo = max(b.high for b in bars), min(b.low for b in bars)
    if hi > 0:
        values["dist_high"] = ((last - hi) / hi).quantize(Q("0.000001"))
        values["dist_low"] = ((last - lo) / lo).quantize(Q("0.000001"))

    if bars[-1].pre_close:
        values["last_chg"] = ((last - bars[-1].pre_close) / bars[-1].pre_close).quantize(Q("0.000001"))
    values["close"] = last
    return values, missing


def bars_visible_at(bars: list[Bar], as_of) -> list[Bar]:
    """available_at <= as_of only; ascending order; last bar fully closed."""
    return sorted((b for b in bars if b.available_at <= as_of), key=lambda b: b.trade_date)


def minute_bars_visible_at(bars: list[MinuteBar], as_of) -> list[MinuteBar]:
    """available_at <= as_of and bar_time <= as_of; ascending order."""
    return sorted(
        (b for b in bars if b.available_at <= as_of and b.bar_time <= as_of and b.source_time <= as_of),
        key=lambda b: b.bar_time,
    )


def compute_minute_features(bars: list[MinuteBar]) -> tuple[dict[str, Decimal], dict[str, str], str]:
    """Calculate deterministic minute features and descriptive market regime.
    
    Returns (values, missing_reasons, regime).
    Regime is descriptive only: 'TRENDING_UP', 'TRENDING_DOWN', 'RANGING', or 'UNKNOWN'.
    """
    values: dict[str, Decimal] = {}
    missing: dict[str, str] = {}
    if not bars:
        return values, {"bars": "no visible minute bars at as_of"}, "UNKNOWN"

    # VWAP uses the latest Shanghai date; rolling windows use only the latest
    # uninterrupted 1-minute segment. Lunch, overnight and missing rows reset it.
    day = bars[-1].bar_time.astimezone(BEIJING).date()
    day_bars = [b for b in bars if b.bar_time.astimezone(BEIJING).date() == day]
    segment = [day_bars[-1]] if day_bars[-1].period == "1m" else []
    for b in reversed(day_bars[:-1]):
        if not segment or b.period != "1m" or segment[0].bar_time - b.bar_time != timedelta(minutes=1):
            break
        segment.insert(0, b)
    closes = [b.close for b in segment]
    vols = [b.volume for b in segment]
    last = day_bars[-1].close
    n = len(segment)
    values["close"] = last

    # Short period returns
    if n >= 2:
        values["ret_1m"] = ((last - closes[-2]) / closes[-2]).quantize(Q("0.000001"))
    else:
        missing["ret_1m"] = f"needs 2 contiguous 1m bars, have {n}"

    if n >= 6:
        values["ret_5m"] = ((last - closes[-6]) / closes[-6]).quantize(Q("0.000001"))
    else:
        missing["ret_5m"] = f"needs 6 contiguous 1m bars, have {n}"

    if n >= 16:
        values["ret_15m"] = ((last - closes[-16]) / closes[-16]).quantize(Q("0.000001"))
    else:
        missing["ret_15m"] = f"needs 16 contiguous 1m bars, have {n}"

    # VWAP
    total_vol = sum(b.volume for b in day_bars)
    if total_vol > 0:
        total_amt = sum(b.amount for b in day_bars)
        if total_amt > 0:
            values["vwap"] = (total_amt / total_vol).quantize(Q("0.0001"))
        else:
            missing["vwap"] = "missing positive traded amount for current date"
    else:
        missing["vwap"] = "zero total volume"

    # ATR 14
    if n >= 15:
        trs: list[Decimal] = []
        for i in range(1, n):
            h, l, prev_c = segment[i].high, segment[i].low, segment[i - 1].close
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        values["atr_14"] = _mean(trs[-14:]).quantize(Q("0.0001"))
    else:
        missing["atr_14"] = f"needs 15 contiguous 1m bars, have {n}"

    # Volume ratio
    if n >= 6:
        base_vol = _mean(vols[-6:-1])
        if base_vol > 0:
            values["volume_ratio"] = (vols[-1] / base_vol).quantize(Q("0.0001"))
        else:
            missing["volume_ratio"] = "zero base volume"
    else:
        missing["volume_ratio"] = f"needs 6 contiguous 1m bars, have {n}"

    # Descriptive Market Regime
    if n < 15:
        regime = "UNKNOWN"
    else:
        ma5 = _mean(closes[-5:])
        ma15 = _mean(closes[-15:])
        ret_15 = (last - closes[-15]) / closes[-15]
        if ma5 > ma15 and ret_15 > Decimal("0.001"):
            regime = "TRENDING_UP"
        elif ma5 < ma15 and ret_15 < Decimal("-0.001"):
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGING"

    return values, missing, regime
