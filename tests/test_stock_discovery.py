"""TASK-20261027-03: stock universe discovery and read-only pool tests."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from trademind.market.adapters import stock_list_ak
from trademind.market.calendar import TradingCalendar
from trademind.market.stock_discovery import (
    REASON_MISSING_LIST_DATE,
    REASON_MISSING_NAME,
    REASON_RECENT_LISTING,
    REASON_ST,
    REASON_SUSPENDED,
    StockUniverseEntry,
    discover_stock_pool,
    entries_from_rows,
    is_name_st,
    result_to_dict,
)
import scripts.scan_stock_candidates as scan_mod
from scripts.scan_stock_candidates import atomic_write_json, scan


def _calendar(days_back: int = 160, forward: int = 80, today: date | None = None) -> TradingCalendar:
    today = today or date(2026, 4, 1)
    days: list[date] = []
    cursor = today - timedelta(days=days_back * 3 // 2)  # generous window
    end = today + timedelta(days=forward * 3 // 2)
    while cursor <= end:
        if cursor.weekday() < 5:  # weekdays only; exact holidays irrelevant here
            days.append(cursor)
        cursor += timedelta(days=1)
    return TradingCalendar(days=days, calendar_id="XTST")


def _entry(code: str, name: str = "平安银行", list_date: date | None = date(2015, 1, 1),
           board: str = "sz_main", suspended: bool = False) -> StockUniverseEntry:
    return StockUniverseEntry(
        code=code, name=name, board=board, list_date=list_date, suspended=suspended,
        source_url="fixture://list", source_time="2026-04-01",
        fetched_at="2026-04-01T02:00:00+00:00",
    )


NOW = datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc)


class _NoSuspensions:
    def suspended_codes(self, on: date) -> set[str]:
        return set()


class _Suspended:
    def __init__(self, codes: set[str]):
        self.codes = codes

    def suspended_codes(self, on: date) -> set[str]:
        return self.codes


# -- name-based ST detection ---------------------------------------------------

def test_is_name_st_variants():
    assert is_name_st("ST 康美")
    assert is_name_st("*ST 海航")
    assert is_name_st("退市海润")
    assert is_name_st("st 低价")  # case-insensitive by design
    assert not is_name_st("平安银行")
    assert not is_name_st("")
    assert not is_name_st(None)


# -- discovery criteria --------------------------------------------------------

def test_qualified_stock_is_kept_with_provenance():
    result = discover_stock_pool([_entry("000001")], NOW, _calendar(), _NoSuspensions())
    assert result.counts["candidates"] == 1
    cand = result.candidates[0]
    assert (cand.code, cand.name, cand.board) == ("000001", "平安银行", "sz_main")
    assert cand.list_date == "2015-01-01"
    assert cand.source_url == "fixture://list"
    assert result.missing_checks == ()


def test_st_name_excluded_with_reason():
    result = discover_stock_pool([_entry("600000", "ST 康美")], NOW, _calendar(), _NoSuspensions())
    assert result.counts["candidates"] == 0
    assert result.excluded[0].reason == REASON_ST
    assert result.excluded[0].detail == "ST 康美"


def test_suspended_excluded_only_via_provider():
    result = discover_stock_pool(
        [_entry("000002", "万科A")], NOW, _calendar(), _Suspended({"000002"})
    )
    assert result.excluded[0].reason == REASON_SUSPENDED


def test_missing_suspension_provider_recorded_not_faked():
    result = discover_stock_pool([_entry("000001")], NOW, _calendar(), None)
    assert result.counts["candidates"] == 1  # kept, but…
    assert result.missing_checks == ("suspension",)  # …criterion marked unevaluated


def test_recent_listing_excluded():
    cal = _calendar()
    recent = cal.add_trading_days(date(2026, 4, 1), -10)  # 10 trading days ago
    result = discover_stock_pool([_entry("301001", "新股", list_date=recent)],
                                 NOW, cal, _NoSuspensions())
    assert result.excluded[0].reason == REASON_RECENT_LISTING
    assert "60 trading days" in result.excluded[0].detail


def test_missing_list_date_excluded():
    result = discover_stock_pool([_entry("000004", list_date=None)], NOW, _calendar(),
                                 _NoSuspensions())
    assert result.excluded[0].reason == REASON_MISSING_LIST_DATE


def test_missing_name_excluded():
    result = discover_stock_pool([_entry("000005", name=None)], NOW, _calendar(),
                                 _NoSuspensions())
    assert result.excluded[0].reason == REASON_MISSING_NAME
    # blank-name variant maps to the same explicit reason
    result2 = discover_stock_pool([_entry("000006", name="  ")], NOW, _calendar(),
                                  _NoSuspensions())
    assert result2.excluded[0].reason == REASON_MISSING_NAME


def test_list_date_before_coverage_counts_as_old():
    cal = _calendar(days_back=200)  # coverage starts ~mid-2025
    old = date(2010, 6, 15)
    assert old < cal.coverage_start
    result = discover_stock_pool([_entry("600001", list_date=old)], NOW, cal, _NoSuspensions())
    assert result.counts["candidates"] == 1


def test_list_date_beyond_coverage_uncheckable_kept_and_flagged():
    cal = _calendar(days_back=50)  # coverage ends well before NOW
    future = date(2027, 1, 4)
    result = discover_stock_pool([_entry("600002", "未来上市", list_date=future)],
                                 NOW, cal, _NoSuspensions())
    assert result.counts["candidates"] == 1
    assert "listing_age" in result.missing_checks


def test_recent_listing_near_coverage_end_conservatively_excluded():
    # A stock listed in the last 60 trading days of calendar coverage cannot
    # have reached the threshold — exclude, never silently keep.
    cal = _calendar()
    end = date.fromisoformat(cal.coverage_end) if isinstance(cal.coverage_end, str) else cal.coverage_end
    recent = cal.add_trading_days(end, -10)
    result = discover_stock_pool([_entry("600003", "年底次新", list_date=recent)],
                                 NOW, cal, _NoSuspensions())
    assert result.counts["candidates"] == 0
    assert result.excluded[0].reason == REASON_RECENT_LISTING


# -- determinism / serialization ----------------------------------------------

def test_discovery_is_deterministic():
    entries = [_entry("000001"), _entry("600000", "ST 康美"), _entry("301001", list_date=None)]
    a = discover_stock_pool(entries, NOW, _calendar(), _NoSuspensions())
    b = discover_stock_pool(entries, NOW, _calendar(), _NoSuspensions())
    assert result_to_dict(a) == result_to_dict(b)


def test_result_to_dict_shape():
    payload = result_to_dict(
        discover_stock_pool([_entry("000001")], NOW, _calendar(), _NoSuspensions())
    )
    assert payload["counts"]["scanned"] == 1
    assert payload["counts"]["candidates"] == 1
    cand = payload["candidates"][0]
    assert set(cand) == {"code", "name", "board", "list_date", "source_url",
                         "source_time", "fetched_at"}
    json.dumps(payload, ensure_ascii=False)  # must stay serializable


# -- entries_from_rows parsing -------------------------------------------------

def test_entries_from_rows_parses_dates_and_keeps_missing_as_none():
    rows = [
        {"code": "000001", "name": "平安银行", "list_date": "1991-04-03"},
        {"code": "000002", "name": "万科A", "list_date": "19910129"},
        {"code": "000003", "name": "坏行", "list_date": "not-a-date"},
        {"code": "000004", "name": None, "list_date": "2020-01-01"},
        {"code": "", "name": "无代码", "list_date": "2020-01-01"},
    ]
    entries = entries_from_rows(rows, board="sz_main", source_url="fixture://",
                                fetched_at="2026-04-01T02:00:00+00:00")
    assert [e.code for e in entries] == ["000001", "000002", "000003", "000004"]
    assert [e.list_date for e in entries] == [
        date(1991, 4, 3), date(1991, 1, 29), None, date(2020, 1, 1),
    ]
    assert entries[3].name is None


# -- akshare adapter (offline, fabricated frames) ------------------------------

def _frame(columns: list[str], rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def test_adapter_board_split_and_scope_drop(monkeypatch):
    sh = _frame(["证券代码", "证券简称", "上市日期"], [
        ["600000", "浦发银行", "1999-11-10"],
        ["688001", "科创板不应出现", "2020-01-01"],
    ])
    sz = _frame(["A股代码", "A股简称", "A股上市日期"], [
        ["000001", "平安银行", "1991-04-03"],
        ["300750", "宁德时代", "2018-06-11"],
        ["002594", "比亚迪", "2011-06-30"],
        ["830001", "北交所不应出现", "2021-01-01"],
    ])

    class _FakeAK:
        @staticmethod
        def stock_info_sh_name_code(symbol):
            assert symbol == "主板A股"
            return sh

        @staticmethod
        def stock_info_sz_name_code(symbol):
            assert symbol == "A股列表"
            return sz

    monkeypatch.setattr(stock_list_ak, "_require_akshare", lambda: _FakeAK)
    entries = stock_list_ak.fetch_universe_entries_v1()
    boards = {(e.code, e.board) for e in entries}
    assert boards == {
        ("600000", "sh_main"), ("000001", "sz_main"),
        ("300750", "chinext"), ("002594", "sz_main"),
    }  # 688/830 dropped upstream, board labels exact


def test_adapter_schema_mismatch_fails_loudly(monkeypatch):
    sh = _frame(["unrelated"], [["x"]])

    class _FakeAK:
        @staticmethod
        def stock_info_sh_name_code(symbol):
            return sh

        @staticmethod
        def stock_info_sz_name_code(symbol):
            return _frame(["A股代码", "A股简称"], [["000001", "平安银行"]])

    monkeypatch.setattr(stock_list_ak, "_require_akshare", lambda: _FakeAK)
    with pytest.raises(RuntimeError, match="schema mismatch"):
        stock_list_ak.fetch_universe_entries_v1()


# -- scan script (fetch monkeypatched; no network) -----------------------------

def test_scan_writes_atomic_idempotent_pool(tmp_path, monkeypatch):
    entries = [_entry("000001"), _entry("600000", "ST 康美")]

    class _CalendarStub:
        coverage_start = "2025-01-01"
        coverage_end = "2026-12-31"

        def add_trading_days(self, d, n):
            return d + timedelta(days=max(0, n) * 7 // 5)  # rough weekday math

    # patch the scan module's own binding (from-import), not the source module
    monkeypatch.setattr(scan_mod, "fetch_universe_entries_v1", lambda: entries)
    monkeypatch.setattr(scan_mod, "TradingCalendar",
                        type("TC", (), {"load": staticmethod(lambda: _CalendarStub())}))
    out_dir = tmp_path / "candidates"
    target = scan(out_dir, now=NOW)
    assert target == out_dir / "stock_pool_2026-04-01.json"
    first = json.loads(target.read_text(encoding="utf-8"))
    assert first["scan_date"] == "2026-04-01"
    assert first["counts"]["candidates"] == 1
    assert first["counts"]["excluded_by_reason"] == {REASON_ST: 1}
    assert not list(out_dir.glob("*.tmp"))  # atomic replace leaves no residue

    # same-day rerun overwrites the same file (idempotent)
    target2 = scan(out_dir, now=NOW)
    assert target2 == target
    assert json.loads(target.read_text(encoding="utf-8")) == first


def test_atomic_write_json_no_tmp_residue_on_failure(tmp_path):
    bad = tmp_path / "pool.json"
    with pytest.raises(TypeError):
        atomic_write_json(bad, object())  # json.dump fails on arbitrary object
    assert not list(tmp_path.glob("*.tmp"))
    assert not bad.exists()
