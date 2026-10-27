"""Per-candidate stock tradability records (read-only, no orders).

TASK-20261027-04 / ARCH-028 decision-layer criterion "资金可达" evaluated
per stock against the proposed stock-account caps (1000/450/900/100,
user-authorized 2026-10-27 to relax the 200 CNY single-stock cap for the
new stock account only; frozen by Task27-05). Reuses the V1 A-share
profile factory (fees already model commission minimum, sell-side stamp
duty and transfer fee) and ``compute_fees`` — no new fee math.

The cost-ratio field exists to expose what small-cap paper trading
actually costs: with a 5 CNY commission minimum, a 450 CNY one-lot round
trip burns ≈2.7% in fees. That is a fact about the parameter set, not a
strategy result; the record makes it impossible to overlook.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..domain.enums import OrderSide
from ..domain.instruments import MarketProfile
from ..portfolio.ledger import compute_fees

LOT_SIZE = Decimal(100)  # SH/SZ main board and ChiNext: 100 shares per lot

REASON_AFFORDABLE = "affordable"
REASON_SINGLE_CAP = "single_cap_exceeded"
REASON_CASH_SHORT = "cash_short"
REASON_NO_PRICE = "no_price"


@dataclass(frozen=True)
class StockCapParams:
    """Proposed stock-account caps; NOT the ETF experiment's frozen caps."""

    initial_cash: Decimal
    max_single_exposure: Decimal
    max_total_exposure: Decimal
    min_cash_reserve: Decimal
    source: str = "ARCH-028 proposal (user-authorized relaxation 2026-10-27); frozen by Task27-05"

    @classmethod
    def proposal(cls) -> "StockCapParams":
        return cls(
            initial_cash=Decimal("1000"),
            max_single_exposure=Decimal("450"),
            max_total_exposure=Decimal("900"),
            min_cash_reserve=Decimal("100"),
        )


@dataclass(frozen=True)
class StockTradabilityRecord:
    """One candidate's rule/fee/affordability assessment."""

    code: str
    board: str
    last_close: str | None  # None when no verified price was available
    price_as_of: str        # bar time the close came from, or the miss reason
    lot_size: int
    t_plus_days: int
    one_lot_notional: str | None
    one_lot_buy_fee: str | None
    round_trip_fee: str | None
    round_trip_ratio: str | None  # (buy+sell fee)/notional, e.g. '0.0272'
    affordable: bool
    reason: str  # REASON_*
    price_limit_ratio: str
    source_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "board": self.board,
            "last_close": self.last_close, "price_as_of": self.price_as_of,
            "lot_size": self.lot_size, "t_plus_days": self.t_plus_days,
            "one_lot_notional": self.one_lot_notional,
            "one_lot_buy_fee": self.one_lot_buy_fee,
            "round_trip_fee": self.round_trip_fee,
            "round_trip_ratio": self.round_trip_ratio,
            "affordable": self.affordable, "reason": self.reason,
            "price_limit_ratio": self.price_limit_ratio,
            "source_ref": self.source_ref,
        }


def assess_tradability(
    code: str,
    board: str,
    profile: MarketProfile,
    last_close: Decimal | None,
    price_as_of: str,
    caps: StockCapParams | None = None,
) -> StockTradabilityRecord:
    """Evaluate one candidate against caps and its board profile.

    ``caps`` defaults to the ARCH-028 proposal. Affordability mirrors the
    fast pipeline's entry check: one lot's notional must fit the single
    cap, and one lot plus buy fees must fit the spendable envelope
    (total cap minus the cash reserve) — position-free worst case.
    """
    caps = caps or StockCapParams.proposal()
    lot = LOT_SIZE
    lot_size_int = int(lot)
    t_plus = 1 if profile.settlement_rule == "T+1" else 0
    if last_close is None or last_close <= 0:
        return StockTradabilityRecord(
            code=code, board=board, last_close=None, price_as_of=price_as_of,
            lot_size=lot_size_int, t_plus_days=t_plus,
            one_lot_notional=None, one_lot_buy_fee=None,
            round_trip_fee=None, round_trip_ratio=None,
            affordable=False, reason=REASON_NO_PRICE,
            price_limit_ratio=str(profile.price_limit_ratio),
            source_ref=profile.source_ref,
        )

    notional = (lot * last_close).quantize(Decimal("0.01"))
    buy_fee = compute_fees(OrderSide.BUY, lot, last_close, profile.fees)
    sell_fee = compute_fees(OrderSide.SELL, lot, last_close, profile.fees)
    round_trip = (buy_fee + sell_fee).quantize(Decimal("0.01"))
    ratio = (round_trip / notional).quantize(Decimal("0.0001"))

    if notional > caps.max_single_exposure:
        reason = REASON_SINGLE_CAP
    elif notional + buy_fee > caps.max_total_exposure - caps.min_cash_reserve:
        reason = REASON_CASH_SHORT
    else:
        reason = REASON_AFFORDABLE

    return StockTradabilityRecord(
        code=code, board=board, last_close=str(last_close), price_as_of=price_as_of,
        lot_size=lot_size_int, t_plus_days=t_plus,
        one_lot_notional=str(notional), one_lot_buy_fee=str(buy_fee),
        round_trip_fee=str(round_trip), round_trip_ratio=str(ratio),
        affordable=(reason == REASON_AFFORDABLE), reason=reason,
        price_limit_ratio=str(profile.price_limit_ratio),
        source_ref=profile.source_ref,
    )


def source_effective_date(profile: MarketProfile) -> date:
    return profile.effective_from


__all__ = [
    "LOT_SIZE",
    "REASON_AFFORDABLE",
    "REASON_SINGLE_CAP",
    "REASON_CASH_SHORT",
    "REASON_NO_PRICE",
    "StockCapParams",
    "StockTradabilityRecord",
    "assess_tradability",
    "source_effective_date",
]
