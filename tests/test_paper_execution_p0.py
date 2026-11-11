"""TASK-20261108-002 P0 quick repairs regression tests (B04/B07/B06/B05).

B04: daily-bar callers must pass replay visibility (as_of) into the router's
revision merge — a correction not yet visible at `now` must not evict the
historically visible bar. B07: fill prices quantize onto the instrument's
tick lattice, not the limit price's string decimal places; a lattice round
that crosses an off-lattice limit refuses the fill instead of clamping back
onto the off-lattice limit (R1). B06: futures short-opening/short-closing
semantics are not implemented and are refused explicitly, leaving no ledger
residue (R1). B05: MultiAssetPaperBroker.execute_order is an offline
experiment component (default-refuse gate, replay protection, only NEW
orders executable — R1).

Real DatasetRouter with synthetic revision/minute adapters; no network,
no model calls, no real market data."""

from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.domain.base import new_id
from trademind.domain.enums import OrderSide, OrderStatus, RunMode
from trademind.domain.orders import Order
from trademind.errors import InsufficientFunds
from trademind.execution.multi_asset_paper import (
    MultiAssetLedger,
    MultiAssetPaperBroker,
    PositionLot,
)
from trademind.execution.paper import PaperBroker, PaperConfig
from trademind.market.base import BEIJING, Bar, MinuteBar, bar_close_time
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.pipeline import Pipeline

MAIN = "513050.SH"
FUT = "RB2610.SHFE"

EARLY = datetime(2026, 10, 12, 16, 0, tzinfo=BEIJING)   # rev1 visible, rev2 not
REV2_AT = datetime(2026, 10, 13, 16, 0, tzinfo=BEIJING)  # correction published
BETWEEN = datetime(2026, 10, 13, 12, 0, tzinfo=BEIJING)  # rev2 not visible yet


def daily_bar(instrument_id: str, d: date, close: str, revision: int = 1,
              available_at: datetime | None = None) -> Bar:
    t = bar_close_time(d)
    return Bar(instrument_id=instrument_id, trade_date=d,
               open=Decimal(close), high=Decimal(close), low=Decimal(close),
               close=Decimal(close), volume="1000", amount="100000",
               provider="rev", source_time=t, fetched_at=available_at or t,
               available_at=available_at or t, revision=revision)


def revision_rows() -> dict[str, list[Bar]]:
    """The 10-12 bar: rev1 (close 100) visible from EARLY, the vendor
    correction rev2 (close 110) only from REV2_AT."""
    return {MAIN: [daily_bar(MAIN, date(2026, 10, 9), "99"),
                   daily_bar(MAIN, date(2026, 10, 12), "100", revision=1,
                             available_at=EARLY),
                   daily_bar(MAIN, date(2026, 10, 12), "110", revision=2,
                             available_at=REV2_AT)]}


def revision_router(rows: dict[str, list[Bar]]) -> DatasetRouter:
    router = DatasetRouter(pref=["rev"])
    router._factories = {"rev": (lambda: _RevAdapter(rows))}
    return router


class _RevAdapter:
    """Synthetic daily source whose rows can grow mid-test (mirrors
    tests/test_daily_revision_visibility.py)."""

    name = "rev"

    def __init__(self, rows: dict[str, list[Bar]]):
        self.rows = rows

    def capabilities(self):
        return {"minute_bars": False, "daily_bars": True}

    def get_daily_bars(self, instrument_id, start, end):
        return [b for b in self.rows.get(instrument_id, [])
                if start <= b.trade_date <= end]

    def get_minute_bars(self, *a, **k):
        return []

    def get_news(self, *a, **k):
        return []

    def get_fundamentals(self, *a, **k):
        return []

    def get_calendar(self, start, end):
        return []


def weekday_calendar(start_d: date, end_d: date) -> TradingCalendar:
    days = [start_d + timedelta(days=i) for i in range((end_d - start_d).days + 1)]
    return TradingCalendar([d for d in days if d.weekday() < 5])


def make_order(instrument_id: str, side: OrderSide, qty: str, price: str,
               account_id: str = "paper-001", created_at: datetime | None = None,
               order_id: str | None = None) -> Order:
    now = created_at or datetime(2026, 10, 12, 16, 0, tzinfo=BEIJING)
    return Order(id=new_id("o"), trace_id=new_id("t"), created_at=now,
                 updated_at=now, order_id=order_id or new_id("o"),
                 client_order_id=new_id("cli"), intent_id=new_id("i"),
                 guard_id=new_id("g"), account_id=account_id,
                 instrument_id=instrument_id, mode=RunMode.PAPER, side=side,
                 quantity=qty, limit_price=price)


class StubMinuteRouter:
    """Serves one fixed minute bar; daily queries raise so the minute path
    is the only eligible source."""

    def __init__(self, bars: list[MinuteBar]):
        self.bars = bars

    def get_minute_bars(self, instrument_id, start, end, period="1m"):
        bars = [b for b in self.bars if b.instrument_id == instrument_id]
        return bars, SimpleNamespace(ok=True, limit_violations=[])

    def get_daily_bars(self, instrument_id, start, end, **k):
        raise AssertionError("daily path must not be used in minute tests")


def minute_bar(instrument_id: str, t: datetime, open_: str, high: str,
               low: str, close: str) -> MinuteBar:
    return MinuteBar(instrument_id=instrument_id, trade_date=t.date(),
                     bar_time=t, open=Decimal(open_), high=Decimal(high),
                     low=Decimal(low), close=Decimal(close), volume="1000",
                     amount="149600", provider="stub", source_time=t,
                     fetched_at=t, available_at=t)


# --- B04: replay visibility must reach the router's revision merge ---------

def test_daily_fill_keeps_bar_whose_revision_is_not_yet_visible():
    rows = revision_rows()
    broker = PaperBroker(revision_router(rows), weekday_calendar(date(2026, 9, 1), date(2026, 10, 16)),
                         None, PaperConfig(slippage_bps=0, price_tick="0.01"))
    order = make_order(MAIN, OrderSide.BUY, "100", "101",
                       created_at=datetime(2026, 10, 9, 16, 0, tzinfo=BEIJING))
    fill = broker.try_fill(order, BETWEEN)
    # Pre-fix the as_of-less merge let rev2 (close 110, invisible at BETWEEN)
    # win for 10-12 and the visibility filter then dropped the whole date —
    # no fill at all. The visible rev1 bar (close 100) must fill.
    assert fill is not None
    assert Decimal(str(fill.price)) == Decimal("100")


def test_reference_price_daily_uses_visible_revision():
    fake = SimpleNamespace(
        router=revision_router(revision_rows()),
        calendar=weekday_calendar(date(2026, 9, 1), date(2026, 10, 16)),
        cfg=SimpleNamespace(t0=SimpleNamespace(enabled=False),
                            risk=SimpleNamespace(quote_max_age_seconds=180)))
    # TASK-20261108-003 B01: reference_price is now a thin wrapper over
    # _reference_quote, which carries the whole daily path (incl. the as_of
    # forwarded into the revision merge) — assert through it directly.
    # R1 rework: _reference_quote delegates to _reference_quote_state, so the
    # unbound fake binds both. Assertion semantics unchanged.
    fake._reference_quote_state = Pipeline._reference_quote_state.__get__(fake)
    assert Pipeline._reference_quote(fake, MAIN, EARLY)[0] == Decimal("100")
    # The late viewpoint adopts the correction normally.
    assert Pipeline._reference_quote(fake, MAIN, REV2_AT + timedelta(minutes=30))[0] == Decimal("110")


# --- B07: fill prices on the tick lattice ----------------------------------

def test_minute_fill_precision_independent_of_limit_string():
    t = datetime(2026, 10, 13, 14, 59, tzinfo=BEIJING)
    router = StubMinuteRouter([minute_bar(MAIN, t, "1.496", "1.497", "1.495", "1.496")])
    broker = PaperBroker(router, weekday_calendar(date(2026, 10, 13), date(2026, 10, 16)),
                         None, PaperConfig(slippage_bps=0, fill_lag_bars=1,
                                           minute_only=True, price_tick="0.001"))
    fill_a = broker.try_fill(make_order(MAIN, OrderSide.BUY, "100", "1.500"),
                             datetime(2026, 10, 13, 15, 0, tzinfo=BEIJING))
    fill_b = broker.try_fill(make_order(MAIN, OrderSide.BUY, "100", "1.50"),
                             datetime(2026, 10, 13, 15, 0, tzinfo=BEIJING))
    # Pre-fix the limit string's decimal places chose the quantum ("1.500"
    # filled at 0.001 precision, "1.50" at 0.01); with a configured tick the
    # two equivalent representations must fill identically.
    assert fill_a is not None and fill_b is not None
    assert Decimal(str(fill_a.price)) == Decimal("1.496")
    assert Decimal(str(fill_a.price)) == Decimal(str(fill_b.price))


def test_multi_asset_fill_price_on_tick_lattice():
    broker = MultiAssetPaperBroker(slippage_bps=5)
    ledger = MultiAssetLedger(account_id="paper-hk", cash_hkd=Decimal("1000000"))
    order = make_order("00700.HK", OrderSide.BUY, "100", "400.30", "paper-hk")
    fill = broker.execute_order(order, Decimal("400.05"), ledger,
                                allow_offline_experiment=True)
    # 400.05 * 1.0005 = 400.250025; tick 0.20 lattice → 400.20. Pre-fix
    # quantize(0.20) kept 400.3 — not a multiple of the tick.
    assert fill is not None
    assert Decimal(str(fill.price)) == Decimal("400.20")
    assert ledger.positions["00700.HK"].quantity == Decimal("100")


def test_lattice_round_crossing_off_lattice_limit_rejects_fill():
    t = datetime(2026, 10, 13, 14, 59, tzinfo=BEIJING)
    # MAJOR-1 / MINOR-4: R1 clamped the crossed lattice round back onto the
    # off-lattice limit, asserting fills at 2.005 / 2.004 under tick 0.01 —
    # prices that are not tick multiples. The reworked semantics refuse the
    # attempt: the half-up round crosses the authorized limit, so no legal
    # on-lattice price exists for it.
    buy_router = StubMinuteRouter([minute_bar(MAIN, t, "2.005", "2.005", "2.005", "2.005")])
    broker = PaperBroker(buy_router, weekday_calendar(date(2026, 10, 13), date(2026, 10, 16)),
                         None, PaperConfig(slippage_bps=0, fill_lag_bars=1,
                                           minute_only=True, price_tick="0.01"))
    fill = broker.try_fill(make_order(MAIN, OrderSide.BUY, "100", "2.005"),
                           datetime(2026, 10, 13, 15, 0, tzinfo=BEIJING))
    assert fill is None  # 2.005 rounds to lattice 2.01 > authorized limit

    # SELL symmetric: raw at off-lattice limit 2.004 rounds down to 2.00 —
    # below the authorized floor, so the attempt is refused too.
    sell_router = StubMinuteRouter([minute_bar(MAIN, t, "2.004", "2.004", "2.004", "2.004")])
    broker = PaperBroker(sell_router, weekday_calendar(date(2026, 10, 13), date(2026, 10, 16)),
                         None, PaperConfig(slippage_bps=0, fill_lag_bars=1,
                                           minute_only=True, price_tick="0.01"))
    fill = broker.try_fill(make_order(MAIN, OrderSide.SELL, "100", "2.004"),
                           datetime(2026, 10, 13, 15, 0, tzinfo=BEIJING))
    assert fill is None  # 2.004 rounds to lattice 2.00 < authorized limit


def test_lattice_round_within_limit_fills_on_tick():
    # Limit + tick dual constraint holding together: off-lattice limit
    # 400.30 (tick 0.20), raw 400.12 rounds onto 400.20 — a tick multiple
    # not worse than the authorized limit — and fills there.
    t = datetime(2026, 10, 13, 14, 59, tzinfo=BEIJING)
    router = StubMinuteRouter([minute_bar(MAIN, t, "400.12", "400.12", "400.12", "400.12")])
    broker = PaperBroker(router, weekday_calendar(date(2026, 10, 13), date(2026, 10, 16)),
                         None, PaperConfig(slippage_bps=0, fill_lag_bars=1,
                                           minute_only=True, price_tick="0.20"))
    fill = broker.try_fill(make_order(MAIN, OrderSide.BUY, "100", "400.30"),
                           datetime(2026, 10, 13, 15, 0, tzinfo=BEIJING))
    assert fill is not None
    assert Decimal(str(fill.price)) == Decimal("400.20")
    assert Decimal(str(fill.price)) % Decimal("0.20") == 0
    assert Decimal(str(fill.price)) <= Decimal("400.30")


def test_multi_asset_off_lattice_limit_refused_when_round_crosses():
    # MAJOR-1 reproduction: tick 0.20, limit = current = 400.30. R1 clamped
    # the lattice round (400.40) back onto 400.30 — an off-lattice fill.
    # The reworked path refuses instead; nothing is booked or marked FILLED.
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-hk", cash_hkd=Decimal("1000000"))
    order = make_order("00700.HK", OrderSide.BUY, "100", "400.30", "paper-hk")
    fill = broker.execute_order(order, Decimal("400.30"), ledger,
                                allow_offline_experiment=True)
    assert fill is None  # 400.30 rounds to lattice 400.40 > authorized limit
    assert not ledger.positions
    assert order.status != OrderStatus.FILLED
    assert not ledger.filled_order_ids


# --- B06: futures short semantics are not implemented and are refused -------

def _futures_broker_and_ledger(cash: str = "10000"):
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-fut", cash_cny=Decimal(cash))
    return broker, ledger


def _ledger_snapshot(ledger: MultiAssetLedger) -> dict:
    """Complete observable ledger state, for before/after refusal checks."""
    return {
        "cash_cny": ledger.cash_cny,
        "cash_hkd": ledger.cash_hkd,
        "reserve_cny": ledger.reserve_cny,
        "reserve_hkd": ledger.reserve_hkd,
        "total_fees_cny": ledger.total_fees_cny,
        "total_fees_hkd": ledger.total_fees_hkd,
        "positions": {k: asdict(v) for k, v in ledger.positions.items()},
        "history": list(ledger.history),
        "filled_order_ids": set(ledger.filled_order_ids),
    }


def test_futures_short_open_beyond_long_rejected():
    broker, ledger = _futures_broker_and_ledger()
    fill_buy = broker.execute_order(make_order(FUT, OrderSide.BUY, "1", "3500.0", "paper-fut"),
                                    Decimal("3500.0"), ledger, allow_offline_experiment=True)
    assert fill_buy is not None
    settled_cash = ledger.cash_cny
    with pytest.raises(ValueError, match="short-opening semantics not implemented"):
        broker.execute_order(make_order(FUT, OrderSide.SELL, "2", "3600.0", "paper-fut"),
                             Decimal("3600.0"), ledger, allow_offline_experiment=True)
    lot = ledger.positions[FUT]
    assert lot.long_qty == Decimal("1") and lot.short_qty == Decimal("0")
    assert lot.occupied_margin == Decimal("3500.00")
    assert ledger.cash_cny == settled_cash  # the rejected fill left no trace
    assert len(ledger.history) == 1


def test_futures_pure_short_open_rejected():
    broker, ledger = _futures_broker_and_ledger()
    with pytest.raises(ValueError, match="short-opening semantics not implemented"):
        broker.execute_order(make_order(FUT, OrderSide.SELL, "1", "3500.0", "paper-fut"),
                             Decimal("3500.0"), ledger, allow_offline_experiment=True)
    assert ledger.cash_cny == Decimal("10000")
    assert not ledger.positions
    assert not ledger.history


def test_rejected_futures_short_open_leaves_ledger_identical():
    # MINOR-3: R1's leading positions.setdefault() created an empty position
    # entry before the rejection fired — a refused pure short open left a
    # phantom ledger key. The complete pre/post snapshot must be identical.
    broker, ledger = _futures_broker_and_ledger()
    before = _ledger_snapshot(ledger)
    with pytest.raises(ValueError, match="short-opening semantics not implemented"):
        broker.execute_order(make_order(FUT, OrderSide.SELL, "1", "3500.0", "paper-fut"),
                             Decimal("3500.0"), ledger, allow_offline_experiment=True)
    assert _ledger_snapshot(ledger) == before
    assert not ledger.positions


def test_rejected_cash_buy_leaves_no_position_entry():
    # Same refusal hygiene on the cash branch (R1): an InsufficientFunds
    # refusal must not grow an empty position key either.
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    before = _ledger_snapshot(ledger)
    order = make_order("110001.SH", OrderSide.BUY, "500", "120.0", "paper-cb")  # 60000 > 5000
    with pytest.raises(InsufficientFunds):
        broker.execute_order(order, Decimal("120.0"), ledger,
                             allow_offline_experiment=True)
    assert _ledger_snapshot(ledger) == before
    assert not ledger.positions


def test_futures_buy_with_open_short_rejected():
    broker, ledger = _futures_broker_and_ledger()
    ledger.positions[FUT] = PositionLot(instrument_id=FUT, kind="futures",
                                        currency="CNY", short_qty=Decimal("1"),
                                        short_avg_cost=Decimal("3500"),
                                        contract_multiplier=Decimal("10"))
    with pytest.raises(ValueError, match="short-closing semantics not implemented"):
        broker.execute_order(make_order(FUT, OrderSide.BUY, "1", "3500.0", "paper-fut"),
                             Decimal("3500.0"), ledger, allow_offline_experiment=True)
    assert ledger.positions[FUT].short_qty == Decimal("1")
    assert ledger.positions[FUT].long_qty == Decimal("0")


# --- B05: offline-experiment gate and replay protection ---------------------

def test_execute_order_gate_refuses_by_default():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    order = make_order("110001.SH", OrderSide.BUY, "10", "120.0", "paper-cb")
    with pytest.raises(RuntimeError, match="offline experiment"):
        broker.execute_order(order, Decimal("120.0"), ledger)
    assert ledger.cash_cny == Decimal("5000")
    assert order.status != OrderStatus.FILLED
    assert not ledger.filled_order_ids


@pytest.mark.parametrize("terminal", [OrderStatus.CANCELLED, OrderStatus.REJECTED])
def test_execute_order_refuses_non_executable_states(terminal):
    # MAJOR-2: R1 only refused FILLED — a CANCELLED or REJECTED order could
    # still be filled, charged and re-marked FILLED. Only NEW orders are
    # executable on this offline-experiment path.
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    order = make_order("110001.SH", OrderSide.BUY, "10", "120.0", "paper-cb")
    order.status = terminal
    with pytest.raises(ValueError, match=f"non-executable state {terminal.value}"):
        broker.execute_order(order, Decimal("120.0"), ledger,
                             allow_offline_experiment=True)
    assert order.status == terminal  # not re-marked FILLED
    assert ledger.cash_cny == Decimal("5000")
    assert not ledger.positions
    assert not ledger.history
    assert not ledger.filled_order_ids


def test_execute_order_replay_rejected():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    order_id = "o-cb-replay"
    order = make_order("110001.SH", OrderSide.BUY, "10", "120.0", "paper-cb",
                       order_id=order_id)
    fill = broker.execute_order(order, Decimal("120.0"), ledger,
                                allow_offline_experiment=True)
    assert fill is not None and order.status == OrderStatus.FILLED
    settled_cash = ledger.cash_cny
    # Same order object: terminal state + ledger record refuse the replay.
    with pytest.raises(ValueError, match="replay rejected"):
        broker.execute_order(order, Decimal("120.0"), ledger,
                             allow_offline_experiment=True)
    # A fresh order object reusing the filled order id: the ledger refuses.
    replay = make_order("110001.SH", OrderSide.BUY, "10", "120.0", "paper-cb",
                        order_id=order_id)
    with pytest.raises(ValueError, match="replay rejected"):
        broker.execute_order(replay, Decimal("120.0"), ledger,
                             allow_offline_experiment=True)
    assert ledger.cash_cny == settled_cash  # no double entry
    assert len(ledger.history) == 1
    assert ledger.history[0]["order_id"] == order_id
