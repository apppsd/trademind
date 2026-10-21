"""ContextBuilder: compile the point-in-time ContextSnapshot (TASK-09).

Visibility rules: only evidence with available_at <= as_of; latest visible
revision of corrections; memories filtered by available_at; the portfolio
view is the ledger snapshot at as_of. Missing data is listed, never zeroed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..config import RiskConfig
from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot, FeatureVector, PortfolioView
from ..domain.evidence import Evidence
from ..domain.enums import DataQuality, Dataset
from ..errors import DataUnavailable, NotFresh, ProviderError
from ..market.calendar import TradingCalendar
from ..market.router import DatasetRouter
from ..portfolio import Ledger
from .features import (
    FEATURE_VERSION,
    MINUTE_FEATURE_VERSION,
    bars_visible_at,
    compute_features,
    compute_minute_features,
    minute_bars_visible_at,
)

NEWS_BODY_BUDGET_CHARS = 600


class ContextBuilder:
    def __init__(self, router: DatasetRouter, calendar: TradingCalendar,
                 ledger: Ledger | None = None, ttl_seconds: int = 900,
                 universe_version: str = "univ-dev",
                 memory_view=None) -> None:
        self.router = router
        self.calendar = calendar
        self.ledger = ledger
        self.ttl_seconds = ttl_seconds
        self.universe_version = universe_version
        self.memory_view = memory_view  # callable(instrument_id, as_of) -> list[dict]

    def build(self, instrument_id: str, as_of: datetime) -> ContextSnapshot:
        evidence_ids: list[str] = []
        missing: dict[str, str] = {}

        # -- bars (point-in-time) -------------------------------------------
        end = self.calendar.last_on_or_before(as_of)
        start = self.calendar.prev(end, 90)  # ~120 trading days window
        bars, _report = self.router.get_daily_bars(instrument_id, start, end, as_of=as_of)
        visible = bars_visible_at(bars, as_of)
        values, fmiss = compute_features(visible)
        missing.update(fmiss)
        for b in visible[-1:]:
            ev = self._evidence_from_bar(b)
            evidence_ids.append(ev.evidence_id)

        # -- news -------------------------------------------------------------
        news = self.router.get_news(
            instrument_id,
            as_of - __import__("datetime").timedelta(days=30),
            as_of,
        )
        news_items = []
        for n in sorted((n for n in news if n.available_at <= as_of and
                         (n.published_at is None or n.published_at <= as_of)), key=lambda x: x.available_at)[-5:]:
            news_items.append({
                "news_id": n.news_id,
                "title": n.title,
                "body": n.body[:NEWS_BODY_BUDGET_CHARS],
                "published_at": n.published_at.isoformat() if n.published_at else None,
                "available_at": n.available_at.isoformat(),
            })

        # -- fundamentals (latest visible revision) -----------------------------
        funds = self.router.get_fundamentals(instrument_id)
        by_period: dict[str, dict] = {}
        for f in sorted(funds, key=lambda x: (x.report_period, x.revision)):
            if f.available_at <= as_of:
                by_period[f.report_period] = {
                    "report_period": f.report_period,
                    "announce_date": f.announce_date.isoformat(),
                    "metrics": {k: str(v) for k, v in f.metrics.items()},
                    "revision": f.revision,
                }
        if not by_period:
            missing["fundamentals"] = "no fundamentals visible at as_of"

        # -- account + memory ---------------------------------------------------
        portfolio_view = None
        if self.ledger is not None:
            prices = {instrument_id: visible[-1].close} if visible else {}
            for pos in self.ledger.snapshot(as_of).positions:
                if pos.instrument_id not in prices:
                    other, _ = self.router.get_daily_bars(pos.instrument_id, start, end,
                                                          as_of=as_of)
                    visible_other = bars_visible_at(other, as_of)
                    if not visible_other:
                        raise DataUnavailable(f"missing visible valuation for {pos.instrument_id}")
                    prices[pos.instrument_id] = visible_other[-1].close
            snap = self.ledger.snapshot(as_of, prices)
            portfolio_view = PortfolioView(
                id=new_id("pv"), trace_id=new_id("tr"), created_at=utcnow(),
                portfolio_version=snap.version, account_id=snap.account_id,
                as_of=snap.as_of, currency=snap.currency, cash=snap.cash,
                equity=snap.equity, reserved_cash=snap.reserved_cash,
                positions={
                    p.instrument_id: {
                        "qty": str(p.quantity), "avg_cost": str(p.avg_cost),
                        "market_value": str(p.market_value or 0),
                        "weight": str((p.market_value or 0) / snap.equity) if snap.equity else "0",
                    }
                    for p in snap.positions
                },
            )

        memory_ids: list[str] = []
        if self.memory_view is not None:
            for rec in self.memory_view(instrument_id, as_of):
                memory_ids.append(rec["memory_id"])

        fv = FeatureVector(
            id=new_id("fv"), trace_id=new_id("tr"), created_at=utcnow(),
            feature_version=FEATURE_VERSION, instrument_id=instrument_id,
            as_of=as_of, values=values, missing_fields=missing,
            quality=DataQuality.DEGRADED if missing else DataQuality.OK,
        )
        snap = ContextSnapshot(
            id=new_id("cs"), trace_id=new_id("tr"), created_at=utcnow(),
            context_id=new_id("ctx"), as_of=as_of,
            expires_at=as_of + __import__("datetime").timedelta(seconds=self.ttl_seconds),
            instrument_id=instrument_id, universe_version=self.universe_version,
            evidence_ids=evidence_ids, features=fv, portfolio=portfolio_view,
            memory_ids=memory_ids, missing_fields=missing,
            input_budget_tokens=None,
        )
        snap.aux = {
            "news": news_items, "fundamentals": list(by_period.values()),
            "last_bars": [
                {"date": b.trade_date.isoformat(), "open": str(b.open), "high": str(b.high),
                 "low": str(b.low), "close": str(b.close), "volume": str(b.volume)}
                for b in visible[-10:]
            ],
        }
        snap.content_hash = snap.compute_hash()
        return snap

    @staticmethod
    def _evidence_from_bar(b) -> Evidence:
        return Evidence(
            id=new_id("ev"), trace_id=new_id("tr"), created_at=utcnow(),
            evidence_id=new_id("ev"), source_id=b.provider,
            source_uri=f"{b.provider}://daily_bars/{b.instrument_id}/{b.trade_date.isoformat()}",
            dataset=Dataset.DAILY_BARS, instrument_ids=[b.instrument_id],
            event_time=b.source_time, published_at=b.source_time,
            available_at=b.available_at, ingested_at=b.fetched_at,
            content_hash=f"{b.instrument_id}:{b.trade_date}:{b.close}:{b.revision}",
            payload_ref=f"{b.provider}://daily_bars/{b.instrument_id}",
        )

    @staticmethod
    def _evidence_from_minute_bar(b) -> Evidence:
        return Evidence(
            id=new_id("ev"), trace_id=new_id("tr"), created_at=utcnow(),
            evidence_id=new_id("ev"), source_id=b.provider,
            source_uri=f"{b.provider}://minute_bars/{b.instrument_id}/{b.bar_time.isoformat()}",
            dataset=Dataset.DAILY_BARS, instrument_ids=[b.instrument_id],
            event_time=b.source_time, published_at=b.source_time,
            available_at=b.available_at, ingested_at=b.fetched_at,
            content_hash=f"{b.instrument_id}:{b.bar_time.isoformat()}:{b.close}:{b.revision}",
            payload_ref=f"{b.provider}://minute_bars/{b.instrument_id}",
        )

    def build_minute(
        self,
        instrument_id: str,
        as_of: datetime,
        period: str = "1m",
        max_stale_seconds: int = 900,
    ) -> ContextSnapshot:
        import datetime as dt_mod
        evidence_ids: list[str] = []
        missing: dict[str, str] = {}

        start = as_of - dt_mod.timedelta(days=2)
        bars, _report = self.router.get_minute_bars(
            instrument_id, start, as_of, period=period,
            as_of=as_of, quote_max_age_seconds=max_stale_seconds)
        if getattr(_report, 'limit_violations', None):
            raise DataUnavailable('minute OHLC quality validation failed')
        visible = minute_bars_visible_at(bars, as_of)
        values, fmiss, regime = compute_minute_features(visible)
        missing.update(fmiss)

        if visible:
            age_seconds = (as_of - visible[-1].bar_time).total_seconds()
            if age_seconds > max_stale_seconds:
                missing["freshness"] = f"latest bar is stale by {age_seconds:.0f}s > {max_stale_seconds}s"
            for b in visible[-1:]:
                ev = self._evidence_from_minute_bar(b)
                evidence_ids.append(ev.evidence_id)
        else:
            missing["bars"] = "no visible minute bars at as_of"

        portfolio_view = None
        valuation_incomplete: list[str] = []
        if self.ledger is not None:
            prices = {instrument_id: visible[-1].close} if visible else {}
            for pos in self.ledger.snapshot(as_of).positions:
                if pos.instrument_id in prices:
                    continue
                try:
                    other, _ = self.router.get_minute_bars(
                        pos.instrument_id, start, as_of, period=period,
                        as_of=as_of, quote_max_age_seconds=max_stale_seconds)
                    visible_other = minute_bars_visible_at(other, as_of)
                    if not visible_other or (as_of - visible_other[-1].bar_time).total_seconds() > max_stale_seconds:
                        raise DataUnavailable(f"missing visible valuation for {pos.instrument_id}")
                    prices[pos.instrument_id] = visible_other[-1].close
                except (DataUnavailable, NotFresh, ProviderError):
                    # One holding's stale quote must not block this symbol's
                    # decision (ISSUE-20261013-05). The ledger falls back to avg
                    # cost for that mark, and Pipeline records the position in
                    # valuation_failures so the Guard refuses new risk while the
                    # account valuation is incomplete.
                    valuation_incomplete.append(pos.instrument_id)
            snap = self.ledger.snapshot(as_of, prices)
            portfolio_view = PortfolioView(
                id=new_id("pv"), trace_id=new_id("tr"), created_at=utcnow(),
                portfolio_version=snap.version, account_id=snap.account_id,
                as_of=snap.as_of, currency=snap.currency, cash=snap.cash,
                equity=snap.equity, reserved_cash=snap.reserved_cash,
                positions={
                    p.instrument_id: {
                        "qty": str(p.quantity), "avg_cost": str(p.avg_cost),
                        "market_value": str(p.market_value or 0),
                        "weight": str((p.market_value or 0) / snap.equity) if snap.equity else "0",
                    }
                    for p in snap.positions
                },
            )

        fv = FeatureVector(
            id=new_id("fv"), trace_id=new_id("tr"), created_at=utcnow(),
            feature_version=MINUTE_FEATURE_VERSION, instrument_id=instrument_id,
            as_of=as_of, values=values,
            window={"period": period, "regime": regime},
            missing_fields=missing,
            quality=DataQuality.DEGRADED if missing else DataQuality.OK,
        )
        snap = ContextSnapshot(
            id=new_id("cs"), trace_id=new_id("tr"), created_at=utcnow(),
            context_id=new_id("ctx"), as_of=as_of,
            expires_at=as_of + dt_mod.timedelta(seconds=self.ttl_seconds),
            instrument_id=instrument_id, universe_version=self.universe_version,
            evidence_ids=evidence_ids, features=fv, portfolio=portfolio_view,
            memory_ids=[], missing_fields=missing,
            input_budget_tokens=None,
        )
        snap.aux = {
            "minute_bars": [
                {
                    "time": b.bar_time.isoformat(),
                    "fetched_at": b.fetched_at.isoformat(),
                    "provider": b.provider,
                    "price_unit": "CNY",
                    "volume_unit": "share",
                    "available_at": b.available_at.isoformat(),
                    "source_time": b.source_time.isoformat(),
                    "open": str(b.open),
                    "high": str(b.high),
                    "low": str(b.low),
                    "close": str(b.close),
                    "volume": str(b.volume),
                }
                for b in visible[-10:]
            ],
            "regime": regime,
            "valuation_incomplete": valuation_incomplete,
        }
        snap.content_hash = snap.compute_hash()
        return snap
