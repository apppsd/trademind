"""Order, Fill, reservation and account contracts (ARCH-001).

Idempotency: client_order_id and provider_fill_id are unique keys; duplicate
fills never double-apply to the ledger.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import DecimalStr, MsgBase, require_tz, utcnow, new_id
from .enums import OrderSide, OrderStatus, OrderType, RunMode, TradeAction


class Order(MsgBase):
    order_id: str
    client_order_id: str  # unique; retry with same id must not duplicate
    intent_id: str
    guard_id: str
    account_id: str
    instrument_id: str
    mode: RunMode
    side: OrderSide
    type: OrderType = OrderType.LIMIT
    quantity: DecimalStr
    limit_price: DecimalStr
    filled_quantity: DecimalStr = Decimal("0")
    average_fill_price: DecimalStr | None = None
    status: OrderStatus = OrderStatus.NEW
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _qty(self):
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        return self


class Fill(MsgBase):
    fill_id: str
    order_id: str
    provider_fill_id: str  # unique; dedupe on replay/redelivery
    quantity: DecimalStr
    price: DecimalStr
    fee: DecimalStr
    currency: str = "CNY"
    executed_at: datetime
    available_at: datetime  # when this system first saw the fill

    @field_validator("executed_at", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _qty(self):
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("fill quantity/price must be positive, fee non-negative")
        return self


class FundReservation(MsgBase):
    """Atomic cash reservation tied to one order; released on terminal state."""

    reservation_id: str
    order_id: str
    account_id: str
    account_version: int
    amount: DecimalStr
    currency: str = "CNY"
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    released_at: datetime | None = None

    @field_validator("created_at", "released_at")
    @classmethod
    def _aware(cls, v):
        if v is not None:
            require_tz(v)
        return v


class AccountEvent(MsgBase):
    """Append-only ledger event. seq is per-account monotonic."""

    seq: int
    account_id: str
    kind: Literal[
        "deposit",
        "withdraw",
        "fill_buy",
        "fill_sell",
        "fee",
        "reserve",
        "release",
        "corporate_action",
        "rollover",
    ]
    instrument_id: str | None = None
    quantity: DecimalStr | None = None
    amount: DecimalStr | None = None
    currency: str = "CNY"
    fill_id: str | None = None
    order_id: str | None = None
    note: str = ""
    effective_at: datetime

    @field_validator("effective_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


class PositionState(MsgBase):
    instrument_id: str
    quantity: DecimalStr
    available_quantity: DecimalStr  # T+1 settlement aware
    avg_cost: DecimalStr
    currency: str = "CNY"
    last_price: DecimalStr | None = None
    market_value: DecimalStr | None = None
    unrealized_pnl: DecimalStr | None = None

    @model_validator(mode="after")
    def _qty(self):
        if self.quantity < 0 or self.available_quantity < 0:
            raise ValueError("quantities must be non-negative")
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity cannot exceed quantity")
        return self


class AccountSnapshot(MsgBase):
    """Point-in-time account facts, single-writer, versioned."""

    account_id: str
    version: int
    as_of: datetime
    currency: str = "CNY"
    cash: DecimalStr
    reserved_cash: DecimalStr = Decimal("0")
    market_value: DecimalStr
    equity: DecimalStr
    positions: list[PositionState]

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    def position_weight(self, instrument_id: str) -> Decimal | None:
        for p in self.positions:
            if p.instrument_id == instrument_id and self.equity != 0:
                return (p.market_value or Decimal(0)) / self.equity
        return None
