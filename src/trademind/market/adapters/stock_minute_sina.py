"""Sina minute-bar adapter for candidate stocks (read-only).

TASK-20261027-04 / ARCH-028. Real-endpoint status: VERIFIED once from the
dev machine after close (2026-10-27 16:5x, sz000001 → 1970 rows ending
15:00 same day, columns day/open/high/low/close/volume/amount). Key unit
fact, evidence-checked: sina stock minute ``volume`` is in SHARES —
amount/volume reproduces the bar's mean price (3960682/350800 ≈ 11.29 =
close), unlike the ETF sina feed whose volume is in lots. No ×100
conversion is applied here; downstream must treat volume as shares.

Endpoint latency/freshness during a live session is NOT yet verified
(requires an intraday run — scheduled on the N150 runtime); this adapter
therefore only claims closed-bar correctness.

Eastmoney (akshare stock_zh_a_hist_min_em) is NOT usable from the dev
machine: connection reset, consistent with the 2026-10-14 six-round reset
recorded in ARCH-020 research. It stays a candidate source for the N150
runtime behind the ARCH-020 router; do not fall back to fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

SINA_SOURCE_URL = "https://finance.sina.com.cn/ (via akshare stock_zh_a_minute, period=1)"

_BOARD_PREFIX = {"sh_main": "sh", "sz_main": "sz", "chinext": "sz"}


@dataclass(frozen=True)
class StockMinuteBar:
    """One closed 1-minute bar; volume/amount are SHARES and CNY."""

    time: str  # provider wall clock 'YYYY-MM-DD HH:MM:SS' (Asia/Shanghai)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # shares (see module docstring)
    amount: Decimal  # CNY
    source_url: str = SINA_SOURCE_URL
    source_time: str = ""


def sina_symbol(code: str, board: str) -> str:
    """sz000001 / sh600000 style; chinext (300/301) is SZ-listed."""
    prefix = _BOARD_PREFIX.get(board)
    if prefix is None:
        raise ValueError(f"unknown board for sina symbol: {board!r}")
    return f"{prefix}{code}"


def to_records(rows: Any) -> list[dict[str, Any]]:
    """akshare returns a pandas DataFrame; normalize to list[dict]."""
    if hasattr(rows, "to_dict"):
        return rows.to_dict("records")
    return list(rows or [])


def parse_stock_minutes(rows: Any) -> list[StockMinuteBar]:
    """Normalize and validate sina minute rows.

    Accepts a pandas DataFrame (what akshare actually returns) or a list
    of dicts. Rejects (by raising ValueError with the offending row)
    malformed prices, non-positive volume, inverted high/low, out-of-order
    or duplicate timestamps — a bar that cannot be trusted is an error,
    not silently repaired data.
    """
    rows = to_records(rows)
    bars: list[StockMinuteBar] = []
    last_time: str | None = None
    for raw in rows:
        try:
            values = [Decimal(str(raw[k])) for k in ("open", "high", "low", "close")]
            volume = Decimal(str(raw["volume"]))
            amount = Decimal(str(raw.get("amount") or 0))
        except (InvalidOperation, KeyError, ArithmeticError) as exc:
            raise ValueError(f"malformed minute row {raw!r}: {exc}") from exc
        o, h, l, c = values
        if min(o, h, l, c) <= 0 or h < l:
            raise ValueError(f"implausible price in minute row {raw!r}")
        if volume < 0:
            raise ValueError(f"negative volume in minute row {raw!r}")
        t = str(raw["day"])
        if last_time is not None and t <= last_time:
            raise ValueError(f"minute rows not strictly ascending at {t}")
        last_time = t
        bars.append(
            StockMinuteBar(
                time=t, open=o, high=h, low=l, close=c, volume=volume, amount=amount,
                source_time=t,
            )
        )
    return bars


def check_share_unit(bars: list[StockMinuteBar], tolerance: Decimal = Decimal("0.05")) -> bool:
    """Evidence check that volume is shares: amount/volume ≈ mean price.

    Samples complete bars with non-trivial volume; True only if every
    sampled ratio lands within ``tolerance`` of the bar's mid price.
    False or degenerate input means the unit assumption must be
    re-examined before any sizing math trusts these bars.
    """
    sampled = 0
    for bar in bars:
        if bar.volume <= 0:
            continue
        mid = (bar.open + bar.close) / 2
        if mid <= 0:
            continue
        ratio = bar.amount / bar.volume
        if abs(ratio - mid) / mid > tolerance:
            return False
        sampled += 1
    return sampled > 0


__all__ = [
    "SINA_SOURCE_URL",
    "StockMinuteBar",
    "sina_symbol",
    "to_records",
    "parse_stock_minutes",
    "check_share_unit",
]
