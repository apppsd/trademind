"""TASK-20261027-04: stock minute adapter, tradability records, verify script."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trademind.market.adapters.stock_minute_sina import (
    check_share_unit,
    parse_stock_minutes,
    sina_symbol,
)
from trademind.market.profiles import a_share_profile
from trademind.market.stock_tradability import (
    REASON_AFFORDABLE,
    REASON_CASH_SHORT,
    REASON_NO_PRICE,
    REASON_SINGLE_CAP,
    StockCapParams,
    assess_tradability,
)
from scripts.verify_stock_tradability import stratified_sample, verify

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)  # pre-open, scan_date 10-27


# -- symbol mapping -------------------------------------------------------------

def test_sina_symbol_mapping():
    assert sina_symbol("000001", "sz_main") == "sz000001"
    assert sina_symbol("600000", "sh_main") == "sh600000"
    assert sina_symbol("300750", "chinext") == "sz300750"  # ChiNext is SZ-listed
    with pytest.raises(ValueError, match="unknown board"):
        sina_symbol("688001", "star")


# -- minute row parsing ---------------------------------------------------------

def _row(day, o, h, l, c, v, a=None):
    return {"day": day, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "amount": a if a is not None else float(Decimal(c) * Decimal(v))}


def test_parse_normal_bars():
    bars = parse_stock_minutes([
        _row("2026-10-27 09:31:00", "11.29", "11.30", "11.29", "11.29", 350800),
        _row("2026-10-27 09:32:00", "11.29", "11.31", "11.28", "11.30", 305300),
    ])
    assert len(bars) == 2
    assert bars[0].volume == Decimal("350800")  # shares, no ×100 applied
    assert bars[1].time > bars[0].time


def test_parse_rejects_out_of_order_and_bad_rows():
    good1 = _row("2026-10-27 09:31:00", "11.29", "11.30", "11.29", "11.29", 100)
    good2 = _row("2026-10-27 09:32:00", "11.29", "11.30", "11.29", "11.29", 100)
    with pytest.raises(ValueError, match="ascending"):
        parse_stock_minutes([good2, good1])
    with pytest.raises(ValueError, match="ascending"):  # duplicate timestamps
        parse_stock_minutes([good1, good1])
    with pytest.raises(ValueError, match="implausible"):
        parse_stock_minutes([_row("2026-10-27 09:31:00", "11.29", "11.20", "11.29", "11.29", 100)])
    with pytest.raises(ValueError, match="negative"):
        parse_stock_minutes([_row("2026-10-27 09:31:00", "11.29", "11.30", "11.29", "11.29", -5)])
    with pytest.raises(ValueError, match="malformed"):
        parse_stock_minutes([_row("2026-10-27 09:31:00", "oops", "11.30", "11.29", "11.29", 100)])


def test_share_unit_evidence_detects_lots_vs_shares():
    mid = "11.29"
    shares_rows = [_row("2026-10-27 09:31:00", mid, "11.30", "11.28", mid, 350800,
                        a=3960682.0)]  # amount = volume × price → shares
    lots_rows = [_row("2026-10-27 09:31:00", mid, "11.30", "11.28", mid, 3508,
                      a=3960682.0)]  # amount = 100 × volume × price → lots
    assert check_share_unit(parse_stock_minutes(shares_rows)) is True
    assert check_share_unit(parse_stock_minutes(lots_rows)) is False
    assert check_share_unit(parse_stock_minutes([
        _row("2026-10-27 09:31:00", mid, "11.30", "11.28", mid, 0, a=0),
    ])) is False  # no evidence at all → False, not vacuous True


# -- tradability records --------------------------------------------------------

def test_affordable_and_exact_fees():
    profile = a_share_profile(board="main", market="XSHE")
    rec = assess_tradability("000001", "sz_main", profile, Decimal("4.00"), "bar@2026-10-27 15:00:00")
    assert rec.affordable and rec.reason == REASON_AFFORDABLE
    assert rec.one_lot_notional == "400.00"
    assert rec.one_lot_buy_fee == "5.00"          # commission min 5 + tiny transfer
    assert rec.round_trip_fee == "10.20"          # + sell stamp 0.200 + transfer
    assert rec.round_trip_ratio == "0.0255"       # 2.55% round trip — exposed, not hidden
    assert rec.t_plus_days == 1 and rec.lot_size == 100


def test_single_cap_exceeded():
    profile = a_share_profile(board="main", market="XSHG")
    rec = assess_tradability("600000", "sh_main", profile, Decimal("4.60"), "bar@x")
    assert not rec.affordable and rec.reason == REASON_SINGLE_CAP  # 460 > 450


def test_cash_short_with_tight_caps():
    profile = a_share_profile(board="main", market="XSHE")
    tight = StockCapParams(
        initial_cash=Decimal("1000"), max_single_exposure=Decimal("450"),
        max_total_exposure=Decimal("500"), min_cash_reserve=Decimal("100"),
    )
    rec = assess_tradability("000002", "sz_main", profile, Decimal("4.00"), "bar@x", tight)
    assert rec.reason == REASON_CASH_SHORT  # 405 > 500−100


def test_no_price_record():
    profile = a_share_profile(board="main", market="XSHE")
    rec = assess_tradability("000003", "sz_main", profile, None, "no_bars_on_2026-10-27")
    assert not rec.affordable and rec.reason == REASON_NO_PRICE
    assert rec.last_close is None and rec.one_lot_notional is None


def test_chinext_profile_20_percent():
    profile = a_share_profile(board="chinext", market="XSHE")
    assert profile.price_limit_ratio == Decimal("0.20")
    rec = assess_tradability("300750", "chinext", profile, Decimal("4.00"), "bar@x")
    assert rec.price_limit_ratio == "0.20" and rec.affordable


# -- sampling -------------------------------------------------------------------

def _pool(board, codes):
    return [{"code": c, "board": board, "name": "x", "list_date": "2015-01-01",
             "source_url": "", "source_time": "", "fetched_at": ""} for c in codes]


def test_stratified_sample_deterministic_and_balanced():
    candidates = (_pool("chinext", [f"30000{i}" for i in range(10)])
                  + _pool("sh_main", [f"60000{i}" for i in range(20)])
                  + _pool("sz_main", [f"00000{i}" for i in range(5)]))
    a = stratified_sample(candidates, 3)  # per-board: 3 each → 9 total
    b = stratified_sample(candidates, 3)
    assert a == b
    boards = [c["board"] for c in a]
    assert boards.count("sh_main") == 3 and boards.count("sz_main") == 3


def test_sample_smaller_than_pool_takes_all():
    candidates = _pool("sz_main", ["000001", "000002"])
    assert len(stratified_sample(candidates, 9)) == 2


# -- verify script (fetch injected, no network) ---------------------------------

def _pool_file(tmp_path: Path) -> Path:
    payload = {
        "generated_at": "2026-10-27T01:00:00+00:00",
        "candidates": stratified_sample(
            _pool("sz_main", [f"0000{i:02d}" for i in range(6)])
            + _pool("sh_main", [f"6000{i:02d}" for i in range(6)])
            + _pool("chinext", [f"3000{i:02d}" for i in range(6)]), 6),
    }
    p = tmp_path / "stock_pool_2026-10-27.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_verify_writes_records_and_handles_fetch_errors(tmp_path):
    pool = _pool_file(tmp_path)
    calls = []

    def fetch(code, board):
        calls.append(code)
        if code.endswith("03"):
            raise ConnectionError("reset")  # per-candidate failure must not abort
        v = 100000
        mid = "4.00"
        return [_row(f"2026-10-27 09:3{code[-1]}:00", mid, "4.01", "3.99", mid, v,
                     a=float(Decimal(mid) * v))]

    target = verify(tmp_path, sample_size=6, now=NOW, pool_path=pool,
                    fetch_fn=fetch, sleep_fn=lambda s: None)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["sample_size"] == 6
    assert payload["affordable_count"] == 3  # three fetch_error records not affordable
    reasons = [r["reason"] for r in payload["records"]]
    assert reasons.count("affordable") == 3
    assert sum(1 for r in payload["records"]
               if r["reason"] == "no_price" and r["price_as_of"].startswith("fetch_error")) == 3
    assert payload["intraday_freshness"].startswith("NOT VERIFIED")
    assert len(calls) == 6
    assert not list(tmp_path.glob("*.tmp"))


def test_verify_requires_pool(tmp_path):
    with pytest.raises(RuntimeError, match="no stock_pool"):
        verify(tmp_path, sample_size=3, now=NOW)
