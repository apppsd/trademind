"""Per-board A-share profiles for the stock decision chain (ARCH-028).

Task27-05. Instrument convention matches the V1 chain: ``<code>.SH`` /
``<code>.SZ``. This factory deliberately does NOT touch
``market.profiles.get_profile`` — the V1 fixture/paper chain keeps its
current raise-for-ChiNext behavior; only the stock chain resolves boards
through here. Boards follow the observation-pool v1 scope (stock_list_ak):
SH main 60x, SZ main 000/001/002/003, ChiNext 300/301 (±20% daily limit).
Rules/fees come from the same ``a_share_profile`` factory the V1 chain
uses (commission min, sell-side stamp duty, transfer fee; T+1, lot 100).
"""
from __future__ import annotations

from typing import Any

from ..domain.instruments import MarketProfile
from .profiles import a_share_profile

_BOARD_BY_CODE = (
    (("60",), "SH", "main", "XSHG"),
    (("000", "001", "002", "003"), "SZ", "main", "XSHE"),
    (("300", "301"), "SZ", "chinext", "XSHE"),
)


def split_instrument(instrument_id: str) -> tuple[str, str]:
    """'600000.SH' → ('600000', 'SH'); rejects malformed ids explicitly."""
    if "." not in instrument_id:
        raise ValueError(f"stock instrument must be '<code>.<SSE|SZSE>' style: {instrument_id!r}")
    code, suffix = instrument_id.split(".", 1)
    if len(code) != 6 or not code.isdigit() or suffix not in ("SH", "SZ"):
        raise ValueError(f"malformed stock instrument: {instrument_id!r}")
    return code, suffix


def instrument_id_for(code: str, board: str) -> str:
    """Observation-pool board → chain instrument id ('sz_main' → '.SZ')."""
    suffix = {"sh_main": "SH", "sz_main": "SZ", "chinext": "SZ"}.get(board)
    if suffix is None:
        raise ValueError(f"unknown board for instrument id: {board!r}")
    return f"{code}.{suffix}"


def stock_profile_for_instrument(instrument_id: str) -> MarketProfile:
    """Resolve the board profile for a stock-chain instrument id."""
    code, suffix = split_instrument(instrument_id)
    for prefixes, want_suffix, board, market in _BOARD_BY_CODE:
        if suffix == want_suffix and code.startswith(prefixes):
            return a_share_profile(board=board, market=market)
    raise ValueError(
        f"{instrument_id!r} is outside the stock-chain v1 board scope "
        "(SH/SZ main, ChiNext); observation-only, no execution profile"
    )


def assert_stock_chain_ready(instrument_id: str, profile: Any) -> None:
    """Guard for callers wiring the stock chain: T+1 lot-100 profile only."""
    if profile.t_plus_days != 1 or int(profile.lot_size) != 100:
        raise ValueError(f"{instrument_id!r}: stock chain requires T+1 lot-100 profile")


__all__ = [
    "split_instrument",
    "instrument_id_for",
    "stock_profile_for_instrument",
    "assert_stock_chain_ready",
]
