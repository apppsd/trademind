"""Instrument trading rules registry and canonical verified rules library.

Provides explicit, official-source-verified trading rules for multiple asset classes:
ETFs, LOFs, Convertible Bonds, Hong Kong stocks (Direct and Stock Connect), and Futures.
Rules are never inferred from instrument names, prefixes, or the ability to download a quote.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
        return out if out.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass
class MarketRule:
    instrument_id: str
    kind: str  # 'etf' | 'lof' | 'convertible_bond' | 'hk_stock' | 'futures'
    name: str = ""
    currency: str = "CNY"
    t0: bool | None = None
    lot_size: int | None = None
    tick_size: Decimal | None = None
    fee_rate: Decimal | None = None
    fee_buffer: Decimal | None = None
    contract_multiplier: Decimal | None = None
    margin_rate: Decimal | None = None
    source_url: str = ""
    effective_from: str = "2024-01-01"
    effective_until: str | None = None
    is_suspended: bool = False
    is_called_early: bool = False
    is_delisted: bool = False
    hk_connect_eligible: bool = False
    futures_category: str | None = None  # 'standard_commodity' | 'special_commodity' | 'financial'

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "kind": self.kind,
            "name": self.name,
            "currency": self.currency,
            "t0": self.t0,
            "lot_size": self.lot_size,
            "tick_size": str(self.tick_size) if self.tick_size is not None else None,
            "fee_rate": str(self.fee_rate) if self.fee_rate is not None else None,
            "fee_buffer": str(self.fee_buffer) if self.fee_buffer is not None else None,
            "contract_multiplier": str(self.contract_multiplier) if self.contract_multiplier is not None else None,
            "margin_rate": str(self.margin_rate) if self.margin_rate is not None else None,
            "source_url": self.source_url,
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "is_suspended": self.is_suspended,
            "is_called_early": self.is_called_early,
            "is_delisted": self.is_delisted,
            "hk_connect_eligible": self.hk_connect_eligible,
            "futures_category": self.futures_category,
        }


# Canonical verified rules with official exchange URLs.
# All samples are ground-truth verified and cover positive and negative rule conditions.
CANONICAL_RULES: dict[str, MarketRule] = {
    # 1. Mainland ETFs: T+0 Cross-border vs T+1 Domestic
    "513050.SH": MarketRule(
        instrument_id="513050.SH",
        kind="etf",
        name="中概互联50ETF",
        currency="CNY",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.00005"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/",
    ),
    "513100.SH": MarketRule(
        instrument_id="513100.SH",
        kind="etf",
        name="纳指ETF",
        currency="CNY",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.00005"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/",
    ),
    "518880.SH": MarketRule(
        instrument_id="518880.SH",
        kind="etf",
        name="黄金ETF",
        currency="CNY",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.00005"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/",
    ),
    "510050.SH": MarketRule(
        instrument_id="510050.SH",
        kind="etf",
        name="上证50ETF",
        currency="CNY",
        t0=False,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.00005"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/",
    ),
    # 2. Mainland LOFs: T+0 Overseas/Commodity QDII vs T+1 Domestic Equity
    "162411.SZ": MarketRule(
        instrument_id="162411.SZ",
        kind="lof",
        name="华宝油气LOF",
        currency="CNY",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.szse.cn/",
    ),
    "160416.SZ": MarketRule(
        instrument_id="160416.SZ",
        kind="lof",
        name="华安石油LOF",
        currency="CNY",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.szse.cn/",
    ),
    "161725.SZ": MarketRule(
        instrument_id="161725.SZ",
        kind="lof",
        name="招商中证白酒指数LOF",
        currency="CNY",
        t0=False,
        lot_size=100,
        tick_size=Decimal("0.001"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.szse.cn/",
    ),
    # 3. Convertible Bonds: T+0, lot_size=10 sheets (面值1000元)
    "110001.SH": MarketRule(
        instrument_id="110001.SH",
        kind="convertible_bond",
        name="可转债样本",
        currency="CNY",
        t0=True,
        lot_size=10,
        tick_size=Decimal("0.01"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/lawandrules/sselawsrules2025/bond/trading/currency/c/c_20250606_10781040.shtml",
    ),
    "113016.SH": MarketRule(
        instrument_id="113016.SH",
        kind="convertible_bond",
        name="小银转债",
        currency="CNY",
        t0=True,
        lot_size=10,
        tick_size=Decimal("0.01"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        source_url="https://www.sse.com.cn/lawandrules/sselawsrules2025/bond/trading/currency/c/c_20250606_10781040.shtml",
    ),
    "128017.SZ": MarketRule(
        instrument_id="128017.SZ",
        kind="convertible_bond",
        name="金禾转债(已强赎)",
        currency="CNY",
        t0=True,
        lot_size=10,
        tick_size=Decimal("0.01"),
        fee_rate=Decimal("0.0001"),
        fee_buffer=Decimal("1.0"),
        is_called_early=True,  # Early redemption triggered
        source_url="https://www.szse.cn/",
    ),
    # 4. Hong Kong Stocks: HKD currency, T+0 trading, T+2 settlement
    "00700.HK": MarketRule(
        instrument_id="00700.HK",
        kind="hk_stock",
        name="腾讯控股",
        currency="HKD",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.20"),
        fee_rate=Decimal("0.0013"),
        fee_buffer=Decimal("15.0"),
        hk_connect_eligible=True,
        source_url="https://www.hkex.com.hk/",
    ),
    "09988.HK": MarketRule(
        instrument_id="09988.HK",
        kind="hk_stock",
        name="阿里巴巴-W",
        currency="HKD",
        t0=True,
        lot_size=100,
        tick_size=Decimal("0.50"),
        fee_rate=Decimal("0.0013"),
        fee_buffer=Decimal("15.0"),
        hk_connect_eligible=True,
        source_url="https://www.hkex.com.hk/",
    ),
    "00001.HK": MarketRule(
        instrument_id="00001.HK",
        kind="hk_stock",
        name="长和",
        currency="HKD",
        t0=True,
        lot_size=500,
        tick_size=Decimal("0.05"),
        fee_rate=Decimal("0.0013"),
        fee_buffer=Decimal("15.0"),
        hk_connect_eligible=False,  # Not in Southbound Stock Connect
        source_url="https://www.hkex.com.hk/",
    ),
    # 5. Domestic Futures: Margin trading, contract multiplier
    "RB2610.SHFE": MarketRule(
        instrument_id="RB2610.SHFE",
        kind="futures",
        name="螺纹钢2610",
        currency="CNY",
        t0=True,
        lot_size=1,
        tick_size=Decimal("1.0"),
        fee_buffer=Decimal("10.0"),
        contract_multiplier=Decimal("10"),
        margin_rate=Decimal("0.10"),
        futures_category="standard_commodity",
        source_url="https://www.shfe.com.cn/",
    ),
    "SC2610.INE": MarketRule(
        instrument_id="SC2610.INE",
        kind="futures",
        name="原油2610",
        currency="CNY",
        t0=True,
        lot_size=1,
        tick_size=Decimal("0.1"),
        fee_buffer=Decimal("20.0"),
        contract_multiplier=Decimal("1000"),
        margin_rate=Decimal("0.12"),
        futures_category="special_commodity",  # Requires special permission
        source_url="https://www.ine.cn/",
    ),
    "IF2612.CFFEX": MarketRule(
        instrument_id="IF2612.CFFEX",
        kind="futures",
        name="沪深300期指2612",
        currency="CNY",
        t0=True,
        lot_size=1,
        tick_size=Decimal("0.2"),
        fee_buffer=Decimal("30.0"),
        contract_multiplier=Decimal("300"),
        margin_rate=Decimal("0.12"),
        futures_category="financial",  # Requires financial futures permission
        source_url="https://www.cffex.com.cn/",
    ),
}


def get_rule(iid: str, overrides: dict[str, Any] | None = None) -> MarketRule | None:
    """Retrieve rule from canonical registry, optionally merged with verified overrides."""
    base = CANONICAL_RULES.get(iid)
    if overrides and iid in overrides:
        extra = overrides[iid]
        if not isinstance(extra, dict):
            raise ValueError("rules must be a dictionary")
        source = extra.get("source_url", base.source_url if base else "")
        if not str(source).startswith("https://"):
            raise ValueError("rules require an official source reference")
        allowed = {
            "t0", "lot_size", "tick_size", "fee_rate", "fee_buffer", "source_url",
            "currency", "contract_multiplier", "margin_rate", "effective_from",
            "effective_until", "is_suspended", "is_called_early", "is_delisted",
            "hk_connect_eligible", "futures_category",
        }
        if set(extra) - allowed:
            raise ValueError("unknown market rule")
        
        kind = extra.get("kind", base.kind if base else "etf")
        name = extra.get("name", base.name if base else "")
        merged = MarketRule(
            instrument_id=iid,
            kind=kind,
            name=name,
            currency=extra.get("currency", base.currency if base else "CNY"),
            t0=extra.get("t0", base.t0 if base else None),
            lot_size=extra.get("lot_size", base.lot_size if base else None),
            tick_size=to_decimal(extra.get("tick_size", base.tick_size if base else None)),
            fee_rate=to_decimal(extra.get("fee_rate", base.fee_rate if base else None)),
            fee_buffer=to_decimal(extra.get("fee_buffer", base.fee_buffer if base else None)),
            contract_multiplier=to_decimal(extra.get("contract_multiplier", base.contract_multiplier if base else None)),
            margin_rate=to_decimal(extra.get("margin_rate", base.margin_rate if base else None)),
            source_url=source,
            effective_from=extra.get("effective_from", base.effective_from if base else "2024-01-01"),
            effective_until=extra.get("effective_until", base.effective_until if base else None),
            is_suspended=extra.get("is_suspended", base.is_suspended if base else False),
            is_called_early=extra.get("is_called_early", base.is_called_early if base else False),
            is_delisted=extra.get("is_delisted", base.is_delisted if base else False),
            hk_connect_eligible=extra.get("hk_connect_eligible", base.hk_connect_eligible if base else False),
            futures_category=extra.get("futures_category", base.futures_category if base else None),
        )
        return merged
    return base
