"""Trading calendar with explicit, confirmed coverage segments.

Coverage is expressed as confirmed contiguous segments of the source data:
consecutive trading days more than ``MAX_CONFIRMED_GAP_DAYS`` apart cannot be
one real holiday run, so the span between them is an unconfirmed hole — its
dates are UNKNOWN, never silently promoted to confirmed closure. Within a
confirmed segment a non-trading day is a fact: a weekend or holiday confirmed
by the source data. Outside every segment, navigation and membership raise
``CalendarCoverageError`` instead of clamping to a boundary date, and session
checks answer False (unknown is never promoted to open, and a covered day is
never invented beyond the data). Source provenance and the verification stamp
come from the ``.meta.json`` sidecar next to the CSV.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

from ..errors import CalendarCoverageError
from .base import BEIJING

_DEFAULT_CALENDAR = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "calendar_xshg.csv"
if not _DEFAULT_CALENDAR.exists():
    _DEFAULT_CALENDAR = Path(sys.prefix) / 'share' / 'trademind' / 'fixtures' / 'calendar_xshg.csv'

# The longest scheduled XSHG closure (Spring Festival) spans at most ~12
# calendar days including the weekends it absorbs. A jump of trading days
# beyond this threshold cannot be one holiday run, so the days between are
# missing data — an unconfirmed hole — rather than confirmed closure
# (ISSUE-20261013-07).
MAX_CONFIRMED_GAP_DAYS = 14


class TradingCalendar:
    def __init__(self, days: list[date], calendar_id: str = "XSHG",
                 source: str | None = None, version: str | None = None,
                 checked_at: str | None = None) -> None:
        self.calendar_id = calendar_id
        self._days = sorted(set(days))
        if not self._days:
            raise ValueError("empty trading calendar")
        self.coverage_start = self._days[0]
        self.coverage_end = self._days[-1]
        self._segments: list[tuple[date, date]] = []
        self._holes: list[tuple[date, date]] = []
        seg_start = prev = self._days[0]
        for d in self._days[1:]:
            if (d - prev).days > MAX_CONFIRMED_GAP_DAYS:
                self._segments.append((seg_start, prev))
                self._holes.append((prev + timedelta(days=1), d - timedelta(days=1)))
                seg_start = d
            prev = d
        self._segments.append((seg_start, prev))
        self.source = source
        self.version = version
        self.checked_at = checked_at
        self.meta_csv_mismatch = False

    @classmethod
    def load(cls, path: Path | str | None = None) -> "TradingCalendar":
        p = Path(path) if path else _DEFAULT_CALENDAR
        days: list[date] = []
        meta: dict = {}
        meta_path = p.with_suffix('.meta.json')
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                if not isinstance(meta, dict):
                    meta = {}
            except (OSError, ValueError):
                meta = {}
        raw_csv = p.read_bytes() if p.exists() else b''
        if raw_csv:
            lines = raw_csv.decode(encoding="utf-8").strip().splitlines()
            for line in lines[1:]:
                line = line.strip()
                if line:
                    days.append(date.fromisoformat(line.split(",")[0]))
        cal = cls(days, source=meta.get('source') or (str(p) if p.exists() else None),
                  version=meta.get('version'), checked_at=meta.get('checked_at'))
        # Provenance must match the data it describes: a sidecar whose hash
        # does not match the CSV is stale (half-applied update) and is
        # reported, never silently trusted (ISSUE-20261013-07).
        expected = meta.get('csv_sha256')
        if expected:
            cal.meta_csv_mismatch = expected != hashlib.sha256(raw_csv).hexdigest()
        return cal

    # -- coverage ------------------------------------------------------------------
    def covers(self, d: date) -> bool:
        """True only inside a confirmed segment; dates in unconfirmed holes
        and beyond the ends are unknown, not closed."""
        return any(s <= d <= e for s, e in self._segments)

    def coverage(self) -> dict:
        return {'calendar_id': self.calendar_id, 'source': self.source,
                'version': self.version, 'checked_at': self.checked_at,
                'coverage_start': self.coverage_start.isoformat(),
                'coverage_end': self.coverage_end.isoformat(),
                'trading_days': len(self._days),
                'confirmed_segments': [[s.isoformat(), e.isoformat()] for s, e in self._segments],
                'unconfirmed_gaps': [[s.isoformat(), e.isoformat()] for s, e in self._holes]}

    def coverage_warnings(self, today: date | None = None) -> list[str]:
        """Human-readable reasons the calendar needs attention before trading."""
        today = today or date.today()
        warnings: list[str] = []
        if not self.covers(today):
            warnings.append(
                f"calendar covers {self.coverage_start.isoformat()}..{self.coverage_end.isoformat()}; "
                f"{today.isoformat()} is beyond confirmed data")
        elif (self.coverage_end - today).days <= 20:
            warnings.append(
                f"calendar coverage ends {self.coverage_end.isoformat()}; update from the official source")
        for s, e in self._holes:
            warnings.append(
                f"calendar has an unconfirmed gap {s.isoformat()}..{e.isoformat()}; "
                "no official data covers these dates — obtain it before trading them")
        if self.meta_csv_mismatch:
            warnings.append("calendar metadata hash does not match the CSV; "
                            "the sidecar is stale — re-run the official merge")
        if not self.source or not self.checked_at:
            warnings.append("calendar source/checked_at metadata missing; provenance unverified")
        return warnings

    def _require_covered(self, d: date) -> None:
        if not self.covers(d):
            spans = ', '.join(f"{s.isoformat()}..{e.isoformat()}" for s, e in self._segments)
            raise CalendarCoverageError(
                f"calendar {self.calendar_id} has confirmed coverage {spans}; "
                f"{d.isoformat()} is outside confirmed data and its trading "
                f"status is unknown (never inferred to be a closure)")

    def _require_no_hole_between(self, a: date, b: date) -> None:
        """Navigation may not jump an unconfirmed hole: an answer on the far
        side would imply the hole's days were all closures."""
        for hs, he in self._holes:
            if a < hs and he < b:
                raise CalendarCoverageError(
                    f"calendar {self.calendar_id} has an unconfirmed gap "
                    f"{hs.isoformat()}..{he.isoformat()} between {a.isoformat()} "
                    f"and {b.isoformat()}; navigation refuses to step over unknown dates")

    # -- membership ----------------------------------------------------------------
    def is_trading_day(self, d: date) -> bool:
        self._require_covered(d)
        return d in self._days

    # -- navigation (never clamps to a boundary date) --------------------------------
    def prev(self, d: date, n: int = 1) -> date:
        self._require_covered(d)
        earlier = [x for x in self._days if x < d]
        if len(earlier) < n:
            raise CalendarCoverageError(
                f"calendar {self.calendar_id} has only {len(earlier)} covered trading days "
                f"before {d.isoformat()}; cannot go back {n}")
        result = earlier[-n]
        self._require_no_hole_between(result, d)
        return result

    def next(self, d: date, n: int = 1) -> date:
        self._require_covered(d)
        later = [x for x in self._days if x > d]
        if len(later) < n:
            raise CalendarCoverageError(
                f"calendar {self.calendar_id} has only {len(later)} covered trading days "
                f"after {d.isoformat()}; cannot go forward {n}")
        result = later[n - 1]
        self._require_no_hole_between(d, result)
        return result

    def last_on_or_before(self, dt: datetime) -> date:
        d = dt.astimezone(BEIJING).date()
        self._require_covered(d)
        on = [x for x in self._days if x <= d]
        return on[-1]  # non-empty: coverage_start <= d

    def window(self, start: date, end: date) -> list[date]:
        """Confirmed trading days inside the range; the range is clipped to
        coverage, and dates inside an unconfirmed hole contribute nothing.
        Callers needing completeness must check ``covers`` first — for a
        range crossing a hole it answers False."""
        return [x for x in self._days if start <= x <= end]

    def add_trading_days(self, d: date, n: int) -> date:
        self._require_covered(d)
        if n >= 0:
            later = [x for x in self._days if x >= d]
            if n >= len(later):
                raise CalendarCoverageError(
                    f"calendar {self.calendar_id} has only {len(later)} covered trading days "
                    f"on/after {d.isoformat()}; cannot add {n}")
            result = later[n]
            self._require_no_hole_between(d, result)
            return result
        earlier = [x for x in self._days if x <= d]
        if len(earlier) < 1 - n:
            raise CalendarCoverageError(
                f"calendar {self.calendar_id} has only {len(earlier)} covered trading days "
                f"on/before {d.isoformat()}; cannot subtract {-n}")
        result = earlier[len(earlier) - 1 + n]
        self._require_no_hole_between(result, d)
        return result

    # -- sessions --------------------------------------------------------------------
    def is_session_open(self, dt: datetime) -> bool:
        """Continuous-session check in market timezone. Auction windows are
        not 'open' for our EOD-first simulation. Never raises: an uncovered
        date answers False — unknown coverage can never look open."""
        try:
            if not self.is_trading_day(dt.astimezone(BEIJING).date()):
                return False
        except CalendarCoverageError:
            return False
        minutes = dt.astimezone(BEIJING).strftime("%H:%M")
        return "09:30" <= minutes < "11:30" or "13:00" <= minutes < "14:57"

    def is_session_open_confirmed(self, dt: datetime) -> bool:
        """Session-open answer backed by covered data; False otherwise."""
        if not self.covers(dt.astimezone(BEIJING).date()):
            return False
        return self.is_session_open(dt)
