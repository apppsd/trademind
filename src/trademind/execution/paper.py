"""Paper broker: fills only at prices visible after the decision (TASK-16).

Daily-bar simulation: a limit order placed after bar T is eligible at bar T+n
(fill_lag_bars, default 1). BUY fills when bar.low <= limit; price is
min(limit, bar.open) worsened by slippage. SELL symmetric. Suspension days
produce no fill; the order simply waits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from ..domain.base import new_id
from ..domain.enums import OrderSide, OrderStatus
from ..domain.orders import Fill, Order
from ..market.calendar import TradingCalendar
from ..market.router import DatasetRouter
from ..market.base import BEIJING, MinuteBar
from .price_quant import quantize_price_to_tick


@dataclass
class PaperConfig:
    slippage_bps: int = 5
    fill_lag_bars: int = 1
    minute_only: bool = False
    # Trading tick from the instrument's MarketProfile (B07). None keeps the
    # legacy decimal-places fallback below; the main chain (pipeline) always
    # passes the profile tick.
    price_tick: Decimal | str | None = None


class PaperBroker:
    def __init__(self, router: DatasetRouter, calendar: TradingCalendar,
                 fees, cfg: PaperConfig | None = None) -> None:
        self.router = router
        self.calendar = calendar
        self.fees = fees
        self.cfg = cfg or PaperConfig()

    def _eligible_minute_bars(self, instrument_id: str, after: datetime, now: datetime):
        if now <= after:
            return []
        try:
            bars, report = self.router.get_minute_bars(instrument_id, after, now, period="1m")
        except Exception:
            return []
        # A window the router itself rejected (structural quality or a standing
        # cross-source refusal) must never produce fills: the decision path
        # refuses on the same report, so execution must not launder it
        # (ISSUE-20261016-04). Unknown-quality windows stay unfilled too —
        # never traded as if the price were simply untouched.
        if report is not None and (not report.ok or report.limit_violations):
            return []
        eligible = sorted(
            (b for b in bars if after < b.source_time <= now and b.bar_time <= now and after < b.available_at <= now),
            key=lambda b: b.available_at,
        )
        return eligible[max(0, self.cfg.fill_lag_bars - 1):]

    def _eligible_bars(self, instrument_id: str, after: datetime, now: datetime):
        if now <= after:
            return []
        # as_of carries replay visibility into the router's per-date revision
        # merge (B04): without it a higher revision not yet visible at `now`
        # wins the merge and the caller-side visibility filter then drops the
        # whole date — a historically visible bar would vanish.
        bars, _ = self.router.get_daily_bars(instrument_id, after.date(), now.date(), as_of=now)
        eligible = sorted((b for b in bars if b.trade_date > after.astimezone(BEIJING).date()
                           and after < b.source_time and after < b.available_at <= now),
                          key=lambda b: b.available_at)
        return eligible[max(0, self.cfg.fill_lag_bars - 1):]

    def try_fill(self, order: Order, now: datetime) -> Fill | None:
        """One fill attempt; None means 'not yet' (market/suspension/price)."""
        after = max(order.created_at, order.updated_at) if order.filled_quantity > 0 else order.created_at
        minute_bars = self._eligible_minute_bars(order.instrument_id, after, now)
        if minute_bars:
            for bar in minute_bars:
                fill = self._fill_at_minute_bar(order, bar)
                if fill is not None:
                    return fill
            return None

        if self.cfg.minute_only:
            return None
        for bar in self._eligible_bars(order.instrument_id, after, now):
            fill = self._fill_at_bar(order, bar)
            if fill is not None:
                return fill
        return None

    def _quantize_fill_price(self, order: Order, raw: Decimal,
                             legacy_quant: Decimal) -> Decimal | None:
        """Quantize a raw fill price under the authorized limit (B07, R1).

        With a configured tick the price is rounded onto the tick lattice.
        The half-up lattice step may cross an off-lattice limit (a BUY
        rounds above its limit, a SELL below); the limit authorizes "no
        worse than this price", so a crossed round leaves no legal fill and
        the attempt is rejected (None). R1 instead clamped the crossed
        round back onto the limit, which filled at an off-lattice price and
        defeated the quantization. Without a tick the path's legacy quantum
        is kept verbatim (minute: limit-string decimal places; daily:
        0.01) so unconfigured constructions behave exactly as before.
        """
        if self.cfg.price_tick is not None:
            price = quantize_price_to_tick(raw, Decimal(str(self.cfg.price_tick)))
            if order.side == OrderSide.BUY and price > order.limit_price:
                return None  # lattice step crossed the authorized limit
            if order.side == OrderSide.SELL and price < order.limit_price:
                return None
            return price
        return raw.quantize(legacy_quant, rounding=ROUND_HALF_UP)

    def _fill_at_minute_bar(self, order: Order, bar: MinuteBar) -> Fill | None:
        if getattr(bar, "suspended", False) or bar.volume <= 0:
            return None
        slip = Decimal(1) + Decimal(self.cfg.slippage_bps) / Decimal(10000)
        slip_down = Decimal(1) - Decimal(self.cfg.slippage_bps) / Decimal(10000)
        if order.side == OrderSide.BUY:
            if bar.low > order.limit_price:
                return None  # limit never touched
            raw = min(order.limit_price, bar.open * slip)
        else:
            if bar.high < order.limit_price:
                return None
            raw = max(order.limit_price, bar.open * slip_down)
        quant = Decimal("0.001") if len(str(order.limit_price).split(".")[-1]) > 2 else Decimal("0.01")
        price = self._quantize_fill_price(order, raw, quant)
        if price is None:
            return None  # lattice round crossed the authorized limit (R1)
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return None
        fill = Fill(
            id=new_id("f"),
            trace_id=order.trace_id,
            created_at=datetime.now(bar.available_at.tzinfo),
            fill_id=new_id("f"),
            order_id=order.order_id,
            provider_fill_id=f"paper-{order.order_id}-{bar.source_time.strftime('%Y%m%d%H%M%S')}",
            quantity=remaining,
            price=price,
            fee=Decimal("0"),
            executed_at=bar.source_time,
            available_at=bar.available_at,
        )
        return fill

    def _fill_at_bar(self, order, bar):
        if bar.suspended or bar.volume <= 0:
            return None
        # A single-price limit board offers no reliable daily-bar execution evidence.
        if bar.low == bar.high and bar.pre_close and abs(bar.pct_change or 0) >= Decimal("0.095"):
            return None
        slip = Decimal(1) + Decimal(self.cfg.slippage_bps) / Decimal(10000)
        slip_down = Decimal(1) - Decimal(self.cfg.slippage_bps) / Decimal(10000)
        if order.side == OrderSide.BUY:
            if bar.low > order.limit_price:
                return None  # limit never touched
            raw = min(order.limit_price, bar.open * slip)
        else:
            if bar.high < order.limit_price:
                return None
            raw = max(order.limit_price, bar.open * slip_down)
        price = self._quantize_fill_price(order, raw, Decimal("0.01"))
        if price is None:
            return None  # lattice round crossed the authorized limit (R1)
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return None
        fill = Fill(
            id=new_id("f"), trace_id=order.trace_id, created_at=datetime.now(bar.available_at.tzinfo),
            fill_id=new_id("f"), order_id=order.order_id,
            provider_fill_id=f"paper-{order.order_id}-{bar.trade_date.isoformat()}",
            quantity=remaining, price=price, fee=Decimal("0"),
            executed_at=bar.source_time, available_at=bar.available_at,
        )
        return fill

    def remaining_status(self, order: Order) -> OrderStatus:
        if order.filled_quantity >= order.quantity:
            return OrderStatus.FILLED
        if order.filled_quantity > 0:
            return OrderStatus.PARTIALLY_FILLED
        return order.status
