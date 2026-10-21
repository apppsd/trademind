"""Adapter interface and record models (TASK-08).

available_at semantics per record: the earliest time this system could have
obtained the row. Fixture adapter derives it from bar close + publish delay;
live adapters must fill it from the vendor feed, never from wall clock alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.base import require_tz

BEIJING = timezone(timedelta(hours=8))
#: daily bars become obtainable after close + publish delay (vendor dependent)
FIXTURE_PUBLISH_DELAY = timedelta(minutes=30)


def bar_close_time(d: date) -> datetime:
    """A-share continuous session closes 15:00 Asia/Shanghai."""
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=BEIJING)


class Bar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal | None = None
    volume: Decimal  # shares
    amount: Decimal  # CNY
    provider: str
    source_time: datetime  # vendor timestamp for the bar (close time)
    fetched_at: datetime
    available_at: datetime
    suspended: bool = False
    revision: int = 1
    unit: Literal["CNY", "yuan"] = "CNY"

    @field_validator("source_time", "fetched_at", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @property
    def pct_change(self) -> Decimal | None:
        if not self.pre_close or self.pre_close == 0:
            return None
        return (self.close - self.pre_close) / self.pre_close


class MinuteBar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    trade_date: date
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # shares / units
    amount: Decimal  # CNY
    provider: str
    source_time: datetime
    fetched_at: datetime
    available_at: datetime
    period: Literal["1m", "5m", "15m", "30m", "60m"] = "1m"
    revision: int = 1
    unit: Literal["CNY", "yuan"] = "CNY"

    @field_validator("bar_time", "source_time", "fetched_at", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


class QuoteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    source_time: datetime
    fetched_at: datetime
    available_at: datetime
    provider: str

    @field_validator("source_time", "fetched_at", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


class NewsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_id: str
    instrument_ids: list[str]
    title: str
    body: str
    event_time: datetime | None
    published_at: datetime | None
    available_at: datetime
    source_uri: str
    provider: str
    missing_reason: str | None = None

    @field_validator("event_time", "published_at", "available_at")
    @classmethod
    def _aware(cls, v):
        if v is not None:
            require_tz(v)
        return v


class FundamentalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    report_period: str  # e.g. "2026H1"
    announce_date: date
    available_at: datetime
    metrics: dict[str, Decimal]
    revision: int = 1
    supersedes_key: str | None = None  # key of corrected record
    provider: str


class MarketDataAdapter(ABC):
    """One provider. Implementations must be lazy about SDK imports so the
    offline fixture path never requires them."""

    name: str = "abstract"

    @abstractmethod
    def get_daily_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]: ...

    def get_minute_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        period: str = "1m",
    ) -> list[MinuteBar]:
        return []

    @abstractmethod
    def get_calendar(self, start: date, end: date) -> list[date]: ...

    def get_quote(self, instrument_id: str) -> QuoteRecord | None:
        return None

    def get_news(self, instrument_id: str, start: datetime, end: datetime) -> list[NewsRecord]:
        return []

    def get_fundamentals(self, instrument_id: str) -> list[FundamentalRecord]:
        return []

    def capabilities(self) -> dict[str, bool]:
        return {
            "daily_bars": True,
            "minute_bars": False,
            "calendar": True,
            "quote": False,
            "news": False,
            "fundamentals": False,
        }
