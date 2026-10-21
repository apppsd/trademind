"""Offline merge of an operator-supplied official trading calendar.

This module never touches the network. The operator downloads the official
exchange calendar (first-hand source), converts it to the fixture CSV shape
(a ``date`` header plus ISO trading-day rows) and runs :func:`merge_official_calendar`
to replace/extend the local calendar and write the ``.meta.json`` sidecar
that ``TradingCalendar.load()`` reports as provenance (ISSUE-20261013-07).

Policy: only years the official source actually announces may enter the
data; this module never invents trading days to fill gaps.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..errors import ConfigError
from .calendar import TradingCalendar


def parse_calendar_csv(text: str, origin: str) -> list[date]:
    """Parse the ``date``-header CSV shape; reject anything unparsable."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines or lines[0].split(',')[0].lower() != 'date':
        raise ConfigError(f"{origin}: expected a 'date' header row")
    days: list[date] = []
    for line in lines[1:]:
        raw = line.split(',')[0]
        try:
            days.append(date.fromisoformat(raw))
        except ValueError as exc:
            raise ConfigError(f"{origin}: unparsable calendar row {raw!r}") from exc
    if not days:
        raise ConfigError(f"{origin}: calendar contains no trading days")
    return sorted(set(days))


def merge_official_calendar(source_csv: Path, target_csv: Path, source: str,
                            version: str | None = None, allow_shrink: bool = False) -> dict:
    """Merge the operator's official calendar into ``target_csv``.

    The official input is a complete statement of the trading days for its
    own coverage. Its valid coverage interval is every existing confirmed
    segment that overlaps ``[incoming[0], incoming[-1]]``: those segments are
    REPLACED by the incoming set — including days the previous file had that
    the official source withdrew — while segments entirely outside the
    interval are preserved untouched as history. A purely later official file
    (starting after every existing day) therefore extends the calendar
    without touching history. When the merge would pull the overall coverage
    end backwards (a genuine withdrawal of recent data), it is refused
    without ``allow_shrink`` so silent data loss cannot masquerade as a
    correction. The CSV and its sidecar are staged through temp files and
    the sidecar records the CSV's sha256, so a half-applied update is
    detectable by ``TradingCalendar.load()`` (ISSUE-20261013-07).
    """
    source_csv, target_csv = Path(source_csv), Path(target_csv)
    incoming = parse_calendar_csv(source_csv.read_text(encoding='utf-8'), str(source_csv))
    existing: list[date] = []
    if target_csv.exists():
        existing = parse_calendar_csv(target_csv.read_text(encoding='utf-8'), str(target_csv))
    lo, hi = incoming[0], incoming[-1]
    replaced: set[date] = set()
    kept: set[date] = set()
    if existing:
        probe = TradingCalendar(existing)
        for seg_start, seg_end in probe._segments:
            if seg_end >= lo and seg_start <= hi:
                replaced.update(d for d in existing if seg_start <= d <= seg_end)
            else:
                kept.update(d for d in existing if seg_start <= d <= seg_end)
    # The overlapping segments are withdrawn wholesale: everything they held
    # is gone unless the official set re-lists it; out-of-interval history
    # and the official set together form the merged calendar.
    merged = sorted(kept | set(incoming))
    if existing and merged[-1] < existing[-1] and not allow_shrink:
        raise ConfigError(
            f"incoming calendar pulls coverage end back to {merged[-1].isoformat()} "
            f"(existing {existing[-1].isoformat()}); refusing to shrink coverage "
            "without allow_shrink")
    target_csv.parent.mkdir(parents=True, exist_ok=True)
    body = 'date\n' + '\n'.join(d.isoformat() for d in merged) + '\n'
    meta = {'source': source, 'version': version,
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'coverage_start': merged[0].isoformat(),
            'coverage_end': merged[-1].isoformat(),
            'trading_days': len(merged),
            'replaced_range': [lo.isoformat(), hi.isoformat()],
            'replaced_days': len(replaced),
            'carried_history_days': len(kept),
            'csv_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}
    temporary = target_csv.with_suffix('.tmp')
    temporary_meta = target_csv.with_suffix('.meta.tmp')
    # Bytes, not text mode: the recorded sha256 must match the file exactly
    # (text mode would translate \n to \r\n on Windows).
    temporary.write_bytes(body.encode('utf-8'))
    temporary_meta.write_bytes(
        json.dumps(meta, ensure_ascii=False, indent=2).encode('utf-8'))
    temporary.replace(target_csv)
    temporary_meta.replace(target_csv.with_suffix('.meta.json'))
    return meta
