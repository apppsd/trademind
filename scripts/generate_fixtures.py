"""Generate deterministic offline fixtures (TASK-08).

Reproducible with a fixed seed: same command, same bytes. Regenerate only
when the fixture contract changes; tests depend on stability.

    .venv/Scripts/python scripts/generate_fixtures.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.market.base import bar_close_time  # noqa: E402

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "data" / "fixtures"

# National holidays inside the window (simplified official holiday list)
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 2),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 4, 6),
    date(2026, 5, 1),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
    date(2026, 10, 8),
}

UNIVERSE = {
    "600519.SH": {"seed": 11, "start": 1450.0, "drift": 0.0004, "vol": 0.014, "name": "贵州茅台"},
    "000001.SZ": {"seed": 22, "start": 11.2, "drift": 0.0002, "vol": 0.016, "name": "平安银行"},
    "601318.SH": {"seed": 33, "start": 52.0, "drift": 0.0003, "vol": 0.013, "name": "中国平安"},
    "600036.SH": {"seed": 44, "start": 39.0, "drift": 0.0005, "vol": 0.015, "name": "招商银行"},
    "000858.SZ": {"seed": 55, "start": 128.0, "drift": -0.0001, "vol": 0.018, "name": "五粮液"},
}

START, END = date(2026, 3, 2), date(2026, 9, 30)
BREAKOUT_DAY = date(2026, 8, 10)  # 600036.SH gets a clean technical breakout
SUSPEND_DAY = date(2026, 7, 6)  # 000858.SZ suspended for one day


def trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            days.append(d)
        d += timedelta(days=1)
    return days


def gen_bars() -> None:
    out_dir = FIXTURE_ROOT / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    days = trading_days(START, END)
    for inst, cfg in UNIVERSE.items():
        rng = random.Random(cfg["seed"])
        price = cfg["start"]
        rows = []
        prev_close = None
        for d in days:
            if inst == "000858.SZ" and d == SUSPEND_DAY:
                continue  # suspension: calendar day exists, bar absent
            if inst == "600036.SH" and d >= BREAKOUT_DAY:
                drift = cfg["drift"] + 0.002  # sustained up-leg for the demo
            else:
                drift = cfg["drift"]
            ret = rng.gauss(drift, cfg["vol"])
            close = max(1.0, price * (1 + ret))
            open_ = price
            high = max(open_, close) * (1 + abs(rng.gauss(0, cfg["vol"] / 2)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, cfg["vol"] / 2)))
            base_vol = 8_000_000 if inst.startswith("6") else 25_000_000
            volume = int(base_vol * (1 + 2.5 * abs(ret) / cfg["vol"]) * rng.uniform(0.7, 1.3))
            amount = volume * (open_ + close) / 2

            def q(x: float) -> str:
                return str(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            rows.append({
                "trade_date": d.isoformat(),
                "open": q(open_), "high": q(high), "low": q(low), "close": q(close),
                "pre_close": q(prev_close) if prev_close else "",
                "volume": str(volume),
                "amount": str(Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                "suspended": "0",
            })
            prev_close = close
            price = close
        with open(out_dir / f"{inst}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def gen_calendar() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_ROOT / "calendar_xshg.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"])
        for d in trading_days(date(2026, 1, 1), date(2026, 12, 31)):
            w.writerow([d.isoformat()])


def gen_news() -> None:
    # The one pre-breakout signal + one far-future item (must stay invisible to replay)
    news = [
        {
            "news_id": "news-2026-0807-001",
            "instrument_ids": ["600036.SH"],
            "title": "招商银行公布中期分红方案，机构调研明显升温",
            "body": "公司公告中期分红方案并披露调研纪要，多家机构上调关注评级。本条为演示用虚构新闻，仅用于流水线验证，不构成任何事实。",
            "event_time": "2026-08-07T21:30:00+08:00",
            "published_at": "2026-08-07T22:10:00+08:00",
            "available_at": "2026-08-07T22:15:00+08:00",
            "source_uri": "fixture://news/news-2026-0807-001",
        },
        {
            "news_id": "news-2026-0701-001",
            "instrument_ids": ["000858.SZ"],
            "title": "五粮液宣布停牌一天",
            "body": "公司股票于2026年7月6日停牌一天。演示停牌处理。",
            "event_time": "2026-07-03T18:00:00+08:00",
            "published_at": "2026-07-03T18:30:00+08:00",
            "available_at": "2026-07-03T18:31:00+08:00",
            "source_uri": "fixture://news/news-2026-0701-001",
        },
        {
            "news_id": "news-future-9999",
            "instrument_ids": ["600519.SH"],
            "title": "[未来] 该新闻 available_at 在回放窗口之后",
            "body": "回放中不可见。",
            "event_time": "2026-10-20T10:00:00+08:00",
            "published_at": "2026-10-20T10:30:00+08:00",
            "available_at": "2026-10-20T10:35:00+08:00",
            "source_uri": "fixture://news/news-future-9999",
        },
        {
            "news_id": "news-unknown-pubtime",
            "instrument_ids": ["601318.SH"],
            "title": "行业政策讨论（公开时间不详）",
            "body": "该记录公开时间未知，published_at 为空且有 missing_reason。",
            "event_time": "2026-06-10T09:00:00+08:00",
            "published_at": None,
            "available_at": "2026-06-11T09:00:00+08:00",
            "source_uri": "fixture://news/news-unknown-pubtime",
            "missing_reason": "fixture source does not disclose publish time",
        },
    ]
    p = FIXTURE_ROOT / "news" / "news.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")


def gen_fundamentals() -> None:
    recs = [
        {
            "instrument_id": "600519.SH", "report_period": "2026H1",
            "announce_date": "2026-07-28",
            "available_at": "2026-07-28T20:00:00+08:00",
            "metrics": {"net_profit_yi": "760.2", "roe": "16.8", "yoy_profit_growth": "0.091"},
            "revision": 1, "provider": "fixture",
        },
        {
            "instrument_id": "600519.SH", "report_period": "2026H1",
            "announce_date": "2026-08-20",
            "available_at": "2026-08-20T19:00:00+08:00",
            "metrics": {"net_profit_yi": "761.5", "roe": "16.9", "yoy_profit_growth": "0.093"},
            "revision": 2, "supersedes_key": "600519.SH:2026H1:1", "provider": "fixture",
        },
        {
            "instrument_id": "600036.SH", "report_period": "2026H1",
            "announce_date": "2026-08-05",
            "available_at": "2026-08-05T19:30:00+08:00",
            "metrics": {"net_profit_yi": "720.4", "roe": "11.2", "yoy_profit_growth": "0.042"},
            "revision": 1, "provider": "fixture",
        },
    ]
    p = FIXTURE_ROOT / "fundamentals" / "fundamentals.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    gen_calendar()
    gen_bars()
    gen_news()
    gen_fundamentals()
    print(f"fixtures written to {FIXTURE_ROOT}")


if __name__ == "__main__":
    main()
