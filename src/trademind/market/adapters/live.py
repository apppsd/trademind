"""Optional live adapters. SDK imports are lazy; missing SDK or token yields
DataUnavailable, never a silent fallback to fabricated data (TASK-08/11).

All live downloads set backfill semantics: available_at is the fetch time —
these rows are NOT point-in-time for replay and are flagged accordingly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..base import BEIJING, Bar, MinuteBar, bar_close_time
from ..profiles import get_profile
from ...errors import DataUnavailable


def _require(mod_name: str):
    try:
        return __import__(mod_name)
    except Exception as exc:  # noqa: BLE001
        raise DataUnavailable(
            f"{mod_name} not installed; install it to enable this adapter "
            f"(see requirements-data.txt)"
        ) from exc


def _split(instrument_id: str) -> tuple[str, str]:
    code, _, suffix = instrument_id.partition(".")
    if not code or suffix not in ("SH", "SZ"):
        raise DataUnavailable(f"unsupported instrument id {instrument_id!r}")
    return code, suffix


class AKShareAdapter:
    name = "akshare"

    def __init__(self) -> None:
        self._ak = _require("akshare")

    def get_daily_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        import io

        import pandas as pd

        code, suffix = _split(instrument_id)
        df = self._ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        out: list[Bar] = []
        fetched = datetime.now(tz=timezone.utc)
        for _, r in df.iterrows():
            d = pd.Timestamp(r["日期"]).date()
            close_t = bar_close_time(d)
            out.append(Bar(
                instrument_id=instrument_id, trade_date=d,
                open=Decimal(str(r["开盘"])), high=Decimal(str(r["最高"])),
                low=Decimal(str(r["最低"])), close=Decimal(str(r["收盘"])),
                pre_close=None,  # qfq series: compute from previous row later
                volume=Decimal(str(r["成交量"])), amount=Decimal(str(r["成交额"])),
                provider=self.name, source_time=close_t,
                fetched_at=fetched, available_at=fetched,
            ))
        for prev, cur in zip(out, out[1:]):
            cur.pre_close = prev.close
        return out

    def get_minute_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        period: str = "1m",
    ) -> list[MinuteBar]:
        code, suffix = _split(instrument_id)
        ak_period = period.rstrip("m")
        is_etf = code.startswith(("51", "58", "15", "16"))
        if is_etf:
            # The ETF minute branch serves verified-whitelist ETFs only, the
            # same membership gate as SinaETFAdapter (TASK-20261014-03).
            try:
                profile = get_profile(instrument_id)
            except ValueError:
                raise DataUnavailable(
                    f"AKShare ETF minute requires a verified ETF: {instrument_id}") from None
            if profile.security_type != "etf":
                raise DataUnavailable(
                    f"AKShare ETF minute requires an ETF, got {profile.security_type}")
        functions = ("fund_etf_hist_min_em",) if is_etf else ("stock_zh_a_hist_min_em",)
        # Official Eastmoney ETF minute data is unadjusted (不复权); only the
        # legacy stock branch keeps qfq.
        adjust = "" if is_etf else "qfq"
        df = None
        last_error = None
        for fn_name in functions:
            fn = getattr(self._ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = fn(
                    symbol=code,
                    period=ak_period,
                    adjust=adjust,
                    start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
                )
                if df is not None and not df.empty:
                    break
            except Exception as exc:
                last_error = exc
                continue

        if df is None or df.empty:
            if last_error is not None:
                raise DataUnavailable('AKShare minute request failed: ' + type(last_error).__name__) from last_error
            return []

        if is_etf:
            return self._etf_minute_rows(instrument_id, df, start, end, period)
        return self._stock_minute_rows(instrument_id, df, start, end, period)

    #: fund_etf_hist_min_em reports 成交量 in 手 (lots of 100); MinuteBar.volume
    #: is shares/units. Interface-level evidence: official 511220 sample row
    #: 2024-03-20 09:31 volume=413, amount=420929 CNY, close=10.192 ->
    #: 420929/(413*100) == 10.1920 ≈ close. See
    #: docs/research/20261014-etf-minute-unit-verification.md (TASK-20261014-03).
    _ETF_MINUTE_LOT = Decimal("100")

    def _etf_minute_rows(
        self, instrument_id: str, df, start: datetime, end: datetime, period: str,
    ) -> list[MinuteBar]:
        fetched = datetime.now(tz=timezone.utc)
        # A bar is usable only once its minute has closed at the source; the
        # in-progress minute and any future-stamped row are excluded.
        closed_before = fetched.astimezone(BEIJING).replace(second=0, microsecond=0)
        out: list[MinuteBar] = []
        for _, r in df.iterrows():
            try:
                dt = datetime.fromisoformat(str(r["时间"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=BEIJING)
                o, h, l, c = (Decimal(str(r[k])) for k in ("开盘", "最高", "最低", "收盘"))
                raw_volume, amount = Decimal(str(r["成交量"])), Decimal(str(r["成交额"]))
                if not all(v.is_finite() for v in (o, h, l, c, raw_volume, amount)):
                    raise ValueError("non-finite field")
                if min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c):
                    raise ValueError("invalid OHLC")
                if raw_volume < 0 or amount < 0:
                    raise ValueError("negative quantity")
                volume = raw_volume * self._ETF_MINUTE_LOT  # 手 -> shares/units
                if volume == 0:
                    if amount != 0:
                        raise ValueError("zero volume with nonzero amount")
                elif not l * Decimal("0.98") <= amount / volume <= h * Decimal("1.02"):
                    # 成交额/份额 must fall inside the bar's price range; a unit
                    # change at the source (e.g. volume already in shares) breaks
                    # this and is rejected instead of silently mis-scaled.
                    raise ValueError("amount/volume inconsistent with price range")
            except (KeyError, ValueError, TypeError, ArithmeticError):
                raise DataUnavailable("AKShare ETF minute invalid fields or quantity units") from None
            if start <= dt <= end and dt < closed_before:
                out.append(
                    MinuteBar(
                        instrument_id=instrument_id,
                        trade_date=dt.date(),
                        bar_time=dt,
                        open=o,
                        high=h,
                        low=l,
                        close=c,
                        volume=volume,
                        amount=amount,
                        provider=self.name,
                        source_time=dt,
                        fetched_at=fetched,
                        available_at=fetched,
                        period=period,  # type: ignore[arg-type]
                    )
                )
        return out

    @staticmethod
    def _stock_minute_rows(
        instrument_id: str, df, start: datetime, end: datetime, period: str,
    ) -> list[MinuteBar]:
        """Legacy stock passthrough; semantics deliberately unchanged."""
        out: list[MinuteBar] = []
        fetched = datetime.now(tz=timezone.utc)
        for _, r in df.iterrows():
            ts_str = str(r.get("时间") or r.get("datetime"))
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BEIJING)
            if not (start <= dt <= end):
                continue
            out.append(
                MinuteBar(
                    instrument_id=instrument_id,
                    trade_date=dt.date(),
                    bar_time=dt,
                    open=Decimal(str(r["开盘"])),
                    high=Decimal(str(r["最高"])),
                    low=Decimal(str(r["最低"])),
                    close=Decimal(str(r["收盘"])),
                    volume=Decimal(str(r["成交量"])),
                    amount=Decimal(str(r["成交额"])),
                    provider="akshare",
                    source_time=dt,
                    fetched_at=fetched,
                    available_at=fetched,
                    period=period,  # type: ignore[arg-type]
                )
            )
        return out

    def get_calendar(self, start: date, end: date) -> list[date]:
        import pandas as pd

        df = self._ak.tool_trade_date_hist_sina()
        days = {pd.Timestamp(d).date() for d in df["trade_date"]}
        return sorted(d for d in days if start <= d <= end)

    def capabilities(self) -> dict[str, bool]:
        return {
            "daily_bars": True,
            "minute_bars": True,
            "calendar": True,
            "quote": True,
            "news": True,
            "fundamentals": True,
        }


class BaoStockAdapter:
    name = "baostock"

    def __init__(self) -> None:
        self._bs = _require("baostock")

    @staticmethod
    def _bs_code(instrument_id: str) -> str:
        code, suffix = _split(instrument_id)
        return f"sh.{code}" if suffix == "SH" else f"sz.{code}"

    def get_daily_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        bs = self._bs
        if not getattr(bs, "_login_state", False):
            bs.login()
        rs = bs.query_history_k_data_plus(
            self._bs_code(instrument_id),
            "date,open,high,low,close,volume,amount,tradestatus",
            start_date=start.isoformat(), end_date=end.isoformat(),
            frequency="d", adjustflag="2",
        )
        out: list[Bar] = []
        fetched = datetime.now(tz=timezone.utc)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        for prev_close, row in zip([None] + rows[:-1], rows):
            (d_s, o, h, l, c, v, amt, status) = row
            if status and status != "1":
                continue  # suspended day: calendar gap, recorded as missing
            d = date.fromisoformat(d_s)
            close_t = bar_close_time(d)
            out.append(Bar(
                instrument_id=instrument_id, trade_date=d,
                open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
                pre_close=Decimal(prev_close[4]) if prev_close else None,
                volume=Decimal(v or 0), amount=Decimal(amt or 0),
                provider=self.name, source_time=close_t,
                fetched_at=fetched, available_at=fetched,
            ))
        return out

    def get_calendar(self, start: date, end: date) -> list[date]:
        bs = self._bs
        if not getattr(bs, "_login_state", False):
            bs.login()
        rs = bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
        days = []
        while rs.error_code == "0" and rs.next():
            d_s, flag = rs.get_row_data()
            if flag == "1":
                days.append(date.fromisoformat(d_s))
        return days

    def capabilities(self) -> dict[str, bool]:
        return {"daily_bars": True, "calendar": True, "quote": False,
                "news": False, "fundamentals": True}


class TushareAdapter:
    """Token-gated; only free-tier endpoints; no auto purchase (ISSUE route)."""

    name = "tushare"

    def __init__(self, token: str) -> None:
        if not token:
            raise DataUnavailable("tushare token missing; adapter disabled by policy")
        ts = _require("tushare")
        self._pro = ts.pro_api(token)

    @staticmethod
    def _ts_code(instrument_id: str) -> str:
        code, suffix = _split(instrument_id)
        return f"{code}.{suffix}"

    def get_daily_bars(self, instrument_id: str, start: date, end: date) -> list[Bar]:
        df = self._pro.daily(ts_code=self._ts_code(instrument_id),
                             start_date=start.strftime("%Y%m%d"),
                             end_date=end.strftime("%Y%m%d"))
        out: list[Bar] = []
        fetched = datetime.now(tz=timezone.utc)
        for _, r in df.sort_values("trade_date").iterrows():
            d = datetime.strptime(str(r["trade_date"]), "%Y%m%d").date()
            close_t = bar_close_time(d)
            out.append(Bar(
                instrument_id=instrument_id, trade_date=d,
                open=Decimal(str(r["open"])), high=Decimal(str(r["high"])),
                low=Decimal(str(r["low"])), close=Decimal(str(r["close"])),
                pre_close=Decimal(str(r["pre_close"])),
                volume=Decimal(str(r["vol"] or 0)) * 100,  # tushare 手 -> shares
                amount=Decimal(str(r["amount"] or 0)) * 1000,  # 千元 -> 元
                provider=self.name, source_time=close_t,
                fetched_at=fetched, available_at=fetched,
            ))
        return out

    def get_calendar(self, start: date, end: date) -> list[date]:
        df = self._pro.trade_cal(exchange="SSE",
                                 start_date=start.strftime("%Y%m%d"),
                                 end_date=end.strftime("%Y%m%d"))
        days = [datetime.strptime(str(d), "%Y%m%d").date()
                for d, op in zip(df["cal_date"], df["is_open"]) if str(op) == "1"]
        return sorted(days)

    def capabilities(self) -> dict[str, bool]:
        return {"daily_bars": True, "calendar": True, "quote": False,
                "news": False, "fundamentals": True}
