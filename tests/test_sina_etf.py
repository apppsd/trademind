from datetime import datetime
from decimal import Decimal

import pytest
import requests

from trademind.errors import DataUnavailable
from trademind.market.adapters.sina_etf import SinaETFAdapter
from trademind.market.base import BEIJING
from trademind.market.router import DatasetRouter


START = datetime(2026, 10, 9, 9, 30, tzinfo=BEIJING)
END = datetime(2026, 10, 9, 15, 0, tzinfo=BEIJING)


def row(**changes):
    return dict(day="2026-10-09 09:31:00", open="1", high="1.01",
                low="0.99", close="1", volume="1000", amount="1000", **changes)


def source(monkeypatch, rows):
    adapter = SinaETFAdapter()
    class Response:
        def raise_for_status(self): pass
        def json(self): return rows
    monkeypatch.setattr(adapter.session, "get", lambda *a, **kw: Response())
    return adapter


def test_route_preserves_real_amount_and_fetch_availability(monkeypatch):
    adapter = source(monkeypatch, [row()])
    router = DatasetRouter(pref=["sina_etf"])
    router._instances["sina_etf"] = adapter
    bars, report = router.get_minute_bars("513050.SH", START, END)
    assert len(bars) == 1
    assert bars[0].volume == Decimal("1000")
    assert bars[0].amount == Decimal("1000")
    assert bars[0].available_at == bars[0].fetched_at > bars[0].bar_time
    assert bars[0].bar_time.tzinfo == BEIJING
    assert bars[0].provider == "sina_etf"
    assert router.last_provider_by_dataset["minute_bars_1m"] == "sina_etf"


@pytest.mark.parametrize("instrument", ["600519.SH", "510999.SH", "110072.SH"])
def test_non_etf_and_unknown_membership_never_request(monkeypatch, instrument):
    a = SinaETFAdapter()
    monkeypatch.setattr(a.session, "get", lambda *a, **kw: pytest.fail("must not request"))
    with pytest.raises(DataUnavailable): a.get_minute_bars(instrument, START, END)


@pytest.mark.parametrize("change", [{"amount": "10"}, {"volume": "NaN"},
    {"close": "2"}, {"volume": "0"}, {"low": "-1"}])
def test_malformed_numbers_and_unit_changes_fail_closed(monkeypatch, change):
    r = row(); r.update(change)
    with pytest.raises(DataUnavailable): source(monkeypatch, [r]).get_minute_bars("513050.SH", START, END)


def test_missing_amount_and_error_envelope_rejected(monkeypatch):
    r = row(); del r["amount"]
    for body in [[r], {"error": "unavailable"}]:
        with pytest.raises(DataUnavailable): source(monkeypatch, body).get_minute_bars("513050.SH", START, END)


def test_current_minute_is_not_exposed(monkeypatch):
    import trademind.market.adapters.sina_etf as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 10, 9, 9, 31, 30, tzinfo=BEIJING).astimezone(tz)
    monkeypatch.setattr(module, "datetime", Clock)
    assert source(monkeypatch, [row()]).get_minute_bars("513050.SH", START, END) == []


def test_transport_errors_are_categorical(monkeypatch):
    a = SinaETFAdapter()
    def fail(*a, **kw): raise requests.ConnectionError("sensitive upstream text")
    monkeypatch.setattr(a.session, "get", fail)
    with pytest.raises(DataUnavailable) as error: a.get_minute_bars("513050.SH", START, END)
    assert "sensitive" not in str(error.value)
