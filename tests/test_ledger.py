"""TASK-17 ledger tests: fees, T+1, idempotency, reservation, recovery."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.domain.base import new_id
from trademind.domain.enums import OrderSide, RunMode
from trademind.domain.instruments import FeeSchedule
from trademind.domain.orders import Fill, Order
from trademind.errors import DuplicateKey, InsufficientFunds
from trademind.portfolio import Ledger, compute_fees
from trademind.storage import Database

T0 = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)  # 15:00 Beijing

FEES = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"), created_at=T0,
                   commission_rate="0.00025", commission_min="5",
                   stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")


@pytest.fixture()
def ledger(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), T0)
    yield led
    db.close()


def _buy_order(qty="100", price="100.00", oid=None):
    return Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0,
                 order_id=oid or new_id("o"), client_order_id=new_id("cli"),
                 intent_id=new_id("i"), guard_id=new_id("g"),
                 account_id="paper-001", instrument_id="600519.SH",
                 mode=RunMode.PAPER, side=OrderSide.BUY, quantity=qty,
                 limit_price=price, updated_at=T0)


def _fill(order, qty, price, pfid=None):
    return Fill(id=new_id("f"), trace_id=new_id("t"), created_at=T0,
                fill_id=new_id("f"), order_id=order.order_id,
                provider_fill_id=pfid or new_id("pf"), quantity=qty,
                price=price, fee="0", executed_at=T0, available_at=T0)


def test_fees_hand_computed():
    fee = compute_fees(OrderSide.BUY, Decimal("1000"), Decimal("10.00"), FEES)
    # commission 10000*0.00025=2.5 -> min 5 ; transfer 10000*0.00001=0.10
    assert fee == Decimal("5.10")
    fee_sell = compute_fees(OrderSide.SELL, Decimal("1000"), Decimal("10.00"), FEES)
    # commission 5 + stamp 5 + transfer 0.10
    assert fee_sell == Decimal("10.10")


def test_buy_updates_cash_position_and_avg_cost(ledger):
    o = _buy_order(qty="1000", price="10.00")
    fee = ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    snap = ledger.snapshot(T0, {"600519.SH": Decimal("11.00")})
    # cash = 1,000,000 - 10,000 - 5.10
    assert snap.cash == Decimal("989994.90")
    pos = snap.positions[0]
    assert pos.quantity == 1000
    assert pos.avg_cost == Decimal("10.0051")  # (10000+5.10)/1000
    assert pos.market_value == Decimal("11000")
    assert pos.unrealized_pnl == Decimal("994.90")
    assert snap.equity == snap.cash + snap.market_value


def test_t1_available_quantity(ledger):
    o = _buy_order(qty="1000", price="10.00")
    ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    snap = ledger.snapshot(T0)
    assert snap.positions[0].available_quantity == 0
    ledger.on_new_trading_day(date(2026, 9, 2))
    snap2 = ledger.snapshot(T0)
    assert snap2.positions[0].available_quantity == 1000
    # sell next day works
    o2 = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id=new_id("o"),
               client_order_id=new_id("cli"), intent_id=new_id("i"), guard_id=new_id("g"),
               account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
               side=OrderSide.SELL, quantity="400", limit_price="10.5",
               updated_at=T0)
    ledger.apply_fill(_fill(o2, "400", "10.50"), o2, T0)
    snap3 = ledger.snapshot(T0)
    assert snap3.positions[0].quantity == 600


def test_t1_rollover_unknown_calendar_coverage_stays_locked(ledger):
    # Outside verified calendar coverage the day is NOT treated as a trading
    # day: no rollover, shares stay locked until the calendar is updated
    # (ISSUE-20261013-07). Conservative, never a fabricated unlock.
    o = _buy_order(qty="1000", price="10.00")
    ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    ledger.on_new_trading_day(date(2027, 1, 4))  # beyond the fixture calendar
    snap = ledger.snapshot(T0)
    assert snap.positions[0].available_quantity == 0


def test_same_day_sell_rejected(ledger):
    o = _buy_order(qty="1000", price="10.00")
    ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    o2 = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id=new_id("o"),
               client_order_id=new_id("cli"), intent_id=new_id("i"), guard_id=new_id("g"),
               account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
               side=OrderSide.SELL, quantity="100", limit_price="10.5",
               updated_at=T0)
    with pytest.raises(InsufficientFunds):
        ledger.apply_fill(_fill(o2, "100", "10.50"), o2, T0)


def test_fill_redelivery_never_double_applies(ledger):
    from trademind.storage import Repository

    o = _buy_order(qty="1000", price="10.00")
    f = _fill(o, "1000", "10.00", pfid="PF-DUP")
    Repository(ledger.db).save_fill(f)  # OMS persists the fill before ledger apply
    ledger.apply_fill(f, o, T0)
    snap1 = ledger.snapshot(T0)
    dup_fill = f.model_copy(update={"fill_id": new_id("f")})
    # repository-level dedupe: same provider_fill_id is rejected before apply
    assert Repository(ledger.db).save_fill(dup_fill) is False
    snap2 = ledger.snapshot(T0)
    assert snap1.cash == snap2.cash and snap1.version == snap2.version


def test_reservation_atomic_and_released(ledger):
    res_id = ledger.reserve("order-A", Decimal("100000"), T0)
    snap = ledger.snapshot(T0)
    assert snap.reserved_cash == Decimal("100000")
    with pytest.raises(InsufficientFunds):
        ledger.reserve("order-B", Decimal("1000000"), T0)
    ledger.release("order-A", T0)
    assert ledger.snapshot(T0).reserved_cash == Decimal("0")


def test_recovery_rebuilds_identical_state(ledger):
    o = _buy_order(qty="1000", price="10.00")
    ledger.reserve(o.order_id, Decimal("12000"), T0)
    ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    ledger.release(o.order_id, T0)
    before = ledger.snapshot(T0, {"600519.SH": Decimal("10.5")})
    ledger.rebuild_from_events()
    after = ledger.snapshot(T0, {"600519.SH": Decimal("10.5")})
    assert before.cash == after.cash
    assert before.market_value == after.market_value
    assert before.equity == after.equity
    assert [p.quantity for p in before.positions] == [p.quantity for p in after.positions]


def test_missing_price_falls_back_to_cost(ledger):
    o = _buy_order(qty="1000", price="10.00")
    ledger.apply_fill(_fill(o, "1000", "10.00"), o, T0)
    snap = ledger.snapshot(T0)  # no prices provided
    assert snap.positions[0].market_value == Decimal("10005.10")  # cost basis incl. fee


# -- TASK-20261016-06: T+0 lots must not be unlocked twice (ISSUE-20261016-03) --

def _t0_etf_buy(ledger, qty="100", price="1.00", at=None, iid="513050.SH"):
    at = at or T0
    order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=at,
                  order_id=new_id("o"), client_order_id=new_id("cli"),
                  intent_id=new_id("i"), guard_id=new_id("g"),
                  account_id="paper-001", instrument_id=iid,
                  mode=RunMode.PAPER, side=OrderSide.BUY, quantity=qty,
                  limit_price=price, updated_at=at)
    fill = Fill(id=new_id("f"), trace_id=order.trace_id, created_at=at,
                fill_id=new_id("f"), order_id=order.order_id,
                provider_fill_id=new_id("pf"), quantity=Decimal(qty),
                price=Decimal(price), fee=Decimal("0"),
                executed_at=at, available_at=at)
    ledger.apply_fill(fill, order, at)
    return order, fill


def _lot_state(ledger, iid="513050.SH"):
    lot = ledger._positions[iid]
    return lot.quantity, lot.available, lot.bought_today


def test_t0_buy_unlocks_immediately_without_bought_today(ledger):
    _t0_etf_buy(ledger)
    qty, available, bought_today = _lot_state(ledger)
    assert (qty, available, bought_today) == (Decimal("100"), Decimal("100"), Decimal("0"))


def test_t0_open_balance_survives_rollover_without_double_unlock(ledger):
    _t0_etf_buy(ledger, at=datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
    # same-process rollover: previously raised ValidationError (available 200 > quantity 100)
    ledger.on_new_trading_day(date(2026, 10, 13))
    assert _lot_state(ledger) == (Decimal("100"), Decimal("100"), Decimal("0"))
    ledger.on_new_trading_day(date(2026, 10, 13))  # same-day repeated rollover: no-op
    assert _lot_state(ledger) == (Decimal("100"), Decimal("100"), Decimal("0"))
    ledger.on_new_trading_day(date(2026, 10, 14))  # consecutive rollover still stable
    assert _lot_state(ledger) == (Decimal("100"), Decimal("100"), Decimal("0"))


def test_t0_three_recovery_paths_agree(ledger):
    _t0_etf_buy(ledger, at=datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
    ledger.on_new_trading_day(date(2026, 10, 13))
    same_process = _lot_state(ledger)
    # snapshot reload path
    db2 = ledger.db
    reloaded = Ledger(db2, "paper-001", FEES)
    snapshot_path = _lot_state(reloaded)
    # event replay path (drop derived snapshot, replay events)
    ledger.repo.db.execute("DELETE FROM account_state WHERE account_id='paper-001'")
    replayed = Ledger(db2, "paper-001", FEES)
    replay_path = _lot_state(replayed)
    assert same_process == snapshot_path == replay_path == (Decimal("100"), Decimal("100"), Decimal("0"))
    for led in (reloaded, replayed):
        led.on_new_trading_day(date(2026, 10, 14))
        assert led._positions["513050.SH"].available == Decimal("100")
    assert _lot_state(replayed) == (Decimal("100"), Decimal("100"), Decimal("0"))


def test_t0_partial_sell_keeps_bounds_across_rollover(ledger):
    _t0_etf_buy(ledger, at=datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))
    sell_order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0,
                       order_id=new_id("o"), client_order_id=new_id("cli"),
                       intent_id=new_id("i"), guard_id=new_id("g"),
                       account_id="paper-001", instrument_id="513050.SH",
                       mode=RunMode.PAPER, side=OrderSide.SELL, quantity="40",
                       limit_price="1.00", updated_at=T0)
    sell_fill = Fill(id=new_id("f"), trace_id=sell_order.trace_id, created_at=T0,
                     fill_id=new_id("f"), order_id=sell_order.order_id,
                     provider_fill_id=new_id("pf"), quantity=Decimal("40"),
                     price=Decimal("1.00"), fee=Decimal("0"),
                     executed_at=T0, available_at=T0)
    ledger.apply_fill(sell_fill, sell_order, sell_fill.executed_at)
    assert _lot_state(ledger) == (Decimal("60"), Decimal("60"), Decimal("0"))
    ledger.on_new_trading_day(date(2026, 10, 13))
    assert _lot_state(ledger) == (Decimal("60"), Decimal("60"), Decimal("0"))


def test_t1_stock_rollover_semantics_unchanged(ledger):
    order = _buy_order()
    fill = Fill(id=new_id("f"), trace_id=order.trace_id, created_at=T0,
                fill_id=new_id("f"), order_id=order.order_id,
                provider_fill_id=new_id("pf"), quantity=Decimal("100"),
                price=Decimal("100.00"), fee=Decimal("0"),
                executed_at=T0, available_at=T0)
    ledger.apply_fill(fill, order, T0)
    assert ledger._positions["600519.SH"].available == Decimal("0")
    assert ledger._positions["600519.SH"].bought_today == Decimal("100")
    ledger.on_new_trading_day(date(2026, 9, 2))
    assert ledger._positions["600519.SH"].available == Decimal("100")
    assert ledger._positions["600519.SH"].bought_today == Decimal("0")
