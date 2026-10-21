from datetime import datetime, timezone
from decimal import Decimal
import pytest

from trademind.domain.enums import OrderSide, OrderStatus, RunMode
from trademind.domain.orders import Order
from trademind.errors import InsufficientFunds
from trademind.execution.multi_asset_paper import MultiAssetLedger, MultiAssetPaperBroker
from trademind.market.rules import get_rule

NOW = datetime.now(timezone.utc)


def make_order(instrument_id: str, side: OrderSide, qty: str, price: str, account_id: str = "paper-001") -> Order:
    return Order(
        id="test-order",
        trace_id="test-trace",
        created_at=NOW,
        updated_at=NOW,
        order_id=f"o-{instrument_id}-{side.value}",
        client_order_id=f"cli-{instrument_id}-{side.value}",
        intent_id="intent-1",
        guard_id="guard-1",
        account_id=account_id,
        instrument_id=instrument_id,
        mode=RunMode.PAPER,
        side=side,
        quantity=qty,
        limit_price=price,
    )


# --- 1. Convertible Bonds Execution (TASK-20261008-07) ---

def test_convertible_bond_t0_roundtrip_conservation():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    initial_cash = ledger.cash_cny

    # 1. Buy 20 sheets at 120.0 (20 is multiple of 10)
    order_buy = make_order("110001.SH", OrderSide.BUY, "20", "120.0", "paper-cb")
    fill_buy = broker.execute_order(order_buy, Decimal("120.0"), ledger)
    assert fill_buy is not None
    assert order_buy.status == OrderStatus.FILLED
    # Notional: 20 * 120 = 2400. Fee: max(2400*0.0001, 1.0) = 1.0
    assert ledger.positions["110001.SH"].quantity == Decimal("20")
    assert ledger.positions["110001.SH"].available == Decimal("20")  # T+0 available immediately
    assert ledger.cash_cny == Decimal("5000") - Decimal("2401.00")

    # 2. Same-day Partial Sell 10 sheets at 125.0
    order_sell_1 = make_order("110001.SH", OrderSide.SELL, "10", "125.0", "paper-cb")
    fill_sell_1 = broker.execute_order(order_sell_1, Decimal("125.0"), ledger)
    assert fill_sell_1 is not None
    assert ledger.positions["110001.SH"].quantity == Decimal("10")
    assert ledger.positions["110001.SH"].available == Decimal("10")

    # 3. Same-day Final Sell remaining 10 sheets at 130.0 (Clear position)
    order_sell_2 = make_order("110001.SH", OrderSide.SELL, "10", "130.0", "paper-cb")
    fill_sell_2 = broker.execute_order(order_sell_2, Decimal("130.0"), ledger)
    assert fill_sell_2 is not None
    assert "110001.SH" not in ledger.positions

    # Conservation: Final cash = initial + realized profit - total fees
    # Profit: (125 - 120)*10 + (130 - 120)*10 = 50 + 100 = 150.
    # Total fees: buy 1.0 + sell1 1.0 + sell2 1.0 = 3.0
    assert ledger.cash_cny == initial_cash + Decimal("150.0") - ledger.total_fees_cny
    assert ledger.total_fees_cny == Decimal("3.00")


def test_convertible_bond_lot_size_violation_rejected():
    broker = MultiAssetPaperBroker()
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    # 15 sheets is not multiple of lot size 10
    order_invalid = make_order("110001.SH", OrderSide.BUY, "15", "120.0", "paper-cb")
    with pytest.raises(ValueError, match="multiple of lot size 10"):
        broker.execute_order(order_invalid, Decimal("120.0"), ledger)


def test_convertible_bond_early_redemption_or_suspension_blocked():
    broker = MultiAssetPaperBroker()
    ledger = MultiAssetLedger(account_id="paper-cb", cash_cny=Decimal("5000"))
    # 128017.SZ is flagged is_called_early=True
    order = make_order("128017.SZ", OrderSide.BUY, "10", "150.0", "paper-cb")
    fill = broker.execute_order(order, Decimal("150.0"), ledger)
    assert fill is None  # Blocked from execution


# --- 2. LOF Execution (TASK-20261008-07) ---

def test_lof_t0_execution_and_conservation():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-lof", cash_cny=Decimal("2000"))
    initial_cash = ledger.cash_cny

    # 162411.SZ is QDII T+0 LOF
    order_buy = make_order("162411.SZ", OrderSide.BUY, "200", "0.80", "paper-lof")
    fill_buy = broker.execute_order(order_buy, Decimal("0.80"), ledger)
    assert fill_buy is not None
    assert ledger.positions["162411.SZ"].available == Decimal("200")  # T+0

    # Same-day full sell
    order_sell = make_order("162411.SZ", OrderSide.SELL, "200", "0.85", "paper-lof")
    fill_sell = broker.execute_order(order_sell, Decimal("0.85"), ledger)
    assert fill_sell is not None
    assert "162411.SZ" not in ledger.positions

    # Profit: (0.85 - 0.80) * 200 = 10.0
    expected_cash = initial_cash + Decimal("10.0") - ledger.total_fees_cny
    assert ledger.cash_cny == expected_cash


def test_lof_t1_domestic_same_day_sell_rejected():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-lof", cash_cny=Decimal("2000"))

    # 161725.SZ is domestic equity T+1 LOF
    order_buy = make_order("161725.SZ", OrderSide.BUY, "100", "1.20", "paper-lof")
    fill_buy = broker.execute_order(order_buy, Decimal("1.20"), ledger)
    assert fill_buy is not None
    assert ledger.positions["161725.SZ"].quantity == Decimal("100")
    assert ledger.positions["161725.SZ"].available == Decimal("0")  # T+1 not available today

    # Attempt same-day sell must raise InsufficientFunds
    order_sell = make_order("161725.SZ", OrderSide.SELL, "100", "1.25", "paper-lof")
    with pytest.raises(InsufficientFunds, match="exceeds available 0"):
        broker.execute_order(order_sell, Decimal("1.25"), ledger)


# --- 3. Hong Kong Stocks Execution (TASK-20261008-08) ---

def test_hk_stock_isolated_hkd_ledger_and_roundtrip():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    # Segregated balances: 10000 CNY, 50000 HKD
    ledger = MultiAssetLedger(account_id="paper-hk", cash_cny=Decimal("10000"), cash_hkd=Decimal("50000"))
    initial_cny = ledger.cash_cny
    initial_hkd = ledger.cash_hkd

    # Buy 100 shares 00700.HK at 400.0 HKD
    order_buy = make_order("00700.HK", OrderSide.BUY, "100", "400.0", "paper-hk")
    fill_buy = broker.execute_order(order_buy, Decimal("400.0"), ledger)
    assert fill_buy is not None
    # CNY must be completely untouched
    assert ledger.cash_cny == initial_cny
    assert ledger.total_fees_cny == Decimal("0")
    # HKD deducted
    assert ledger.cash_hkd < initial_hkd
    assert ledger.positions["00700.HK"].currency == "HKD"

    # Sell 100 shares at 410.0 HKD
    order_sell = make_order("00700.HK", OrderSide.SELL, "100", "410.0", "paper-hk")
    fill_sell = broker.execute_order(order_sell, Decimal("410.0"), ledger)
    assert fill_sell is not None
    assert "00700.HK" not in ledger.positions

    # CNY remains 10000
    assert ledger.cash_cny == initial_cny
    # HKD profit: (410 - 400)*100 = 1000 HKD
    assert ledger.cash_hkd == initial_hkd + Decimal("1000.0") - ledger.total_fees_hkd


def test_hk_stock_zero_hkd_cash_cannot_spend_cny():
    broker = MultiAssetPaperBroker()
    # 50000 CNY, 0 HKD
    ledger = MultiAssetLedger(account_id="paper-hk", cash_cny=Decimal("50000"), cash_hkd=Decimal("0"))
    order_buy = make_order("00700.HK", OrderSide.BUY, "100", "400.0", "paper-hk")
    with pytest.raises(InsufficientFunds, match="exceeds available 0 HKD"):
        broker.execute_order(order_buy, Decimal("400.0"), ledger)


# --- 4. Futures Margin Execution (TASK-20261008-08) ---

def test_futures_margin_open_close_and_pnl_conservation():
    broker = MultiAssetPaperBroker(slippage_bps=0)
    ledger = MultiAssetLedger(account_id="paper-fut", cash_cny=Decimal("10000"))
    initial_cash = ledger.cash_cny

    # Buy 1 lot RB2610.SHFE at 3500.0
    # Multiplier: 10, Margin rate: 0.10. Notional = 35000. Margin = 3500.0
    order_buy = make_order("RB2610.SHFE", OrderSide.BUY, "1", "3500.0", "paper-fut")
    fill_buy = broker.execute_order(order_buy, Decimal("3500.0"), ledger)
    assert fill_buy is not None

    lot = ledger.positions["RB2610.SHFE"]
    assert lot.long_qty == Decimal("1")
    assert lot.occupied_margin == Decimal("3500.00")
    # Fee: min 5.0
    assert ledger.cash_cny == initial_cash - Decimal("3500.00") - Decimal("5.00")

    # Sell 1 lot to close at 3600.0 (Profit: (3600 - 3500) * 1 * 10 = +1000.0)
    order_sell = make_order("RB2610.SHFE", OrderSide.SELL, "1", "3600.0", "paper-fut")
    fill_sell = broker.execute_order(order_sell, Decimal("3600.0"), ledger)
    assert fill_sell is not None

    # Position closed
    assert lot.long_qty == Decimal("0")
    assert lot.occupied_margin == Decimal("0")

    # Final cash = initial + 1000 profit - total fees (5 + 5 = 10)
    assert ledger.cash_cny == initial_cash + Decimal("1000.00") - ledger.total_fees_cny
    assert ledger.total_fees_cny == Decimal("10.00")
