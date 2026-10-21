"""TASK-20261014-03: AKShare Eastmoney ETF minute unit mapping and source-level
rejection semantics (ISSUE-20261014-02).

The adapter is exercised against a stub akshare module so these tests stay
offline; real sampling evidence lives in
docs/research/20261014-etf-minute-unit-verification.md.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from trademind.errors import DataUnavailable
from trademind.market.base import BEIJING
from trademind.market.adapters.live import AKShareAdapter

ETF_COLUMNS = ["时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]


def _etf_row(ts: str, o: str, h: str, l: str, c: str, raw_vol: str, amount: str) -> dict:
    return {"时间": ts, "开盘": o, "收盘": c, "最高": h, "最低": l,
            "成交量": raw_vol, "成交额": amount}


class _StubAK(types.ModuleType):
    """Stub akshare module: records calls, returns canned frames or raises."""

    def __init__(self):
        super().__init__("akshare")
        self.calls: list[dict] = []
        self.etf_frames: list[pd.DataFrame] = []
        self.stock_frames: list[pd.DataFrame] = []
        self.etf_error: Exception | None = None

    def fund_etf_hist_min_em(self, **kwargs):
        self.calls.append({"fn": "fund_etf_hist_min_em", **kwargs})
        if self.etf_error is not None:
            raise self.etf_error
        return self.etf_frames.pop(0) if self.etf_frames else pd.DataFrame(columns=ETF_COLUMNS)

    def stock_zh_a_hist_min_em(self, **kwargs):
        self.calls.append({"fn": "stock_zh_a_hist_min_em", **kwargs})
        return self.stock_frames.pop(0) if self.stock_frames else pd.DataFrame(columns=ETF_COLUMNS)


@pytest.fixture
def stub_ak(monkeypatch):
    stub = _StubAK()
    monkeypatch.setitem(sys.modules, "akshare", stub)
    return stub


def _adapter(stub) -> AKShareAdapter:
    return AKShareAdapter()


def _window(day: str = "2026-10-09"):
    start = datetime.fromisoformat(f"{day} 09:00:00").replace(tzinfo=BEIJING)
    end = datetime.fromisoformat(f"{day} 15:00:00").replace(tzinfo=BEIJING)
    return start, end


def test_official_511220_sample_confirms_lot_unit():
    """Official AKShare sample: volume=413, amount=420929 CNY, close=10.192.
    Only a 手->份额 (x100) reading makes 成交额/份额 land on the bar price."""
    implied_shares_price = Decimal("420929") / (Decimal("413") * Decimal("100"))
    assert abs(implied_shares_price - Decimal("10.192")) < Decimal("0.001")
    assert Decimal("10.192") * Decimal("0.98") <= implied_shares_price <= Decimal("10.192") * Decimal("1.02")
    implied_raw_shares = Decimal("420929") / Decimal("413")  # reading volume as shares
    assert implied_raw_shares > Decimal("10.192") * Decimal("50")  # ~1019.2, far outside any ±2% band


def test_etf_volume_converted_to_shares_exactly_once(stub_ak):
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row("2026-10-09 09:31:00", "1.500", "1.510", "1.490", "1.505", "413", "62135"),
        _etf_row("2026-10-09 09:32:00", "1.505", "1.520", "1.500", "1.515", "0", "0"),
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start, end = _window()
    bars = adapter.get_minute_bars("513050.SH", start, end, period="1m")
    assert [str(b.volume) for b in bars] == ["41300", "0"]  # 413手 -> 41300 shares, applied once
    assert [str(b.amount) for b in bars] == ["62135", "0"]  # CNY kept verbatim
    assert stub_ak.calls[0]["adjust"] == ""  # official ETF minute data is unadjusted


def test_etf_unit_change_rejected_not_mis_scaled(stub_ak):
    """If the source one day reports shares directly, blind x100 breaks the
    amount/volume relation and the source is rejected outright."""
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row("2026-10-09 09:31:00", "1.500", "1.510", "1.490", "1.505", "41300", "62135"),
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")


def test_etf_missing_amount_column_rejected(stub_ak):
    frame = pd.DataFrame([{"时间": "2026-10-09 09:31:00", "开盘": "1.5", "收盘": "1.5",
                           "最高": "1.51", "最低": "1.49", "成交量": "413"}])
    stub_ak.etf_frames.append(frame)
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")  # amount never fabricated


def test_etf_field_change_rejected(stub_ak):
    frame = pd.DataFrame([_etf_row("2026-10-09 09:31:00", "1.5", "1.51", "1.49", "1.5", "413", "62135")])
    stub_ak.etf_frames.append(frame.rename(columns={"成交量": "vol"}))
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")


def test_etf_unparsable_time_rejected(stub_ak):
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row("2026/10/09 09:31", "1.5", "1.51", "1.49", "1.5", "413", "62135"),
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")


def test_etf_invalid_ohlc_rejected(stub_ak):
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row("2026-10-09 09:31:00", "1.500", "1.480", "1.490", "1.505", "413", "62135"),
    ], columns=ETF_COLUMNS))  # high < low
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")


def test_etf_zero_volume_semantics(stub_ak):
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row("2026-10-09 09:31:00", "1.500", "1.500", "1.500", "1.500", "0", "0"),
        _etf_row("2026-10-09 09:32:00", "1.500", "1.510", "1.500", "1.505", "0", "621"),
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("513050.SH", start, end, period="1m")  # zero volume must carry zero amount


def test_etf_unclosed_and_future_rows_excluded(stub_ak):
    now = datetime.now(BEIJING)
    unclosed = now.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    future = (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    stub_ak.etf_frames.append(pd.DataFrame([
        _etf_row(unclosed, "1.500", "1.510", "1.490", "1.505", "413", "62135"),
        _etf_row(future, "1.500", "1.510", "1.490", "1.505", "413", "62135"),
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start = now - timedelta(days=2)
    end = now + timedelta(days=1)
    bars = adapter.get_minute_bars("513050.SH", start, end, period="1m")
    assert bars == []  # in-progress minute and future rows are never served


def test_etf_requires_verified_whitelist(stub_ak):
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable):
        adapter.get_minute_bars("511220.SH", start, end, period="1m")
    assert stub_ak.calls == []  # refused before any upstream request


def test_etf_connection_failure_records_category(stub_ak):
    stub_ak.etf_error = TimeoutError("upstream unreachable")
    adapter = _adapter(stub_ak)
    start, end = _window()
    with pytest.raises(DataUnavailable) as excinfo:
        adapter.get_minute_bars("513050.SH", start, end, period="1m")
    assert "TimeoutError" in str(excinfo.value)  # error category preserved for callers


def test_stock_minute_branch_unchanged(stub_ak):
    """Stock passthrough keeps raw fields and qfq — the stock path is out of scope."""
    stub_ak.stock_frames.append(pd.DataFrame([
        {"时间": "2026-10-09 09:31:00", "开盘": "10.0", "收盘": "10.1", "最高": "10.2",
         "最低": "9.9", "成交量": "413", "成交额": "420929"},
    ], columns=ETF_COLUMNS))
    adapter = _adapter(stub_ak)
    start, end = _window()
    bars = adapter.get_minute_bars("600519.SH", start, end, period="1m")
    assert len(bars) == 1
    assert str(bars[0].volume) == "413"  # raw passthrough, no lot conversion
    assert stub_ak.calls[0]["adjust"] == "qfq"  # legacy stock behavior kept
