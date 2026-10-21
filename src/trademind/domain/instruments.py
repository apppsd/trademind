"""Instrument and MarketProfile contracts (ARCH-001)."""

from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal

from pydantic import field_validator, model_validator

from .base import DecimalStr, MsgBase, require_tz, utcnow, new_id
from .enums import Market


class Instrument(MsgBase):
    """Security master record. Rules carry effective dates."""

    instrument_id: str
    market: Market
    symbol: str
    name: str | None = None
    currency: str = "CNY"
    calendar_id: str
    price_tick: DecimalStr
    lot_size: int
    security_type: str = "stock"  # first version: ordinary A-share stock only
    board: str = "main"  # main | chinext | star
    suspended: bool = False
    active_from: date | None = None
    delisted_on: date | None = None

    @field_validator("created_at", "active_from", "delisted_on", mode="before")
    @classmethod
    def _tz(cls, v):
        return v

    @model_validator(mode="after")
    def _ids(self):
        if not self.instrument_id or not self.symbol:
            raise ValueError("instrument_id and symbol are required")
        return self


class FeeSchedule(MsgBase):
    """A-share fee model, rates are decimal ratios, minimums in CNY."""

    commission_rate: DecimalStr  # e.g. 0.00025
    commission_min: DecimalStr
    stamp_duty_rate_sell: DecimalStr  # e.g. 0.0005
    transfer_fee_rate: DecimalStr  # e.g. 0.00001 (SSE/Shenzhen, both sides)
    currency: str = "CNY"


class MarketProfile(MsgBase):
    """Per exchange/board/type trading rules with an effective date.

    Numeric rules must come from an official source recorded in
    ``source_ref``; do not apply one rule set to all boards blindly.
    """

    profile_id: str
    market: Market
    security_type: str = "stock"
    board: str = "main"
    effective_from: date
    source_ref: str
    price_limit_ratio: DecimalStr  # e.g. 0.10 daily up/down limit
    lot_size: int = 100
    price_tick: DecimalStr = Decimal("0.01")
    settlement_rule: str = "T+1"
    short_selling_allowed: bool = False
    auction_open: str = "09:15-09:25"
    continuous: str = "09:30-11:30,13:00-14:57"
    closing_auction: str = "14:57-15:00"
    timezone: str = "Asia/Shanghai"
    fees: FeeSchedule

    @model_validator(mode="after")
    def _rules(self):
        if self.short_selling_allowed and self.security_type != "futures":
            raise ValueError("V1 does not support short selling")
        if self.lot_size <= 0 or self.price_tick <= 0:
            raise ValueError("lot_size and price_tick must be positive")
        if self.price_limit_ratio <= 0 or self.price_limit_ratio >= 1:
            raise ValueError("price_limit_ratio must be in (0,1)")
        return self

    @property
    def t_plus_days(self) -> int:
        return 0 if self.settlement_rule == "T+0" else 1


def make_profile_id(market: str, board: str, effective_from: date) -> str:
    return new_id(f"mp-{market}-{board}-{effective_from.isoformat()}")[:48]
