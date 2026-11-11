"""Shared fill-price quantization onto an instrument's price tick (B07).

``Decimal.quantize(tick)`` only aligns the result's decimal places with the
tick's exponent — it does not round onto the tick lattice (tick ``0.20``
admits ``1.23`` via quantize, which is no multiple of 0.20). The V1 broker
instead picked the quantum from the limit price's string decimal places, so
the same ETF order at limit ``1.50`` and ``1.500`` filled at different
precision. Both paper brokers quantize through :func:`quantize_price_to_tick`.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def quantize_price_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Round ``price`` to the nearest multiple of ``tick`` (half up).

    The integer step count ``price / tick`` is rounded first, then
    multiplied back — never ``quantize(tick)``.
    """
    tick = Decimal(str(tick))
    if tick <= 0:
        raise ValueError(f"price tick must be positive, got {tick}")
    steps = (Decimal(str(price)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (steps * tick).quantize(tick)
