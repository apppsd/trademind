"""Explicit verified-ETF minute source; no stock SDK or fabricated bars."""
from datetime import datetime, timezone
from decimal import Decimal

import requests

from ...errors import DataUnavailable
from ..base import BEIJING, MinuteBar
from ..profiles import get_profile


class SinaETFAdapter:
    name = "sina_etf"
    endpoint = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"

    def __init__(self):
        self.session = requests.Session()

    def capabilities(self):
        return {"minute_bars": True, "daily_bars": False}

    def get_daily_bars(self, *args):
        raise DataUnavailable("Sina ETF adapter serves minutes only")

    def get_news(self, *args):
        return []

    def get_fundamentals(self, *args):
        return []

    def get_minute_bars(self, instrument_id, start, end, period="1m"):
        try:
            profile = get_profile(instrument_id)
        except ValueError:
            raise DataUnavailable("Sina ETF requires verified ETF membership") from None
        if profile.security_type != "etf" or period != "1m":
            raise DataUnavailable("Sina ETF requires verified ETF and one-minute period")
        if start.tzinfo is None or end.tzinfo is None or start > end:
            raise DataUnavailable("Sina ETF requires an aware ordered time range")
        code, suffix = instrument_id.split(".")
        try:
            response = self.session.get(self.endpoint, params={
                "symbol": suffix.lower()+code, "scale": "1", "ma": "no", "datalen": "1970"
            }, timeout=8)
            response.raise_for_status()
            rows = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DataUnavailable("Sina ETF minute request failed") from exc
        if not isinstance(rows, list):
            raise DataUnavailable("Sina ETF invalid response envelope")
        fetched = datetime.now(timezone.utc)
        closed_before = fetched.replace(second=0, microsecond=0)
        bars = []
        for row in rows:
            try:
                dt = datetime.fromisoformat(row["day"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=BEIJING)
                values = {key: Decimal(str(row[key])) for key in
                          ("open", "high", "low", "close", "volume", "amount")}
                if not all(v.is_finite() and v >= 0 for v in values.values()):
                    raise ValueError()
                low, high = values["low"], values["high"]
                if not (0 < low <= values["open"] <= high and low <= values["close"] <= high):
                    raise ValueError()
                volume, amount = values["volume"], values["amount"]
                if (volume == 0 and amount != 0) or (volume > 0 and not
                    low*Decimal("0.98") <= amount/volume <= high*Decimal("1.02")):
                    raise ValueError()
            except (KeyError, ValueError, TypeError, ArithmeticError):
                raise DataUnavailable("Sina ETF invalid fields or quantity units") from None
            if start <= dt <= end and dt < closed_before:
                bars.append(MinuteBar(instrument_id=instrument_id, trade_date=dt.date(),
                    bar_time=dt, provider=self.name, source_time=dt,
                    fetched_at=fetched, available_at=fetched, period="1m", **values))
        return bars
