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
from ..domain.instruments import MarketProfile
from ..domain.orders import Fill, Order
from ..errors import MarketClosed
from ..market.calendar import TradingCalendar
from ..market.router import DatasetRouter
from ..market.base import BEIJING, MinuteBar


@dataclass
class PaperConfig:
    slippage_bps: int = 5
    fill_lag_bars: int = 1
    minute_only: bool = False


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
        bars, _ = self.router.get_daily_bars(instrument_id, after.date(), now.date())
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
        price = raw.quantize(quant, rounding=ROUND_HALF_UP)
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
        price = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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
