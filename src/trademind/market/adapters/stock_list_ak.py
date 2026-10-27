"""AKShare-backed universe providers for the A-share discovery pool.

Real-endpoint status: NOT YET VERIFIED against live akshare — network
verification happens on the N150 runtime after deployment (TASK-20261027-03
acceptance requires a real scan run there). Column mappings below try
common header variants and fail loudly listing the observed columns when
none match, so a live schema mismatch surfaces as an explicit error, never
as silently empty data.

Board coverage (ARCH-028 v1): SH main board (60x), SZ main board (000/001/
002/003), ChiNext (300/301). STAR (68x) and BSE (8xx/4xx/92x) are excluded
— different lot and quote rules; revisit in a later task if ever needed.

Suspension: no provider is wired yet; the scan records ``suspension`` in
``missing_checks`` instead of pretending every stock trades.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..stock_discovery import StockUniverseEntry, entries_from_rows

SH_SOURCE_URL = "https://www.sse.com.cn/ (via akshare stock_info_sh_name_code)"
SZ_SOURCE_URL = "https://www.szse.cn/ (via akshare stock_info_sz_name_code)"


def _require_akshare() -> Any:
    try:
        import akshare  # noqa: PLC0415 — optional heavy import, deferred
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "akshare is required for stock universe discovery; "
            "install it (requirements.txt already pins it)"
        ) from exc
    return akshare


def _pick_column(columns: list[str], candidates: tuple[str, ...]) -> str:
    for cand in candidates:
        if cand in columns:
            return cand
    raise RuntimeError(
        f"universe endpoint schema mismatch: none of {candidates} found in "
        f"observed columns {sorted(columns)}"
    )


def _clean_frame(rows: Any, code_cols: tuple[str, ...], name_cols: tuple[str, ...],
                 date_cols: tuple[str, ...]) -> tuple[list[dict[str, Any]], bool]:
    """Normalize provider rows; returns (rows, has_list_date)."""
    columns = [str(c) for c in rows.columns]
    code_col = _pick_column(columns, code_cols)
    name_col = _pick_column(columns, name_cols)
    date_col = next((c for c in date_cols if c in columns), None)
    out: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        out.append(
            {
                "code": str(row[code_col]).strip(),
                "name": str(row[name_col]).strip(),
                "list_date": (str(row[date_col]).strip() if date_col else ""),
            }
        )
    return out, date_col is not None


def _by_prefix(rows: list[dict[str, Any]], prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
    return [r for r in rows if r["code"].startswith(prefixes)]


def fetch_universe_entries_v1() -> list[StockUniverseEntry]:
    """Fetch the v1 universe (SH/SZ main + ChiNext) with list dates.

    Calls akshare once per exchange; rows outside v1 board prefixes are
    dropped upstream so the discovery layer never sees them.
    """
    ak = _require_akshare()
    fetched_at = datetime.now(timezone.utc).isoformat()
    entries: list[StockUniverseEntry] = []

    sh_rows, sh_dated = _clean_frame(
        ak.stock_info_sh_name_code(symbol="主板A股"),
        code_cols=("证券代码", "stock_code", "code"),
        name_cols=("证券简称", "stock_name", "name"),
        date_cols=("上市日期", "list_date", "listing_date"),
    )
    sh_source = SH_SOURCE_URL if sh_dated else SH_SOURCE_URL + " [list_date column missing]"
    entries.extend(
        entries_from_rows(
            _by_prefix(sh_rows, ("60",)),
            board="sh_main",
            source_url=sh_source,
            fetched_at=fetched_at,
        )
    )

    sz_rows, sz_dated = _clean_frame(
        ak.stock_info_sz_name_code(symbol="A股列表"),
        code_cols=("A股代码", "证券代码", "code"),
        name_cols=("A股简称", "证券简称", "name"),
        date_cols=("A股上市日期", "上市日期", "list_date"),
    )
    sz_source = SZ_SOURCE_URL if sz_dated else SZ_SOURCE_URL + " [list_date column missing]"
    for board, prefixes in (
        ("sz_main", ("000", "001", "002", "003")),
        ("chinext", ("300", "301")),
    ):
        entries.extend(
            entries_from_rows(
                _by_prefix(sz_rows, prefixes),
                board=board,
                source_url=sz_source,
                fetched_at=fetched_at,
            )
        )
    return entries


__all__ = ["fetch_universe_entries_v1"]
