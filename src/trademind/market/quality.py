"""Data quality checks (TASK-08): dedupe, ordering, corrections, limit moves.

Deterministic normalization:
1. exact duplicates collapse to one;
2. per (trade_date, provider) the highest revision wins (corrections supersede);
3. across providers on the same date, lowest provider name wins ties;
4. output sorted ascending; nothing silently dropped — counts are reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from .base import BEIJING, Bar, MinuteBar


@dataclass
class QualityReport:
    instrument_id: str = ""
    rows_in: int = 0
    rows_out: int = 0
    duplicates_removed: int = 0
    superseded_corrections: int = 0
    limit_violations: list[str] = field(default_factory=list)
    # routing evidence (TASK-20261014-04): filled by DatasetRouter, not normalize
    selected_provider: str = ""
    source_rejections: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.limit_violations


def normalize_bars(bars: list[Bar], price_limit_ratio: Decimal | None = None) -> tuple[list[Bar], QualityReport]:
    report = QualityReport()
    if not bars:
        return [], report
    report.instrument_id = bars[0].instrument_id
    report.rows_in = len(bars)

    exact: dict[tuple, Bar] = {}
    for b in bars:
        key = (b.instrument_id, b.trade_date, b.provider, b.revision,
               str(b.open), str(b.close), str(b.volume))
        exact[key] = b  # later fetch wins for identical rows
    report.duplicates_removed = report.rows_in - len(exact)

    by_date: dict[date, list[Bar]] = {}
    for b in exact.values():
        by_date.setdefault(b.trade_date, []).append(b)

    out: list[Bar] = []
    for d in sorted(by_date):
        # highest revision wins; within a revision, lowest provider name wins ties
        max_rev = max(b.revision for b in by_date[d])
        winner = sorted((b for b in by_date[d] if b.revision == max_rev),
                        key=lambda b: b.provider)[0]
        if len(by_date[d]) > 1:
            report.superseded_corrections += len(by_date[d]) - 1
        if price_limit_ratio is not None and winner.pre_close:
            move = abs(winner.close - winner.pre_close) / winner.pre_close
            if move > price_limit_ratio * Decimal("1.001"):
                report.limit_violations.append(
                    f"{d.isoformat()}: move {move} exceeds limit {price_limit_ratio}")
        out.append(winner)

    out.sort(key=lambda b: b.trade_date)
    report.rows_out = len(out)
    return out, report


def get_minute_bucket(dt: datetime, period: str) -> datetime:
    dt_bj = dt.astimezone(BEIJING)
    m = dt_bj.minute
    h = dt_bj.hour
    mins = h * 60 + m
    step = 5 if period == "5m" else 15
    if mins <= 690:
        rem = (mins - 570) % step
        target_mins = mins if rem == 0 else mins + (step - rem)
        target_mins = min(690, target_mins)
    else:
        rem = (mins - 780) % step
        target_mins = mins if rem == 0 else mins + (step - rem)
        target_mins = min(900, target_mins)

    th = target_mins // 60
    tm = target_mins % 60
    return dt_bj.replace(hour=th, minute=tm, second=0, microsecond=0)


def aggregate_minute_bars(
    bars_1m: list[MinuteBar],
    target_period: Literal["5m", "15m"],
) -> list[MinuteBar]:
    if not bars_1m:
        return []
    buckets: dict[datetime, list[MinuteBar]] = {}
    for b in sorted(bars_1m, key=lambda x: x.bar_time):
        bucket_time = get_minute_bucket(b.bar_time, target_period)
        buckets.setdefault(bucket_time, []).append(b)

    out: list[MinuteBar] = []
    for bucket_time in sorted(buckets):
        grp = buckets[bucket_time]
        out.append(
            MinuteBar(
                instrument_id=grp[0].instrument_id,
                trade_date=grp[-1].trade_date,
                bar_time=bucket_time,
                open=grp[0].open,
                high=max(b.high for b in grp),
                low=min(b.low for b in grp),
                close=grp[-1].close,
                volume=sum(b.volume for b in grp),
                amount=sum(b.amount for b in grp),
                provider=grp[-1].provider,
                source_time=bucket_time,
                fetched_at=max(b.fetched_at for b in grp),
                available_at=max(b.available_at for b in grp),
                period=target_period,
                revision=max(b.revision for b in grp),
                unit=grp[-1].unit,
            )
        )
    return out


def cross_source_close_divergence(
    bars_a: list[MinuteBar],
    bars_b: list[MinuteBar],
) -> tuple[Decimal | None, datetime | None]:
    """Max relative close divergence over rows present in BOTH windows at the
    same bar_time (same closed minute, same unit). Returns (worst_ratio, bar_time);
    (None, None) when there is nothing comparable — no overlap is not evidence
    of divergence (TASK-20261014-04)."""
    by_time = {b.bar_time: b.close for b in bars_a}
    worst: Decimal | None = None
    worst_t: datetime | None = None
    for b in bars_b:
        other = by_time.get(b.bar_time)
        if other is None or other == 0:
            continue
        div = abs(b.close - other) / other
        if worst is None or div > worst:
            worst, worst_t = div, b.bar_time
    return worst, worst_t


def normalize_minute_bars(
    bars: list[MinuteBar],
    as_of: datetime | None = None,
) -> tuple[list[MinuteBar], QualityReport]:
    report = QualityReport()
    if not bars:
        return [], report
    report.instrument_id = bars[0].instrument_id
    report.rows_in = len(bars)

    valid_bars = [b for b in bars if as_of is None or b.available_at <= as_of]

    exact: dict[tuple, MinuteBar] = {}
    for b in valid_bars:
        key = (
            b.instrument_id,
            b.bar_time.isoformat(),
            b.period,
            b.provider,
            b.revision,
            str(b.open),
            str(b.close),
            str(b.volume),
        )
        exact[key] = b
    report.duplicates_removed = report.rows_in - len(exact)

    by_time: dict[datetime, list[MinuteBar]] = {}
    for b in exact.values():
        by_time.setdefault(b.bar_time, []).append(b)

    out: list[MinuteBar] = []
    for t in sorted(by_time):
        max_rev = max(b.revision for b in by_time[t])
        winner = sorted(
            (b for b in by_time[t] if b.revision == max_rev),
            key=lambda b: b.provider,
        )[0]
        if len(by_time[t]) > 1:
            report.superseded_corrections += len(by_time[t]) - 1

        if winner.open <= 0 or winner.close <= 0 or winner.high <= 0 or winner.low <= 0:
            report.limit_violations.append(f"{t.isoformat()}: non-positive price")
        elif winner.high < max(winner.open, winner.close) or winner.low > min(winner.open, winner.close):
            report.limit_violations.append(f"{t.isoformat()}: high/low violation")

        out.append(winner)

    out.sort(key=lambda b: b.bar_time)
    report.rows_out = len(out)
    return out, report
