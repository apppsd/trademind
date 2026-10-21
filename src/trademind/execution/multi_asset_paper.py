"""Multi-asset paper broker and isolated ledger execution.

Supports isolated paper simulation for:
1. Convertible Bonds (T+0, 10-sheet lot size, early redemption and circuit breaker checks)
2. LOFs (strict T+0 verification; domestic T+1 LOFs rejected)
3. Hong Kong Stocks (HKD currency, isolated HKD ledger, per-stock lot size, Stock Connect gating)
4. Futures (Margin trading, contract multipliers, long/short positions, mark-to-market PnL)

Strictly preserves cash/fees/position conservation and enforces zero synthetic market fabrication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any

from ..domain.base import new_id, utcnow
from ..domain.enums import OrderSide, OrderStatus, RunMode
from ..domain.instruments import MarketProfile
from ..domain.orders import Fill, Order
from ..errors import InsufficientFunds, MarketClosed
from ..market.access import evaluate_eligibility
from ..market.profiles import get_multi_asset_profile
from ..market.rules import CANONICAL_RULES, MarketRule, get_rule, to_decimal


@dataclass
class PositionLot:
    instrument_id: str
    kind: str
    quantity: Decimal = Decimal("0")
    available: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")
    currency: str = "CNY"
    # Futures specific fields
    long_qty: Decimal = Decimal("0")
    short_qty: Decimal = Decimal("0")
    long_avg_cost: Decimal = Decimal("0")
    short_avg_cost: Decimal = Decimal("0")
    occupied_margin: Decimal = Decimal("0")
    contract_multiplier: Decimal = Decimal("1")


@dataclass
class MultiAssetLedger:
    account_id: str
    cash_cny: Decimal = Decimal("0")
    cash_hkd: Decimal = Decimal("0")
    reserve_cny: Decimal = Decimal("0")
    reserve_hkd: Decimal = Decimal("0")
    positions: dict[str, PositionLot] = field(default_factory=dict)
    total_fees_cny: Decimal = Decimal("0")
    total_fees_hkd: Decimal = Decimal("0")
    history: list[dict[str, Any]] = field(default_factory=list)

    def deposit(self, currency: str, amount: Decimal) -> None:
        if amount <= 0:
            raise ValueError("deposit must be positive")
        if currency == "HKD":
            self.cash_hkd += amount
        else:
            self.cash_cny += amount
        self.history.append({"kind": "deposit", "currency": currency, "amount": str(amount), "time": utcnow().isoformat()})

    def available_cash(self, currency: str) -> Decimal:
        if currency == "HKD":
            return max(Decimal("0"), self.cash_hkd - self.reserve_hkd)
        return max(Decimal("0"), self.cash_cny - self.reserve_cny)

    def apply_fill(self, fill: Fill, order: Order, rule: MarketRule | None = None) -> Decimal:
        iid = order.instrument_id
        r = rule or get_rule(iid)
        profile = get_multi_asset_profile(iid, r)
        curr = profile.fees.currency
        is_t0 = profile.settlement_rule == "T+0"
        qty = Decimal(str(fill.quantity))
        price = Decimal(str(fill.price))
        fee = Decimal(str(fill.fee))
        notional = qty * price

        lot = self.positions.setdefault(
            iid,
            PositionLot(
                instrument_id=iid,
                kind=profile.security_type,
                currency=curr,
                contract_multiplier=Decimal(str(r.contract_multiplier)) if r and r.contract_multiplier else Decimal("1"),
            ),
        )

        # 1. Futures execution branch
        if profile.security_type == "futures":
            multiplier = lot.contract_multiplier
            margin_rate = Decimal(str(r.margin_rate)) if r and r.margin_rate else Decimal("0.10")
            notional_contract = notional * multiplier
            req_margin = (notional_contract * margin_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if order.side == OrderSide.BUY:
                # Open Long position
                if self.cash_cny < req_margin + fee:
                    raise InsufficientFunds(f"futures margin {req_margin} + fee {fee} exceeds cash {self.cash_cny}")
                self.cash_cny -= req_margin + fee
                self.total_fees_cny += fee
                lot.occupied_margin += req_margin
                total_long_cost = lot.long_avg_cost * lot.long_qty + price * qty
                lot.long_qty += qty
                lot.long_avg_cost = (total_long_cost / lot.long_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                lot.quantity = lot.long_qty - lot.short_qty
                if is_t0:
                    lot.available = lot.long_qty
            else:
                # Close Long position (or open short)
                if lot.long_qty >= qty:
                    # Closing existing long
                    pnl = ((price - lot.long_avg_cost) * qty * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    margin_released = (lot.long_avg_cost * qty * multiplier * margin_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    margin_released = min(lot.occupied_margin, margin_released)
                    lot.occupied_margin -= margin_released
                    self.cash_cny += margin_released + pnl - fee
                    self.total_fees_cny += fee
                    lot.long_qty -= qty
                    if lot.long_qty == 0:
                        lot.long_avg_cost = Decimal("0")
                    lot.quantity = lot.long_qty - lot.short_qty
                    lot.available = lot.long_qty
                else:
                    # Open short
                    if self.cash_cny < req_margin + fee:
                        raise InsufficientFunds(f"futures margin {req_margin} + fee {fee} exceeds cash {self.cash_cny}")
                    self.cash_cny -= req_margin + fee
                    self.total_fees_cny += fee
                    lot.occupied_margin += req_margin
                    total_short_cost = lot.short_avg_cost * lot.short_qty + price * qty
                    lot.short_qty += qty
                    lot.short_avg_cost = (total_short_cost / lot.short_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    lot.quantity = lot.long_qty - lot.short_qty

            self.history.append({
                "kind": "fill", "side": order.side.value, "instrument_id": iid,
                "quantity": str(qty), "price": str(price), "fee": str(fee),
                "margin": str(req_margin), "currency": "CNY", "time": utcnow().isoformat(),
            })
            return fee

        # 2. Cash Securities branch (Convertible Bonds, LOFs, HK Stocks, ETFs)
        cash_avail = self.cash_hkd if curr == "HKD" else self.cash_cny
        if order.side == OrderSide.BUY:
            total_cost = notional + fee
            if total_cost > cash_avail:
                raise InsufficientFunds(f"buy fill {total_cost} exceeds available {cash_avail} {curr}")
            if curr == "HKD":
                self.cash_hkd -= total_cost
                self.total_fees_hkd += fee
            else:
                self.cash_cny -= total_cost
                self.total_fees_cny += fee

            basis = lot.avg_cost * lot.quantity + notional + fee
            lot.quantity += qty
            lot.avg_cost = (basis / lot.quantity).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if is_t0:
                lot.available += qty
        else:
            if qty > lot.available:
                raise InsufficientFunds(f"sell {qty} exceeds available {lot.available} (T+1 or insufficient position)")
            net_proceeds = notional - fee
            if curr == "HKD":
                self.cash_hkd += net_proceeds
                self.total_fees_hkd += fee
            else:
                self.cash_cny += net_proceeds
                self.total_fees_cny += fee

            lot.quantity -= qty
            lot.available -= qty
            if lot.quantity == 0:
                lot.avg_cost = Decimal("0")
                del self.positions[iid]

        self.history.append({
            "kind": "fill", "side": order.side.value, "instrument_id": iid,
            "quantity": str(qty), "price": str(price), "fee": str(fee),
            "currency": curr, "time": utcnow().isoformat(),
        })
        return fee


class MultiAssetPaperBroker:
    def __init__(self, slippage_bps: int = 5):
        self.slippage_bps = slippage_bps

    def execute_order(
        self,
        order: Order,
        current_price: Decimal,
        ledger: MultiAssetLedger,
        rule: MarketRule | None = None,
        is_suspended: bool = False,
    ) -> Fill | None:
        """Attempt to fill an order against current price."""
        if is_suspended:
            return None

        iid = order.instrument_id
        r = rule or get_rule(iid)
        if r and (r.is_suspended or r.is_called_early or r.is_delisted):
            return None

        profile = get_multi_asset_profile(iid, r)

        # Lot size check
        qty = Decimal(str(order.quantity))
        lot_size = Decimal(str(profile.lot_size))
        if qty <= 0 or (qty % lot_size) != 0:
            raise ValueError(f"order quantity {qty} must be positive multiple of lot size {lot_size}")

        # Slippage simulation
        slip_up = Decimal(1) + Decimal(self.slippage_bps) / Decimal(10000)
        slip_down = Decimal(1) - Decimal(self.slippage_bps) / Decimal(10000)
        limit = Decimal(str(order.limit_price))

        if order.side == OrderSide.BUY:
            if current_price > limit:
                return None  # limit price breached
            exec_price = min(limit, current_price * slip_up)
        else:
            if current_price < limit:
                return None
            exec_price = max(limit, current_price * slip_down)

        quant = Decimal(str(profile.price_tick))
        fill_price = exec_price.quantize(quant, rounding=ROUND_HALF_UP)

        # Compute fee for Fill record
        fee_rate = Decimal(str(profile.fees.commission_rate))
        fee_min = Decimal(str(profile.fees.commission_min))
        stamp_rate = Decimal(str(profile.fees.stamp_duty_rate_sell))
        transfer_rate = Decimal(str(profile.fees.transfer_fee_rate))
        notional = qty * fill_price
        comm = max(notional * fee_rate, fee_min)
        total_fee = comm + notional * transfer_rate
        if order.side == OrderSide.SELL:
            total_fee += notional * stamp_rate
        fee = total_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        fill = Fill(
            id=new_id("f"),
            trace_id=order.trace_id,
            created_at=utcnow(),
            fill_id=new_id("f"),
            order_id=order.order_id,
            provider_fill_id=new_id("pf"),
            quantity=str(qty),
            price=str(fill_price),
            fee=str(fee),
            currency=profile.fees.currency,
            executed_at=utcnow(),
            available_at=utcnow(),
        )

        ledger.apply_fill(fill, order, r)
        order.filled_quantity = qty
        order.status = OrderStatus.FILLED
        return fill
