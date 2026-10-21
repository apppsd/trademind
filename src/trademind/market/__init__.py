"""Market package: adapters, calendar, profiles, quality, router (ARCH-001 market).

Boundary: this package observes and normalizes; it never judges buy/sell.
Missing datasets are reported as missing, never synthesized.
"""

from .base import Bar, FundamentalRecord, MarketDataAdapter, NewsRecord, QuoteRecord
from .calendar import TradingCalendar
from .profiles import a_share_profile, get_profile
from .quality import QualityReport, normalize_bars
from .router import DatasetRouter

__all__ = [
    "Bar",
    "DatasetRouter",
    "FundamentalRecord",
    "MarketDataAdapter",
    "NewsRecord",
    "QualityReport",
    "QuoteRecord",
    "TradingCalendar",
    "a_share_profile",
    "get_profile",
]
