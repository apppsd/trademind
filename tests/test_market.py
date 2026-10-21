"""TASK-08 market data tests: availability semantics, corrections,
suspension, timezone, cross-source normalization."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.errors import CalendarCoverageError, ConfigError, DataUnavailable
from trademind.market.adapters.fixture import FixtureAdapter
from trademind.market.base import BEIJING, Bar, bar_close_time
from trademind.market.calendar import TradingCalendar
from trademind.market.calendar_update import merge_official_calendar, parse_calendar_csv
from trademind.market.quality import normalize_bars
from trademind.market.router import DatasetRouter

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _bar(d: date, close: str, revision: int = 1, provider: str = "p1", pre: str | None = None) -> Bar:
    t = bar_close_time(d)
    return Bar(instrument_id="600519.SH", trade_date=d, open="100", high="110", low="99",
               close=close, pre_close=pre, volume="1000", amount="105000",
               provider=provider, source_time=t, fetched_at=t + timedelta(minutes=30),
               available_at=t + timedelta(minutes=30), revision=revision)


def test_fixture_bars_have_availability_and_provider():
    a = FixtureAdapter()
    bars = a.get_daily_bars("600519.SH", date(2026, 3, 2), date(2026, 3, 31))
    assert bars
    for b in bars:
        assert b.available_at.tzinfo is not None
        assert b.provider == "fixture"
        assert b.available_at >= b.source_time  # publish delay respected
        assert b.available_at.astimezone(timezone.utc).hour in (7, 8)  # 15:30 Beijing == 07:30 UTC


def test_fixture_suspension_gap_is_missing_not_zero():
    a = FixtureAdapter()
    cal = TradingCalendar.load()
    d = date(2026, 7, 6)
    assert cal.is_trading_day(d)
    bars = {b.trade_date: b for b in a.get_daily_bars("000858.SZ", date(2026, 7, 1), date(2026, 7, 10))}
    assert d not in bars  # absent, never synthesized
    assert date(2026, 7, 7) in bars


def test_correction_supersedes_only_latest_revision():
    d = date(2026, 5, 6)
    bars = [_bar(d, "100"), _bar(d, "101", revision=2, provider="p1"),
            _bar(d, "102", revision=2, provider="p0")]  # cross-provider tie
    out, rep = normalize_bars(bars)
    assert len(out) == 1
    assert out[0].provider == "p0" and out[0].revision == 2  # deterministic winner
    assert rep.superseded_corrections == 2


def test_exact_duplicates_removed():
    d = date(2026, 5, 6)
    out, rep = normalize_bars([_bar(d, "100"), _bar(d, "100")])
    assert len(out) == 1 and rep.duplicates_removed == 1


def test_limit_violation_flagged():
    d1, d2 = date(2026, 5, 6), date(2026, 5, 7)
    bars = [_bar(d1, "100", pre="100"), _bar(d2, "115", pre="100")]  # +15% > 10%
    _out, rep = normalize_bars(bars, Decimal("0.10"))
    assert not rep.ok and rep.limit_violations


def test_calendar_sessions():
    cal = TradingCalendar.load()
    # 2026-07-06 is a Monday fixture trading day
    open_dt = datetime(2026, 7, 6, 2, 0, tzinfo=timezone.utc)  # 10:00 Beijing
    lunch = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)  # 11:30 Beijing
    assert cal.is_session_open(open_dt)
    assert not cal.is_session_open(lunch)
    weekend = datetime(2026, 7, 4, 2, 0, tzinfo=timezone.utc)
    assert not cal.is_session_open(weekend)


# -- ISSUE-20261013-07: coverage bounds, out-of-bounds protection ---------------

def _small_calendar(tmp_path, days, meta=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path/'cal.csv'
    p.write_text('date\n' + '\n'.join(d.isoformat() for d in days) + '\n', encoding='utf-8')
    if meta is not None:
        p.with_suffix('.meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return TradingCalendar.load(p)


def test_issue_repro_out_of_coverage_is_unknown_not_closed(tmp_path):
    # The Issue's isolated repro: coverage ends 2026-12-31, query 2027-01-04.
    cal = _small_calendar(tmp_path, [date(2026, 12, 30), date(2026, 12, 31)])
    assert cal.coverage_end == date(2026, 12, 31)
    with pytest.raises(CalendarCoverageError):
        cal.is_trading_day(date(2027, 1, 4))
    with pytest.raises(CalendarCoverageError):
        cal.next(date(2026, 12, 31))  # never fabricates a past date as "next"
    with pytest.raises(CalendarCoverageError):
        cal.add_trading_days(date(2027, 1, 4), 1)  # never IndexError
    beyond = datetime(2027, 1, 4, 2, 0, tzinfo=timezone.utc)
    assert cal.is_session_open(beyond) is False  # unknown is never open
    assert cal.is_session_open_confirmed(beyond) is False


def test_calendar_navigation_never_clamps(tmp_path):
    cal = _small_calendar(tmp_path, [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 5)])
    assert cal.is_trading_day(date(2026, 3, 2))
    assert not cal.is_trading_day(date(2026, 3, 4))  # covered gap: a fact, not unknown
    assert cal.next(date(2026, 3, 3)) == date(2026, 3, 5)
    assert cal.prev(date(2026, 3, 5)) == date(2026, 3, 3)
    assert cal.add_trading_days(date(2026, 3, 3), 1) == date(2026, 3, 5)
    assert cal.add_trading_days(date(2026, 3, 5), -1) == date(2026, 3, 3)
    assert cal.add_trading_days(date(2026, 3, 4), 0) == date(2026, 3, 5)
    with pytest.raises(CalendarCoverageError):
        cal.prev(date(2026, 3, 2), 1)  # no covered day before the first
    with pytest.raises(CalendarCoverageError):
        cal.prev(date(2026, 3, 5), 3)  # insufficient covered depth, never clamps to first day
    with pytest.raises(CalendarCoverageError):
        cal.next(date(2026, 3, 5), 1)
    with pytest.raises(CalendarCoverageError):
        cal.add_trading_days(date(2026, 3, 2), 5)
    with pytest.raises(CalendarCoverageError):
        cal.is_trading_day(date(2026, 3, 1))  # before coverage
    with pytest.raises(CalendarCoverageError):
        cal.last_on_or_before(datetime(2026, 3, 1, tzinfo=BEIJING))
    with pytest.raises(CalendarCoverageError):
        cal.last_on_or_before(datetime(2027, 1, 4, tzinfo=BEIJING))  # after coverage
    assert cal.last_on_or_before(datetime(2026, 3, 4, tzinfo=BEIJING)) == date(2026, 3, 3)


def test_calendar_coverage_metadata_and_warnings(tmp_path):
    cal = _small_calendar(tmp_path, [date(2026, 10, 12), date(2026, 10, 13)],
                          meta={'source': 'SSE official 2026 announcement',
                                'version': '2026-official',
                                'checked_at': '2026-10-01T00:00:00+00:00'})
    cov = cal.coverage()
    assert cov['source'] == 'SSE official 2026 announcement'
    assert cov['version'] == '2026-official'
    assert cov['coverage_start'] == '2026-10-12' and cov['coverage_end'] == '2026-10-13'
    assert cov['confirmed_segments'] == [['2026-10-12', '2026-10-13']]
    assert cov['unconfirmed_gaps'] == []
    # A verified calendar with ample headroom and no holes raises nothing.
    dense = [d for d in (date(2026, 1, 1) + timedelta(n)
                         for n in range(365)) if d.weekday() < 5]
    ample = TradingCalendar(dense,
                            source='SSE official 2026 announcement', version='2026-official',
                            checked_at='2026-10-01T00:00:00+00:00')
    assert ample.coverage_warnings(today=date(2026, 6, 1)) == []
    near_end = ample.coverage_warnings(today=date(2026, 12, 20))
    assert any('coverage ends' in w for w in near_end)
    beyond = cal.coverage_warnings(today=date(2026, 11, 2))
    assert any('beyond confirmed data' in w for w in beyond)
    # An unconfirmed interior gap is reported as needing official data.
    holed = TradingCalendar([date(2026, 1, 5), date(2026, 3, 2)],
                            source='x', version='v', checked_at='2026-10-01T00:00:00+00:00')
    hole_warnings = holed.coverage_warnings(today=date(2026, 1, 6))
    assert any('unconfirmed gap 2026-01-06..2026-03-01' in w for w in hole_warnings)
    assert holed.coverage()['unconfirmed_gaps'] == [['2026-01-06', '2026-03-01']]
    # Unverified provenance is reported, never silently trusted.
    bare = _small_calendar(tmp_path/'bare', [date(2026, 10, 12)])
    assert any('metadata missing' in w for w in bare.coverage_warnings(today=date(2026, 10, 10)))


def test_calendar_load_default_fixture_reports_coverage():
    cal = TradingCalendar.load()
    cov = cal.coverage()
    start, end = date.fromisoformat(cov['coverage_start']), date.fromisoformat(cov['coverage_end'])
    assert start < end
    assert cov['trading_days'] == len(cal.window(start, end))
    assert cal.source  # falls back to the fixture path when no sidecar exists


def test_parse_and_merge_official_calendar(tmp_path):
    target = tmp_path/'calendar_xshg.csv'
    target.write_text('date\n2026-12-30\n2026-12-31\n', encoding='utf-8')
    incoming = tmp_path/'official.csv'
    incoming.write_text('date\n2026-12-31\n2027-01-04\n2027-01-05\n', encoding='utf-8')
    meta = merge_official_calendar(incoming, target, source='SSE official announcement', version='v2027')
    # The official file is authoritative for the span it covers: the
    # overlapping segment is withdrawn wholesale and re-listed from it.
    assert meta['coverage_start'] == '2026-12-31' and meta['coverage_end'] == '2027-01-05'
    assert meta['trading_days'] == 3
    assert meta['source'] == 'SSE official announcement' and meta['checked_at']
    assert meta['csv_sha256']
    cal = TradingCalendar.load(target)
    assert cal.coverage_end == date(2027, 1, 5)
    assert cal.source == 'SSE official announcement' and cal.version == 'v2027'
    assert cal.checked_at == meta['checked_at']
    # Coverage end must never be pulled backwards without allow_shrink: the
    # official file re-listing only 12-31 would withdraw 01-04/01-05.
    older = tmp_path/'older.csv'
    older.write_text('date\n2026-12-31\n', encoding='utf-8')
    with pytest.raises(ConfigError):
        merge_official_calendar(older, target, source='x')
    assert TradingCalendar.load(target).coverage_end == date(2027, 1, 5)
    assert merge_official_calendar(older, tmp_path/'shrink.csv', source='x', allow_shrink=True)
    # Unparsable rows are rejected, never half-applied.
    bad = tmp_path/'bad.csv'
    bad.write_text('date\n2027-01-04\nnot-a-date\n', encoding='utf-8')
    with pytest.raises(ConfigError):
        parse_calendar_csv(bad.read_text(encoding='utf-8'), 'bad.csv')


# -- ISSUE-20261013-07 reopen: withdrawal, holes, provenance consistency -------

def test_merge_withdraws_wrong_dates_inside_official_span(tmp_path):
    """The Issue's repro: the old file wrongly lists 10-12/13 as trading; the
    official correction lists only 10-14. After the merge they are no longer
    trading days — union must not preserve corrected dates forever."""
    target = tmp_path/'cal.csv'
    target.write_text('date\n2026-10-12\n2026-10-13\n2026-10-14\n', encoding='utf-8')
    official = tmp_path/'official.csv'
    official.write_text('date\n2026-10-14\n', encoding='utf-8')
    meta = merge_official_calendar(official, target, source='SSE official')
    assert meta['replaced_days'] == 3 and meta['carried_history_days'] == 0
    cal = TradingCalendar.load(target)
    with pytest.raises(CalendarCoverageError):
        cal.is_trading_day(date(2026, 10, 13))  # withdrawn: unknown, not a closure
    assert cal.is_trading_day(date(2026, 10, 14))
    assert cal.coverage()['confirmed_segments'] == [['2026-10-14', '2026-10-14']]


def test_merge_extension_keeps_history(tmp_path):
    """A purely later official file extends the calendar without touching
    history — the annual-update path must not withdraw past years."""
    target = tmp_path/'cal.csv'
    target.write_text('date\n2025-12-29\n2025-12-30\n2025-12-31\n', encoding='utf-8')
    official = tmp_path/'official.csv'
    official.write_text('date\n2026-01-05\n2026-01-06\n', encoding='utf-8')
    meta = merge_official_calendar(official, target, source='SSE official')
    assert meta['replaced_days'] == 0 and meta['carried_history_days'] == 3
    cal = TradingCalendar.load(target)
    assert cal.is_trading_day(date(2025, 12, 30))  # history intact
    assert cal.is_trading_day(date(2026, 1, 5))
    # A 5-day New Year gap is one confirmed run — no hole is invented.
    assert cal.coverage()['confirmed_segments'] == [['2025-12-29', '2026-01-06']]
    assert cal.coverage()['unconfirmed_gaps'] == []


def test_unprovided_year_is_unknown_never_confirmed_closure(tmp_path):
    """The Issue's repro: merging 2026 rows and 2030 rows must not make the
    calendar claim 2027–2029 as confirmed closures."""
    cal = TradingCalendar([date(2026, 12, 30), date(2026, 12, 31),
                           date(2030, 1, 2), date(2030, 1, 3)])
    assert cal.covers(date(2028, 6, 1)) is False
    with pytest.raises(CalendarCoverageError):
        cal.is_trading_day(date(2028, 6, 1))  # unknown, never silently closed
    with pytest.raises(CalendarCoverageError):
        cal.next(date(2026, 12, 31))
    assert any('unconfirmed gap' in w
               for w in cal.coverage_warnings(today=date(2026, 12, 30)))


def test_merge_cross_year_gap_leaves_hole(tmp_path):
    target = tmp_path/'cal.csv'
    target.write_text('date\n2026-12-30\n2026-12-31\n', encoding='utf-8')
    official = tmp_path/'official.csv'
    official.write_text('date\n2030-01-02\n2030-01-03\n', encoding='utf-8')
    merge_official_calendar(official, target, source='SSE official')
    cal = TradingCalendar.load(target)
    assert cal.coverage_end == date(2030, 1, 3)
    assert cal.covers(date(2028, 6, 1)) is False
    assert cal.coverage()['unconfirmed_gaps'] == [['2027-01-01', '2030-01-01']]


def test_calendar_meta_hash_mismatch_detected(tmp_path):
    target = tmp_path/'cal.csv'
    target.write_text('date\n2026-10-13\n2026-10-14\n', encoding='utf-8')
    official = tmp_path/'official.csv'
    official.write_text('date\n2026-10-13\n2026-10-14\n', encoding='utf-8')
    merge_official_calendar(official, target, source='SSE official')
    # A hand-edit or a half-applied update leaves the sidecar stale; load()
    # reports the mismatch instead of silently trusting the provenance.
    target.write_text('date\n2026-10-13\n', encoding='utf-8')
    cal = TradingCalendar.load(target)
    assert cal.meta_csv_mismatch is True
    assert any('sidecar is stale' in w
               for w in cal.coverage_warnings(today=date(2026, 10, 12)))


def test_merge_meta_write_failure_leaves_detectable_state(tmp_path, monkeypatch):
    """Atomicity: if the sidecar write fails after the CSV was replaced, the
    stale metadata is detectable through its recorded hash."""
    target = tmp_path/'cal.csv'
    target.write_text('date\n2026-10-13\n2026-10-14\n', encoding='utf-8')
    official = tmp_path/'official.csv'
    official.write_text('date\n2026-10-13\n2026-10-14\n2026-10-15\n', encoding='utf-8')
    # A first successful merge leaves a consistent pair.
    merge_official_calendar(official, target, source='SSE official')
    official2 = tmp_path/'official2.csv'
    official2.write_text('date\n2026-10-14\n2026-10-15\n2026-10-16\n', encoding='utf-8')
    real_replace = Path.replace
    calls = {'n': 0}

    def failing_replace(self, target_path):
        calls['n'] += 1
        if calls['n'] == 2:  # the sidecar replace
            raise OSError('simulated crash between the two replaces')
        return real_replace(self, target_path)

    monkeypatch.setattr(Path, 'replace', failing_replace)
    with pytest.raises(OSError):
        merge_official_calendar(official2, target, source='SSE official')
    monkeypatch.undo()
    # CSV advanced, sidecar stale — detectable, never silently inconsistent.
    cal = TradingCalendar.load(target)
    assert cal.meta_csv_mismatch is True
    assert any('sidecar is stale' in w
               for w in cal.coverage_warnings(today=date(2026, 10, 12)))
    # Re-running the merge converges to a consistent pair.
    merge_official_calendar(official2, target, source='SSE official')
    cal = TradingCalendar.load(target)
    assert cal.meta_csv_mismatch is False
    assert cal.is_trading_day(date(2026, 10, 15))


def test_router_falls_back_to_fixture():
    router = DatasetRouter(pref=["fixture"])
    bars, rep = router.get_daily_bars("600519.SH", date(2026, 3, 2), date(2026, 9, 30))
    assert bars and rep.ok
    assert router.last_provider_by_dataset["daily_bars"] == "fixture"


def test_live_adapter_requires_sdk_or_token():
    with pytest.raises(DataUnavailable):
        # tushare without token refuses by policy before even importing
        from trademind.market.adapters.live import TushareAdapter

        TushareAdapter(token="")
    with pytest.raises(DataUnavailable):
        from trademind.market.adapters.live import _split

        _split("BADFORMAT")


def test_news_point_in_time():
    a = FixtureAdapter()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    all_news = []
    for inst in ("600036.SH", "000858.SZ", "600519.SH", "601318.SH"):
        all_news += a.get_news(inst, start, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert all(n.available_at <= datetime(2026, 10, 1, tzinfo=timezone.utc) for n in all_news)
    # replay before Aug 7 must not see the dividend news
    early = a.get_news("600036.SH", start, datetime(2026, 8, 7, 12, 0, tzinfo=BEIJING))
    assert all("分红" not in n.title for n in early)
