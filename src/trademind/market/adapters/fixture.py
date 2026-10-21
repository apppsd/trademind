"""Fixture adapter: deterministic offline data, no API keys (TASK-08)."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys

from ..base import (
    BEIJING,
    FIXTURE_PUBLISH_DELAY,
    Bar,
    FundamentalRecord,
    MarketDataAdapter,
    MinuteBar,
    NewsRecord,
    bar_close_time,
)

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "data" / "fixtures"
if not DEFAULT_FIXTURE_DIR.exists():
    DEFAULT_FIXTURE_DIR = Path(sys.prefix) / 'share' / 'trademind' / 'fixtures'


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING)
    return dt


class FixtureAdapter(MarketDataAdapter):
    name = "fixture"

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_FIXTURE_DIR
        self._bars_cache: dict[str, list[Bar]] = {}

    def _daily_path(self, instrument_id: str) -> Path:
        return self.root / "daily" / f"{instrument_id}.csv"

    def get_daily_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        if instrument_id not in self._bars_cache:
            rows: list[Bar] = []
            p = self._daily_path(instrument_id)
            if not p.exists():
                self._bars_cache[instrument_id] = []
            else:
                with open(p, encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        d = date.fromisoformat(row["trade_date"])
                        close_t = bar_close_time(d)
                        fetched = close_t + FIXTURE_PUBLISH_DELAY
                        pre = row.get("pre_close") or ""
                        rows.append(Bar(
                            instrument_id=instrument_id,
                            trade_date=d,
                            open=Decimal(row["open"]),
                            high=Decimal(row["high"]),
                            low=Decimal(row["low"]),
                            close=Decimal(row["close"]),
                            pre_close=Decimal(pre) if pre else None,
                            volume=Decimal(row["volume"]),
                            amount=Decimal(row["amount"]),
                            provider="fixture",
                            source_time=close_t,
                            fetched_at=fetched,
                            available_at=fetched,
                            suspended=row.get("suspended", "") == "1",
                        ))
                self._bars_cache[instrument_id] = rows
        return [b for b in self._bars_cache[instrument_id] if start <= b.trade_date <= end]

    def get_calendar(self, start: date, end: date) -> list[date]:
        cal: list[date] = []
        p = self.root / "calendar_xshg.csv"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    d = date.fromisoformat(row["date"])
                    if start <= d <= end:
                        cal.append(d)
        return cal

    def get_news(self, instrument_id: str, start: datetime, end: datetime) -> list[NewsRecord]:
        p = self.root / "news" / "news.json"
        if not p.exists():
            return []
        out = []
        for rec in json.loads(p.read_text(encoding="utf-8")):
            if instrument_id not in rec["instrument_ids"]:
                continue
            available = _parse_dt(rec["available_at"])
            if not (start <= available <= end):
                continue
            out.append(NewsRecord(
                news_id=rec["news_id"],
                instrument_ids=rec["instrument_ids"],
                title=rec["title"],
                body=rec["body"],
                event_time=_parse_dt(rec["event_time"]) if rec.get("event_time") else None,
                published_at=_parse_dt(rec["published_at"]) if rec.get("published_at") else None,
                available_at=available,
                source_uri=rec["source_uri"],
                provider="fixture",
                missing_reason=rec.get("missing_reason"),
            ))
        return out

    def get_fundamentals(self, instrument_id: str) -> list[FundamentalRecord]:
        p = self.root / "fundamentals" / "fundamentals.json"
        if not p.exists():
            return []
        out = []
        for rec in json.loads(p.read_text(encoding="utf-8")):
            if rec["instrument_id"] != instrument_id:
                continue
            out.append(FundamentalRecord(
                instrument_id=rec["instrument_id"],
                report_period=rec["report_period"],
                announce_date=date.fromisoformat(rec["announce_date"]),
                available_at=_parse_dt(rec["available_at"]),
                metrics={k: Decimal(str(v)) for k, v in rec["metrics"].items()},
                revision=rec.get("revision", 1),
                supersedes_key=rec.get("supersedes_key"),
                provider="fixture",
            ))
        return out

    def get_minute_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        period: str = "1m",
    ) -> list[MinuteBar]:
        p = self.root / "minute" / f"{instrument_id}_{period}.csv"
        if not p.exists():
            p = self.root / "minute" / f"{instrument_id}.csv"
        if not p.exists():
            return []
        out: list[MinuteBar] = []
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                bt = _parse_dt(row["bar_time"])
                if not (start <= bt <= end):
                    continue
                d = date.fromisoformat(row["trade_date"])
                fetched = bt + timedelta(seconds=1)
                out.append(
                    MinuteBar(
                        instrument_id=instrument_id,
                        trade_date=d,
                        bar_time=bt,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                        amount=Decimal(row["amount"]),
                        provider="fixture",
                        source_time=bt,
                        fetched_at=fetched,
                        available_at=fetched,
                        period=period,  # type: ignore[arg-type]
                    )
                )
        return out

    def capabilities(self) -> dict[str, bool]:
        return {
            "daily_bars": True,
            "minute_bars": True,
            "calendar": True,
            "quote": False,
            "news": True,
            "fundamentals": True,
        }
