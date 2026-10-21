"""MarketProfile construction for A-share boards (TASK-04/08/15/16).

Rate values come from public exchange fee schedules; the source_ref records
the basis. Boards differ (STAR/ChiNext ±20%, main ±10%), so profiles are per
board — never one rule for all A-shares.
"""

from __future__ import annotations

from datetime import date

from ..domain.instruments import FeeSchedule, MarketProfile
from ..domain.base import new_id

import json
from pathlib import Path

_FEES_SOURCE = "exchange public fee schedule; simulated defaults confirmed by TASK-20261007-04"
_WHITELIST_PATH = Path(__file__).resolve().parents[3] / "data" / "etf_whitelist.json"
_ETF_WHITELIST_MAP: dict[str, dict] = {}


def _get_etf_map() -> dict[str, dict]:
    global _ETF_WHITELIST_MAP
    if not _ETF_WHITELIST_MAP and _WHITELIST_PATH.exists():
        with open(_WHITELIST_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
            _ETF_WHITELIST_MAP = {item["symbol"]: item for item in items}
    return _ETF_WHITELIST_MAP


def etf_profile(symbol: str, meta: dict | None = None) -> MarketProfile:
    etf_map = _get_etf_map()
    info = meta or etf_map.get(symbol) or {}
    market = info.get("market", "XSHG")
    settlement = info.get("settlement_rule", "T+0")
    price_limit = info.get("price_limit_ratio", "0.10")
    price_tick = info.get("price_tick", "0.001")
    effective_from = date.fromisoformat(info.get("effective_from", "2026-01-01"))
    fees = FeeSchedule(
        id=new_id("fs"),
        trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate="0.0001",
        commission_min="0.1",
        stamp_duty_rate_sell="0.0",  # No stamp duty on ETFs
        transfer_fee_rate="0.00001",
    )
    return MarketProfile(
        id=new_id("mp"),
        trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-etf-{symbol}-{effective_from.isoformat()}",
        market=market,  # type: ignore[arg-type]
        security_type="etf",
        board="main",
        effective_from=effective_from,
        source_ref=info.get("source_url", "SSE/SZSE official ETF announcement"),
        price_limit_ratio=price_limit,
        lot_size=int(info.get("lot_size", 100)),
        price_tick=price_tick,
        settlement_rule=settlement,
        fees=fees,
    )


def a_share_profile(board: str = "main", market: str = "XSHG", effective_from: date | None = None) -> MarketProfile:
    effective_from = effective_from or date(2026, 1, 1)
    if board == "main":
        price_limit = "0.10"
    elif board in ("star", "chinext"):
        price_limit = "0.20"
    else:
        raise ValueError(f"unsupported board {board!r} in V1")
    fees = FeeSchedule(
        id=new_id("fs"), trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate="0.00025",
        commission_min="5",
        stamp_duty_rate_sell="0.0005",
        transfer_fee_rate="0.00001",
    )
    return MarketProfile(
        id=new_id("mp"), trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-{market}-{board}-{effective_from.isoformat()}",
        market=market,  # type: ignore[arg-type]
        board=board,
        effective_from=effective_from,
        source_ref=_FEES_SOURCE,
        price_limit_ratio=price_limit,
        fees=fees,
    )


def get_profile(instrument_id: str) -> MarketProfile:
    """Board from instrument suffix/code conventions; V1 covers main board
    fixtures only and refuses unknown codes instead of guessing rules."""
    code = instrument_id.split(".")[0]
    suffix = instrument_id.split(".")[-1] if "." in instrument_id else ""
    if suffix == "SH":
        market = "XSHG"
        board = "main"
    elif suffix == "SZ":
        market = "XSHE"
        board = "main"
    else:
        raise ValueError(f"cannot infer market profile for {instrument_id!r}")

    etf_map = _get_etf_map()
    if instrument_id in etf_map:
        return etf_profile(instrument_id, etf_map[instrument_id])
    if code.startswith(("51", "58", "15", "16")):
        raise ValueError(f"ETF {instrument_id!r} not in verified whitelist; unknown rules cannot be traded")

    supported = ('600', '601', '603', '605', '688') if suffix == 'SH' else ('000', '001', '002', '003', '300')
    if len(code) != 6 or not code.isdigit() or not code.startswith(supported):
        raise ValueError('instrument has no supported execution profile; use the observation-only market entry')

    if code.startswith("688") or code.startswith("300"):
        board = "star" if code.startswith("688") else "chinext"
        if board == "chinext":
            raise ValueError("ChiNext (300xxx) out of V1 fixture scope; extend profiles first")
    return a_share_profile(board=board, market=market)


def convertible_bond_profile(symbol: str, rule=None) -> MarketProfile:
    from .rules import CANONICAL_RULES
    r = rule or CANONICAL_RULES.get(symbol)
    market = "XSHG" if symbol.endswith(".SH") else "XSHE"
    lot_size = r.lot_size if r and r.lot_size else 10
    price_tick = str(r.tick_size) if r and r.tick_size else "0.01"
    fees = FeeSchedule(
        id=new_id("fs"),
        trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate=str(r.fee_rate) if r and r.fee_rate else "0.0001",
        commission_min="1.0",
        stamp_duty_rate_sell="0.0",
        transfer_fee_rate="0.0",
    )
    return MarketProfile(
        id=new_id("mp"),
        trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-cb-{symbol}-2024-01-01",
        market=market,  # type: ignore[arg-type]
        security_type="convertible_bond",
        board="main",
        effective_from=date(2024, 1, 1),
        source_ref=r.source_url if r else "https://www.sse.com.cn/",
        price_limit_ratio="0.20",
        lot_size=lot_size,
        price_tick=price_tick,
        settlement_rule="T+0",
        fees=fees,
    )


def lof_profile(symbol: str, rule=None) -> MarketProfile:
    from .rules import CANONICAL_RULES
    r = rule or CANONICAL_RULES.get(symbol)
    market = "XSHE" if symbol.endswith(".SZ") else "XSHG"
    settlement = "T+1" if r and r.t0 is False else "T+0"
    lot_size = r.lot_size if r and r.lot_size else 100
    price_tick = str(r.tick_size) if r and r.tick_size else "0.001"
    fees = FeeSchedule(
        id=new_id("fs"),
        trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate=str(r.fee_rate) if r and r.fee_rate else "0.0001",
        commission_min="0.1",
        stamp_duty_rate_sell="0.0",
        transfer_fee_rate="0.0",
    )
    return MarketProfile(
        id=new_id("mp"),
        trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-lof-{symbol}-2024-01-01",
        market=market,  # type: ignore[arg-type]
        security_type="lof",
        board="main",
        effective_from=date(2024, 1, 1),
        source_ref=r.source_url if r else "https://www.szse.cn/",
        price_limit_ratio="0.10",
        lot_size=lot_size,
        price_tick=price_tick,
        settlement_rule=settlement,
        fees=fees,
    )


def hk_stock_profile(symbol: str, rule=None) -> MarketProfile:
    from .rules import CANONICAL_RULES
    r = rule or CANONICAL_RULES.get(symbol)
    lot_size = r.lot_size if r and r.lot_size else 100
    price_tick = str(r.tick_size) if r and r.tick_size else "0.20"
    fees = FeeSchedule(
        id=new_id("fs"),
        trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate=str(r.fee_rate) if r and r.fee_rate else "0.0013",
        commission_min="15.0",
        stamp_duty_rate_sell="0.001",
        transfer_fee_rate="0.00005",
        currency="HKD",
    )
    return MarketProfile(
        id=new_id("mp"),
        trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-hk-{symbol}-2024-01-01",
        market="XHKG",  # type: ignore[arg-type]
        security_type="hk_stock",
        board="main",
        effective_from=date(2024, 1, 1),
        source_ref=r.source_url if r else "https://www.hkex.com.hk/",
        price_limit_ratio="0.50",
        lot_size=lot_size,
        price_tick=price_tick,
        settlement_rule="T+0",
        timezone="Asia/Hong_Kong",
        fees=fees,
    )


def futures_profile(symbol: str, rule=None) -> MarketProfile:
    from .rules import CANONICAL_RULES
    r = rule or CANONICAL_RULES.get(symbol)
    ex_str = symbol.split(".")[-1] if "." in symbol else "SHFE"
    ex_map = {"SHFE": "XSGE", "DCE": "XDCE", "CZCE": "XZCE", "CFFEX": "CCFX", "INE": "XINE"}
    market = ex_map.get(ex_str, "XSGE")
    lot_size = r.lot_size if r and r.lot_size else 1
    price_tick = str(r.tick_size) if r and r.tick_size else "1.0"
    fees = FeeSchedule(
        id=new_id("fs"),
        trace_id=new_id("tr"),
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
        commission_rate="0.0001",
        commission_min="5.0",
        stamp_duty_rate_sell="0.0",
        transfer_fee_rate="0.0",
        currency="CNY",
    )
    return MarketProfile(
        id=new_id("mp"),
        trace_id=new_id("tr"),
        created_at=fees.created_at,
        profile_id=f"mp-fut-{symbol}-2024-01-01",
        market=market,  # type: ignore[arg-type]
        security_type="futures",
        board="main",
        effective_from=date(2024, 1, 1),
        source_ref=r.source_url if r else "https://www.shfe.com.cn/",
        price_limit_ratio="0.10",
        lot_size=lot_size,
        price_tick=price_tick,
        settlement_rule="T+0",
        short_selling_allowed=True,
        fees=fees,
    )


def get_multi_asset_profile(instrument_id: str, rule=None) -> MarketProfile:
    etf_map = _get_etf_map()
    if instrument_id in etf_map:
        return etf_profile(instrument_id, etf_map[instrument_id])

    from .rules import CANONICAL_RULES, get_rule
    r = rule or get_rule(instrument_id)

    if instrument_id.endswith(".HK"):
        return hk_stock_profile(instrument_id, r)

    if any(instrument_id.endswith(f".{ex}") for ex in ("SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX")):
        return futures_profile(instrument_id, r)

    if r:
        if r.kind == "convertible_bond":
            return convertible_bond_profile(instrument_id, r)
        if r.kind == "lof":
            return lof_profile(instrument_id, r)
        if r.kind == "etf":
            return etf_profile(instrument_id)

    code = instrument_id.split(".")[0]
    if code.startswith(("11", "12")):
        return convertible_bond_profile(instrument_id, r)
    if code.startswith("16"):
        return lof_profile(instrument_id, r)

    return get_profile(instrument_id)



def etf_display_names() -> dict[str, str]:
    """Verified Chinese fund names from the ETF whitelist (symbol -> name).

    Workbench rendering only; the trading identity stays the instrument code.
    """
    return {symbol: str(info["name"]) for symbol, info in _get_etf_map().items()
            if info.get("name")}
