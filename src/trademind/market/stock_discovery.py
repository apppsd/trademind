"""Read-only A-share universe discovery for the wide-observation pool.

TASK-20261027-03 / ARCH-028 observation layer: assembles a candidate pool
from provider-supplied universe entries and filters it with programmatic,
fully explainable criteria. Zero model calls, zero orders, zero cash
operations — this module has no dependency on pipeline, models, ledger or
OMS, so it cannot place orders or spend budget by construction.

First version covers SH main board (60x), SZ main board (00x) and ChiNext
(30x). STAR market (68x, 200-share lots) and BSE (8xx/4xx/92x) are excluded
upstream by the provider because their lot/quote rules differ materially.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Protocol

# ARCH-028 proposal value; Task27-04 calibrates against real samples before
# any decision-layer use. Observation layer only records it.
OBSERVATION_MIN_LISTING_DAYS = 60

# Machine-readable exclusion reasons; every excluded entry carries exactly one.
REASON_ST = "st_name"
REASON_SUSPENDED = "suspended"
REASON_RECENT_LISTING = "recent_listing"
REASON_MISSING_NAME = "missing_name"
REASON_MISSING_LIST_DATE = "missing_list_date"

ST_MARKERS = ("ST", "*ST", "退")


@dataclass(frozen=True)
class StockUniverseEntry:
    """One raw universe row as supplied by a provider (no judgement yet)."""

    code: str
    name: str | None
    board: str  # 'sh_main' | 'sz_main' | 'chinext'
    list_date: date | None
    suspended: bool = False
    source_url: str = ""
    source_time: str = ""  # provider-side data timestamp, verbatim
    fetched_at: str = ""  # ISO instant the row was fetched


@dataclass(frozen=True)
class StockPoolCandidate:
    """A pool member: observation-layer qualified, decision NOT implied."""

    code: str
    name: str
    board: str
    list_date: str  # ISO date
    source_url: str
    source_time: str
    fetched_at: str


@dataclass(frozen=True)
class StockPoolExcluded:
    code: str
    reason: str
    detail: str  # human-readable, e.g. the name or the list date seen


@dataclass(frozen=True)
class StockDiscoveryResult:
    """Deterministic output: same entries + same clock → identical result."""

    generated_at: str
    candidates: tuple[StockPoolCandidate, ...]
    excluded: tuple[StockPoolExcluded, ...]
    missing_checks: tuple[str, ...] = field(default=())
    # missing_checks lists criteria that could NOT be evaluated at all (e.g.
    # no suspension provider configured). Per-issue absence is expressed via
    # exclusion reasons, never silently treated as passing.

    @property
    def counts(self) -> dict[str, int]:
        by_reason: dict[str, int] = {}
        for ex in self.excluded:
            by_reason[ex.reason] = by_reason.get(ex.reason, 0) + 1
        return {
            "scanned": len(self.candidates) + len(self.excluded),
            "candidates": len(self.candidates),
            "excluded": len(self.excluded),
            "excluded_by_reason": by_reason,
        }


class SuspensionProvider(Protocol):
    """Optional provider marking suspended codes for the scan date."""

    def suspended_codes(self, on: date) -> set[str]: ...


def is_name_st(name: str | None) -> bool:
    """Name-based ST detection (self-contained, no extra endpoint).

    Uses uppercase containment of known markers, matching exchange naming
    ('ST', '*ST', delisting-awareness '退'). None handled by caller.
    """
    if not name:
        return False
    upper = name.upper()
    return any(marker in upper for marker in ST_MARKERS)


def _to_pool_candidate(entry: StockUniverseEntry) -> StockPoolCandidate:
    assert entry.name is not None and entry.list_date is not None  # filtered upstream
    return StockPoolCandidate(
        code=entry.code,
        name=entry.name,
        board=entry.board,
        list_date=entry.list_date.isoformat(),
        source_url=entry.source_url,
        source_time=entry.source_time,
        fetched_at=entry.fetched_at,
    )


def discover_stock_pool(
    entries: list[StockUniverseEntry],
    now: datetime,
    calendar: Any,
    suspension_provider: SuspensionProvider | None = None,
    min_listing_days: int = OBSERVATION_MIN_LISTING_DAYS,
) -> StockDiscoveryResult:
    """Filter raw universe entries into the observation pool.

    Criteria (ARCH-028 observation layer), each producing an explainable
    exclusion reason:
      1. non-empty name, not ST/delisting-marker  → REASON_MISSING_NAME / REASON_ST
      2. not suspended on the scan date           → REASON_SUSPENDED
      3. listed at least ``min_listing_days`` trading days ago
                                                  → REASON_RECENT_LISTING /
                                                    REASON_MISSING_LIST_DATE

    Calendar coverage edges degrade conservatively and explainably: a
    list_date before the calendar's covered start counts as sufficiently
    old; the check is skipped (recorded in missing_checks) only if the
    calendar cannot answer at all.
    """
    generated_at = now.isoformat()
    missing_checks: list[str] = []
    suspended_codes: set[str] = set()
    if suspension_provider is None:
        missing_checks.append("suspension")
    else:
        suspended_codes = suspension_provider.suspended_codes(now.date())

    candidates: list[StockPoolCandidate] = []
    excluded: list[StockPoolExcluded] = []
    for entry in entries:
        if not entry.name or not entry.name.strip():
            excluded.append(StockPoolExcluded(entry.code, REASON_MISSING_NAME, "name missing"))
            continue
        if is_name_st(entry.name):
            excluded.append(StockPoolExcluded(entry.code, REASON_ST, entry.name))
            continue
        if entry.code in suspended_codes:
            excluded.append(StockPoolExcluded(entry.code, REASON_SUSPENDED, entry.name))
            continue
        if entry.list_date is None:
            excluded.append(
                StockPoolExcluded(entry.code, REASON_MISSING_LIST_DATE, "list_date missing")
            )
            continue
        old_enough, checkable = _listed_at_least(
            entry.list_date, now.date(), calendar, min_listing_days
        )
        if not checkable:
            if "listing_age" not in missing_checks:
                missing_checks.append("listing_age")
        elif not old_enough:
            excluded.append(
                StockPoolExcluded(
                    entry.code,
                    REASON_RECENT_LISTING,
                    f"listed {entry.list_date.isoformat()}, <{min_listing_days} trading days",
                )
            )
            continue
        candidates.append(_to_pool_candidate(entry))

    return StockDiscoveryResult(
        generated_at=generated_at,
        candidates=tuple(candidates),
        excluded=tuple(excluded),
        missing_checks=tuple(missing_checks),
    )


def _as_date(value: Any) -> date:
    """Accept date/datetime/ISO-string coverage bounds (calendar returns date)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _listed_at_least(
    list_date: date, today: date, calendar: Any, min_days: int
) -> tuple[bool, bool]:
    """(old_enough, checkable) for the listing-age criterion.

    Trading-day #1 is the listing day itself, so ``min_days`` trading days
    are complete once ``add_trading_days(list_date, min_days - 1)`` has
    passed. Conservative on calendar edges: a list_date before the
    calendar's coverage start counts as sufficiently old (it cannot be a
    recent listing); a list_date beyond coverage, or an age computation
    crossing an unconfirmed gap, is reported uncheckable.
    """
    try:
        coverage_start = _as_date(calendar.coverage_start)
        if list_date < coverage_start:
            return True, True
        coverage_end = _as_date(calendar.coverage_end)
        if list_date > coverage_end:
            return False, False
        threshold = calendar.add_trading_days(list_date, min_days - 1)
    except Exception:
        return False, False
    return threshold <= today, True


def result_to_dict(result: StockDiscoveryResult) -> dict[str, Any]:
    """Serializable form used by the scan script (stable field order)."""
    return {
        "generated_at": result.generated_at,
        "missing_checks": list(result.missing_checks),
        "counts": result.counts,
        "candidates": [asdict(c) for c in result.candidates],
        "excluded": [asdict(e) for e in result.excluded],
    }


def entries_from_rows(
    rows: list[dict[str, Any]],
    board: str,
    source_url: str,
    fetched_at: str,
    name_key: str = "name",
    code_key: str = "code",
    list_date_key: str = "list_date",
) -> list[StockUniverseEntry]:
    """Adapter helper: map provider rows to universe entries.

    Missing/invalid fields become None (never fabricated); list_date values
    are parsed from ISO or compact YYYYMMDD forms.
    """
    entries: list[StockUniverseEntry] = []
    for row in rows:
        code = str(row.get(code_key) or "").strip()
        if not code:
            continue
        raw_date = row.get(list_date_key)
        parsed_date: date | None = None
        if raw_date:
            text = str(raw_date).strip()
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    parsed_date = datetime.strptime(text, fmt).date()
                    break
                except ValueError:
                    continue
        entries.append(
            StockUniverseEntry(
                code=code,
                name=(str(row.get(name_key)).strip() if row.get(name_key) else None),
                board=board,
                list_date=parsed_date,
                source_url=source_url,
                source_time=str(row.get("source_time") or ""),
                fetched_at=fetched_at,
            )
        )
    return entries


__all__ = [
    "OBSERVATION_MIN_LISTING_DAYS",
    "REASON_ST",
    "REASON_SUSPENDED",
    "REASON_RECENT_LISTING",
    "REASON_MISSING_NAME",
    "REASON_MISSING_LIST_DATE",
    "StockUniverseEntry",
    "StockPoolCandidate",
    "StockPoolExcluded",
    "StockDiscoveryResult",
    "SuspensionProvider",
    "is_name_st",
    "discover_stock_pool",
    "result_to_dict",
    "entries_from_rows",
]
