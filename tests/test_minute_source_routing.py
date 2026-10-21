"""TASK-20261014-04 / ISSUE-20261014-01: quality-driven multi-source minute
routing — the four observed counterexamples, bounded waits, backoff/limits,
cache re-validation and isolation, sparse cross-source observation."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trademind.market.base import BEIJING, MinuteBar
from trademind.market.router import DatasetRouter, MinuteRoutingPolicy

IID = "513050.SH"


def bar(offset_min: int, close: str = "1.500", volume: str = "1000",
        at: datetime | None = None, available_in: float = 1.0,
        provider: str = "p") -> MinuteBar:
    base = at or (datetime(2026, 10, 8, 9, 31, tzinfo=BEIJING) + timedelta(minutes=offset_min))
    c = Decimal(close)
    fetched = base + timedelta(seconds=available_in)
    return MinuteBar(
        instrument_id=IID, trade_date=base.date(), bar_time=base,
        open=c, high=c + Decimal("0.01"), low=c - Decimal("0.01"), close=c,
        volume=Decimal(volume), amount=c * Decimal(volume), provider=provider,
        source_time=base, fetched_at=fetched, available_at=fetched, period="1m")


def fresh_window(provider: str, end: datetime, count: int = 8) -> list[MinuteBar]:
    return [bar(i, provider=provider, at=end - timedelta(minutes=count - 1 - i)) for i in range(count)]


class FakeSource:
    def __init__(self, name, bars=None, error=None, delay=0.0, minute_capable=True):
        self.name, self.bars, self.error, self.delay = name, bars, error, delay
        self.minute_capable = minute_capable
        self.calls = 0

    def get_daily_bars(self, *a, **k):
        return []

    def get_calendar(self, *a, **k):
        return []

    def capabilities(self):
        return {"minute_bars": self.minute_capable, "daily_bars": False}

    def get_minute_bars(self, instrument_id, start, end, period="1m"):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return [b for b in (self.bars or []) if start <= b.bar_time <= end]


class BlockingSource(FakeSource):
    """Controllable SDK stand-in: no network, released explicitly by the test."""
    def __init__(self, name, gate: threading.Event, bars=None):
        super().__init__(name, bars=bars)
        self.gate = gate
        self.entered = threading.Event()

    def get_minute_bars(self, instrument_id, start, end, period="1m"):
        self.calls += 1
        self.entered.set()
        self.gate.wait()
        return [b for b in (self.bars or []) if start <= b.bar_time <= end]


def policy(**kw) -> MinuteRoutingPolicy:
    base = dict(per_source_timeout_seconds=5.0, round_timeout_seconds=15.0,
                failure_backoff_seconds=0.0, daily_request_limit=1000)
    base.update(kw)
    return MinuteRoutingPolicy(**base)


def router_with(*sources, pol=None) -> tuple[DatasetRouter, list[FakeSource]]:
    router = DatasetRouter(pref=["fixture"], minute_policy=pol or policy())
    router._factories = {s.name: (lambda s=s: s) for s in sources}
    return router, list(sources)


class FakeClock:
    """Controllable monotonic clock. Observation-cadence tests must not depend
    on how long this process or the machine has been up (ISSUE-20261015-01)."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


NOW = datetime(2026, 10, 8, 9, 40, tzinfo=BEIJING)
WIN = (NOW - timedelta(days=2), NOW)


# -- the four observed counterexamples (ISSUE-20261014-01) ----------------------

def test_empty_from_capable_primary_falls_back():
    bad, good = FakeSource("bad", bars=[]), FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(bad, good)
    bars, report = router.get_minute_bars(IID, *WIN, pref=["bad", "good"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert bad.calls == 1 and good.calls == 1
    assert bars and report.selected_provider == "good"
    assert router.last_provider_by_dataset["minute_bars_1m"] == "good"
    statuses = [(e["provider"], e["status"]) for e in router.minute_source_log[IID]]
    assert ("bad", "rejected") in statuses and ("good", "selected") in statuses


def test_invalid_ohlc_primary_falls_back():
    bad = FakeSource("bad", bars=[bar(0, close="-1.0"), bar(1, close="1.5")])
    good = FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(bad, good)
    bars, report = router.get_minute_bars(IID, *WIN, pref=["bad", "good"])
    assert report.selected_provider == "good"
    assert all(b.close > 0 for b in bars)
    assert any("bad" in r for r in report.source_rejections)


def test_future_visible_primary_falls_back():
    late = NOW + timedelta(seconds=30)  # everything becomes visible after as_of
    bad = FakeSource("bad", bars=[bar(i, at=NOW - timedelta(minutes=5 - i),
                                      available_in=(late - (NOW - timedelta(minutes=5 - i))).total_seconds())
                                 for i in range(5)])
    good = FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(bad, good)
    bars, report = router.get_minute_bars(IID, *WIN, pref=["bad", "good"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert report.selected_provider == "good" and bars


def test_stale_primary_falls_back():
    stale = FakeSource("stale", bars=fresh_window("stale", NOW - timedelta(minutes=15)))
    good = FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(stale, good)
    bars, report = router.get_minute_bars(IID, *WIN, pref=["stale", "good"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert report.selected_provider == "good"
    assert (NOW - bars[-1].bar_time).total_seconds() <= 180


def test_all_stale_returns_report_instead_of_raise():
    a = FakeSource("a", bars=fresh_window("a", NOW - timedelta(minutes=15)))
    b = FakeSource("b", bars=fresh_window("b", NOW - timedelta(minutes=10)))
    router, _ = router_with(a, b)
    bars, report = router.get_minute_bars(IID, *WIN, pref=["a", "b"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert bars  # least-bad window returned for downstream gates to reject
    assert report.source_rejections and len(report.source_rejections) == 2
    assert "stale" in report.source_rejections[0]


# -- failure terminals and bounded waits ----------------------------------------

def test_all_sources_raising_runtime_error():
    a, b = FakeSource("a", error=OSError("down")), FakeSource("b", error=OSError("down"))
    router, _ = router_with(a, b)
    with pytest.raises(RuntimeError):
        router.get_minute_bars(IID, *WIN, pref=["a", "b"])
    assert a.calls == 1 and b.calls == 1


def test_slow_source_bounded_by_per_source_timeout():
    slow, fast = FakeSource("slow", bars=fresh_window("slow", NOW), delay=1.5), FakeSource("fast", bars=fresh_window("fast", NOW))
    router, _ = router_with(slow, fast, pol=policy(per_source_timeout_seconds=0.3))
    started = time.monotonic()
    bars, report = router.get_minute_bars(IID, *WIN, pref=["slow", "fast"])
    elapsed = time.monotonic() - started
    assert elapsed < 1.2  # bounded wait, not the source's full 1.5s
    assert report.selected_provider == "fast" and slow.calls == 1


def test_round_deadline_bounds_total_wait():
    s1 = FakeSource("s1", bars=fresh_window("s1", NOW), delay=1.0)
    s2 = FakeSource("s2", bars=fresh_window("s2", NOW), delay=1.0)
    router, _ = router_with(s1, s2, pol=policy(round_timeout_seconds=0.4, per_source_timeout_seconds=2.0))
    started = time.monotonic()
    with pytest.raises(RuntimeError) as excinfo:
        router.get_minute_bars(IID, *WIN, pref=["s1", "s2"])
    elapsed = time.monotonic() - started
    assert elapsed < 1.5  # one clamped wait, then the round budget stops
    assert s1.calls == 1 and s2.calls == 0  # budget exhausted: second source never tried
    assert "did not answer within 0.4s" in str(excinfo.value)


# -- backoff, recovery probe, daily counts ---------------------------------------

def test_backoff_skips_then_recovery_probe_retries():
    bad, good = FakeSource("bad", error=OSError("flap")), FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(bad, good, pol=policy(failure_backoff_seconds=0.25))
    router.get_minute_bars(IID, *WIN, pref=["bad", "good"])
    assert bad.calls == 1
    router._minute_cache.clear()  # isolate the health/backoff layer from the cache
    router.get_minute_bars(IID, *WIN, pref=["bad", "good"])  # bad in backoff, skipped
    assert bad.calls == 1 and good.calls == 2
    time.sleep(0.3)  # backoff expired: bounded recovery probe
    router._minute_cache.clear()
    router.get_minute_bars(IID, *WIN, pref=["bad", "good"])
    assert bad.calls == 2  # probed again, failed again, good still serves


def test_daily_request_limit_stops_source():
    good = FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(good, pol=policy(daily_request_limit=2))
    router.get_minute_bars(IID, *WIN, pref=["good"])
    router._minute_cache.clear()
    router.get_minute_bars(IID, *WIN, pref=["good"])
    router._minute_cache.clear()
    with pytest.raises(RuntimeError) as excinfo:
        router.get_minute_bars(IID, *WIN, pref=["good"])
    assert good.calls == 2
    assert "daily request limit reached" in str(excinfo.value)


# -- cache re-validation and isolation -------------------------------------------

def test_stale_cache_falls_through_to_refetch():
    src = FakeSource("src", bars=fresh_window("src", NOW))
    router, _ = router_with(src)
    win = (NOW - timedelta(days=2), NOW)
    bars, _ = router.get_minute_bars(IID, *win, pref=["src"], as_of=NOW, quote_max_age_seconds=180)
    assert bars and src.calls == 1  # fresh window cached
    later = NOW + timedelta(minutes=5)
    bars, report = router.get_minute_bars(IID, *win, pref=["src"], as_of=later, quote_max_age_seconds=180)
    assert src.calls == 2  # cached bars are 5min old vs the new as_of: refetch, never block
    assert report.source_rejections  # refetched data is stale too: least-bad + rejections


def test_cache_hit_within_freshness_is_used():
    src = FakeSource("src", bars=fresh_window("src", NOW))
    router, _ = router_with(src)
    win = (NOW - timedelta(days=2), NOW)
    first, _ = router.get_minute_bars(IID, *win, pref=["src"], as_of=NOW, quote_max_age_seconds=180)
    second, report = router.get_minute_bars(IID, *win, pref=["src"], as_of=NOW, quote_max_age_seconds=180)
    assert src.calls == 1  # served from cache
    assert report.selected_provider == "src"
    assert [b.bar_time for b in second] == [b.bar_time for b in first]


def test_cache_isolated_by_pref():
    a, b = FakeSource("a", bars=fresh_window("a", NOW)), FakeSource("b", bars=fresh_window("b", NOW))
    router, _ = router_with(a, b)
    win = (NOW - timedelta(days=2), NOW)
    router.get_minute_bars(IID, *win, pref=["a"])
    router.get_minute_bars(IID, *win, pref=["b"])
    assert a.calls == 1 and b.calls == 1  # different pref tuples never share cache


# -- sparse cross-source observation ---------------------------------------------

def test_cross_check_divergence_refuses_and_reports():
    good_t = [bar(i, close="1.500", provider="primary", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    skewed_t = [bar(i, close="1.600", provider="other", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    primary, other = FakeSource("primary", bars=good_t), FakeSource("other", bars=skewed_t)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    # symmetric sparse observation: each qualified candidate is cross-checked
    # against the first other source (each source: 1 candidate + 1 observer call)
    assert primary.calls == 2 and other.calls == 2
    assert any("cross-source divergence" in v for v in report.limit_violations)
    assert not report.ok
    bars2, _ = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    # divergent window was never cached: round 2 refetches both roles again
    assert (primary.calls, other.calls) == (4, 4)


def test_cross_check_off_by_default_adds_no_requests():
    primary, other = FakeSource("primary", bars=fresh_window("primary", NOW)), FakeSource("other", bars=fresh_window("other", NOW))
    router, _ = router_with(primary, other)
    router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert other.calls == 0


def test_cross_check_sparse_interval_and_agreement():
    same = lambda name: FakeSource(name, bars=[bar(i, close="1.500", provider=name,
                                                  at=NOW - timedelta(minutes=8 - i)) for i in range(8)])
    primary, other = same("primary"), same("other")
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=3600.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    clock = FakeClock(10.0)  # "process up" 10s: below the 3600s interval
    router._clock = clock
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert report.ok  # agreement within tolerance
    assert other.calls == 1  # first observation runs: "never observed" is not a zero timestamp
    clock.advance(60.0)
    router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert other.calls == 1  # interval not elapsed: no extra observation
    clock.advance(3601.0)
    router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert other.calls == 2  # interval elapsed: observed again


def test_cross_check_divergence_refusal_survives_pref_and_rerequests():
    # ISSUE-20261015-01 gap 1: under a non-zero interval the backup candidate
    # is not re-observed — it must still carry the refusal, never be cached,
    # and re-requests must not launder the divergence either.
    good_t = [bar(i, close="1.500", provider="primary", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    skewed_t = [bar(i, close="1.600", provider="other", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    primary, other = FakeSource("primary", bars=good_t), FakeSource("other", bars=skewed_t)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=900.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    clock = FakeClock(1000.0)
    router._clock = clock
    key = (IID, "1m", ("primary", "other"))
    # round 1: first observation diverges; the backup qualifies but is refused
    # on the standing note without a second observation
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (1, 2)  # other: 1 observer + 1 candidate
    assert any("cross-source divergence" in v for v in report.limit_violations)
    assert not report.ok
    assert key not in router._minute_cache  # divergent round cached nothing
    # round 2 (re-request): interval not elapsed, no new observation, still refused
    clock.advance(30.0)
    bars2, report2 = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (2, 3)  # exactly one candidate fetch each
    assert any("cross-source divergence" in v for v in report2.limit_violations)
    assert not report2.ok
    assert key not in router._minute_cache
    # recovery: after the interval an agreeing observation lifts the refusal
    other.bars = [bar(i, close="1.500", provider="other", at=NOW - timedelta(minutes=8 - i))
                  for i in range(8)]
    clock.advance(901.0)
    bars3, report3 = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (3, 4)  # candidate + fresh observer call
    assert report3.ok and report3.selected_provider == "primary"
    assert router._divergence.get(IID) is None
    assert key in router._minute_cache  # only the agreeing window is cached


def test_cross_check_daily_cap_and_day_switch_do_not_launder_divergence():
    good_t = [bar(i, close="1.500", provider="primary", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    skewed_t = [bar(i, close="1.600", provider="other", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    primary, other = FakeSource("primary", bars=good_t), FakeSource("other", bars=skewed_t)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=1, cross_check_tolerance_ratio="0.001"))
    router.get_minute_bars(IID, *WIN, pref=["primary", "other"])  # consumes the daily cap
    assert (primary.calls, other.calls) == (1, 2)  # other: 1 observer + 1 candidate
    router._minute_cache.clear()
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (2, 3)  # cap spent: no new observation
    assert any("cross-source divergence" in v for v in report.limit_violations)
    assert not report.ok  # refused on the standing note, not on a fresh comparison
    # Beijing-day switch resets the daily counter, so the (still divergent)
    # observation runs again — the refusal is only lifted by agreement, not by
    # the day rolling over
    router._beijing_day = lambda: datetime(2026, 10, 9, tzinfo=BEIJING).date()
    router._minute_cache.clear()
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (3, 5)  # observation ran again on the new day
    assert not report.ok and router._divergence.get(IID) is not None


def test_cross_check_observer_waits_within_round_budget():
    # ISSUE-20261015-01 gap 2: the observation shares the round deadline — its
    # wait is clamped to the remaining budget instead of the full per-source
    # timeout (previously ~0.30s total; the round must stay near its 0.1s cap).
    primary = FakeSource("primary", bars=fresh_window("primary", NOW), delay=0.02)
    other = FakeSource("other", bars=fresh_window("other", NOW), delay=0.5)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, round_timeout_seconds=0.1,
        per_source_timeout_seconds=0.25))
    started = time.monotonic()
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert report.ok and report.selected_provider == "primary"
    assert other.calls == 1  # request was started, then bounded-abandoned
    assert router._cross_check["last"]["status"] == "error"
    assert router._source_health["other"]["failed_at"] is None  # no backoff poisoning
    router._minute_cache.clear()
    _, report2 = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert report2.ok and report2.selected_provider == "primary"  # later routing unaffected


def test_cross_check_skips_observation_when_round_budget_spent():
    primary, other = FakeSource("primary", bars=fresh_window("primary", NOW)), \
        FakeSource("other", bars=fresh_window("other", NOW))
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0, cross_check_max_daily=5))
    notes = router._maybe_cross_check(
        "primary", fresh_window("primary", NOW), IID, *WIN, period="1m",
        deadline=time.monotonic() - 1.0)  # the round budget is already spent
    assert notes == []
    assert other.calls == 0  # no out-of-budget request was started
    assert router._cross_check["last"]["status"] == "skipped"
    assert "budget" in router._cross_check["last"]["reason"]
    assert router._cross_check["count"] == 0  # nothing consumed: no observation ran
    assert router._cross_check["last_mono"] is None


def test_cross_check_first_observation_independent_of_monotonic_value():
    # ISSUE-20261015-01 gap 3 (audit repro): last_mono was initialised to 0.0,
    # so on a machine up less than the interval the first observation was
    # silently skipped. "Never observed" must always observe.
    same = lambda name: FakeSource(name, bars=[bar(i, close="1.500", provider=name,
                                                  at=NOW - timedelta(minutes=8 - i)) for i in range(8)])
    primary, other = same("primary"), same("other")
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=900.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    router._clock = lambda: 10.0  # monotonic value far below the interval
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"])
    assert other.calls == 1
    assert report.ok and report.selected_provider == "primary"


def test_cross_check_standing_refusal_not_bypassed_via_cache():
    # Acceptance 2026-10-15 (TASK-20261015-03): same-pref narrow -> wide ->
    # narrow. The narrow window was cached while sources agreed; the wide
    # window then diverged and set the standing refusal; the still-warm narrow
    # cache entry must not serve a clean report past that refusal — the
    # re-request must re-fetch and re-observe instead.
    agree = [bar(i, close="1.500", provider="p", at=NOW - timedelta(minutes=16 - i))
             for i in range(8)]
    primary_t = agree + [bar(8 + i, close="1.500", provider="primary",
                             at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    skewed_t = agree + [bar(8 + i, close="1.600", provider="other",
                            at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    primary, other = FakeSource("primary", bars=primary_t), FakeSource("other", bars=skewed_t)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=10, cross_check_tolerance_ratio="0.001"))
    router._clock = FakeClock(1000.0)
    narrow = (NOW - timedelta(days=2), NOW - timedelta(minutes=9))
    wide = (NOW - timedelta(days=2), NOW)
    # 1: narrow window agrees -> selected and cached
    bars1, report1 = router.get_minute_bars(IID, *narrow, pref=["primary", "other"])
    assert report1.ok and (primary.calls, other.calls) == (1, 1)
    # 2: wide window diverges on the extra bars -> standing refusal, not cached
    bars2, report2 = router.get_minute_bars(IID, *wide, pref=["primary", "other"])
    assert not report2.ok
    assert router._divergence.get(IID) is not None
    calls_before = (primary.calls, other.calls)  # [3, 3] in the acceptance repro
    assert calls_before == (3, 3)
    # 3: the warm narrow cache must not launder the refusal
    bars3, report3 = router.get_minute_bars(IID, *narrow, pref=["primary", "other"])
    assert (primary.calls, other.calls) == (4, 4)  # re-fetched, not served from cache
    # the clean result is backed by a fresh agreeing observation, not by the
    # pre-divergence cache entry
    assert report3.ok and router._divergence.get(IID) is None


def test_cross_check_future_visible_observer_not_a_comparison_basis():
    # ISSUE-20261015-01 gap 4: observer rows invisible at as_of must not enter
    # the divergence math (previously they produced a ~6.7% "divergence").
    good_t = [bar(i, close="1.500", provider="primary", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    future_t = [bar(i, close="1.900", provider="other", at=NOW - timedelta(minutes=8 - i),
                    available_in=3600.0) for i in range(8)]
    primary, other = FakeSource("primary", bars=good_t), FakeSource("other", bars=future_t)
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert report.ok and report.selected_provider == "primary"  # no phantom divergence
    assert other.calls == 1
    assert router._cross_check["last"]["status"] == "unqualified"
    assert router._divergence.get(IID) is None  # invisible data manufactured no refusal
    router._minute_cache.clear()
    bars2, report2 = router.get_minute_bars(IID, *WIN, pref=["primary", "other"],
                                            as_of=NOW, quote_max_age_seconds=180)
    assert report2.ok  # and the unqualified observation started no standing refusal


def test_cross_check_stale_and_invalid_observers_not_a_comparison_basis():
    good_t = [bar(i, close="1.500", provider="primary", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    skewed_t = [bar(i, close="1.900", provider="other", at=NOW - timedelta(minutes=8 - i)) for i in range(8)]
    # stale observer: visible and structurally fine, but older than the caller's
    # freshness policy — recorded, not compared
    stale = FakeSource("other", bars=[bar(i, close="1.900", provider="other",
                                          at=NOW - timedelta(minutes=15 - i)) for i in range(8)])
    router, _ = router_with(FakeSource("primary", bars=good_t), stale, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    bars, report = router.get_minute_bars(IID, *WIN, pref=["primary", "other"],
                                          as_of=NOW, quote_max_age_seconds=180)
    assert report.ok and report.selected_provider == "primary"
    assert router._cross_check["last"]["status"] == "unqualified"
    assert "stale" in router._cross_check["last"]["reason"]
    assert router._divergence.get(IID) is None
    # invalid observer: structurally rejected by the same acceptance as any
    # candidate (negative closes), never a comparison basis
    bad = FakeSource("other", bars=[bar(i, close="-1.0", provider="other",
                                        at=NOW - timedelta(minutes=8 - i)) for i in range(8)])
    router2, _ = router_with(FakeSource("primary", bars=good_t), bad, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, cross_check_tolerance_ratio="0.001"))
    bars, report = router2.get_minute_bars(IID, *WIN, pref=["primary", "other"],
                                           as_of=NOW, quote_max_age_seconds=180)
    assert report.ok and report.selected_provider == "primary"
    assert router2._cross_check["last"]["status"] == "unqualified"
    assert "quality violations" in router2._cross_check["last"]["reason"]


# -- in-flight resource capacity (ISSUE-20261017-03) -----------------------------

def test_recovery_probes_and_day_switch_cannot_multiply_inflight_workers():
    # Original Task17-12 Event repro: eight expired backoffs used to leave 1..8
    # live workers. A timed-out SDK call now keeps the only per-source slot.
    gate = threading.Event()
    blocked = BlockingSource("blocked", gate, bars=fresh_window("blocked", NOW))
    router, _ = router_with(blocked, pol=policy(
        per_source_timeout_seconds=0.01, round_timeout_seconds=0.03,
        failure_backoff_seconds=60.0, max_backoff_seconds=900.0,
        max_inflight_requests=4, max_inflight_requests_per_source=1))
    clock = FakeClock()
    router._clock = clock
    day_switched = False
    try:
        for attempt in range(8):
            clock.advance(901.0)
            if attempt == 4:
                router._beijing_day = lambda: datetime(2026, 10, 9, tzinfo=BEIJING).date()
                day_switched = True
            with pytest.raises(RuntimeError) as excinfo:
                router.get_minute_bars(IID, *WIN, pref=["blocked"])
            if attempt == 0:
                assert blocked.entered.wait(0.2)
            else:
                assert "busy" in str(excinfo.value)
            assert blocked.calls == 1  # no repeated recovery SDK call
            assert len(router._inflight) == 1
            # Day switch resets the daily counter only; it neither starts a new
            # request nor forgets the still-active slot.
            assert router._health("blocked")["requests"] == (0 if day_switched else 1)
        started = time.monotonic()
        assert not router.close(0.001)  # bounded close cannot cancel the SDK
        assert time.monotonic() - started < 0.2
    finally:
        gate.set()
        assert router.close(1.0)  # actual worker exit joins and releases
    assert not router._inflight and not router._inflight_by_source


def test_global_capacity_busy_has_no_queue_or_phantom_request_count():
    gate = threading.Event()
    blocked = BlockingSource("blocked", gate, bars=fresh_window("blocked", NOW))
    healthy = FakeSource("healthy", bars=fresh_window("healthy", NOW))
    router, _ = router_with(blocked, healthy, pol=policy(
        per_source_timeout_seconds=0.01, round_timeout_seconds=5.0,
        max_inflight_requests=1, max_inflight_requests_per_source=1))
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            router.get_minute_bars(IID, *WIN, pref=["blocked", "healthy"])
        assert blocked.entered.wait(0.2)
        assert time.monotonic() - started < 0.2  # busy is immediate, never queued
        assert healthy.calls == 0
        assert router._health("healthy")["requests"] == 0
        assert len(router._inflight) == 1
        busy = [e for e in router.minute_source_log[IID] if e["status"] == "busy"]
        assert busy and "router" in busy[-1]["reason"]
    finally:
        gate.set()
        assert router.close(1.0)


def test_healthy_backup_uses_free_slot_and_late_result_only_releases():
    gate = threading.Event()
    late = BlockingSource("late", gate, bars=fresh_window("late", NOW))
    good = FakeSource("good", bars=fresh_window("good", NOW))
    router, _ = router_with(late, good, pol=policy(
        per_source_timeout_seconds=0.01, round_timeout_seconds=5.0,
        failure_backoff_seconds=60.0, max_backoff_seconds=900.0,
        max_inflight_requests=2, max_inflight_requests_per_source=1))
    clock = FakeClock()
    router._clock = clock
    try:
        _, report = router.get_minute_bars(IID, *WIN, pref=["late", "good"])
        assert late.entered.wait(0.2)
        assert report.ok and report.selected_provider == "good"
        assert len(router._inflight) == 1  # late primary + completed backup <= 2

        router._minute_cache.clear()
        clock.advance(901.0)  # recovery backoff expires while old SDK is alive
        _, report2 = router.get_minute_bars(IID, *WIN, pref=["late", "good"])
        assert report2.ok and report2.selected_provider == "good"
        assert late.calls == 1 and good.calls == 2
        assert router._health("late")["requests"] == 1
        assert any("busy" in reason for reason in report2.source_rejections)

        cache = {key: (value["provider"], tuple(value["bars"]))
                 for key, value in router._minute_cache.items()}
        selected = dict(router.last_provider_by_dataset)
        health = {name: dict(value) for name, value in router._source_health.items()}
        divergence = dict(router._divergence)
        log_length = len(router.minute_source_log[IID])
        gate.set()
        assert router.close(1.0)
        # The late primary's valid-looking rows never become a success, cache,
        # selection or divergence mutation; only its slot disappears.
        assert {key: (value["provider"], tuple(value["bars"]))
                for key, value in router._minute_cache.items()} == cache
        assert router.last_provider_by_dataset == selected
        assert router._source_health == health
        assert router._divergence == divergence
        assert len(router.minute_source_log[IID]) == log_length
        assert not router._inflight
    finally:
        gate.set()
        assert router.close(1.0)


def test_observer_busy_preserves_counts_and_recovers_after_actual_exit():
    gate = threading.Event()
    primary = FakeSource("primary", bars=fresh_window("primary", NOW))
    observer = BlockingSource("observer", gate, bars=fresh_window("primary", NOW))
    router, _ = router_with(primary, observer, pol=policy(
        per_source_timeout_seconds=0.01, round_timeout_seconds=5.0,
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=5, max_inflight_requests=2,
        max_inflight_requests_per_source=1))
    clock = FakeClock()
    router._clock = clock
    try:
        _, report = router.get_minute_bars(IID, *WIN, pref=["primary", "observer"])
        assert observer.entered.wait(0.2)
        assert report.ok and report.selected_provider == "primary"
        assert router._cross_check["count"] == 1
        assert router._health("observer")["requests"] == 1
        assert len(router._inflight) == 1

        router._minute_cache.clear()
        clock.advance(1.0)
        _, report2 = router.get_minute_bars(IID, *WIN, pref=["primary", "observer"])
        assert report2.ok and report2.selected_provider == "primary"
        assert observer.calls == 1
        assert router._cross_check["count"] == 1  # busy is not an observation
        assert router._health("observer")["requests"] == 1
        assert router._cross_check["last"]["status"] == "busy"

        last = dict(router._cross_check["last"])
        health = {name: dict(value) for name, value in router._source_health.items()}
        divergence = dict(router._divergence)
        cache = {key: (value["provider"], tuple(value["bars"]))
                 for key, value in router._minute_cache.items()}
        workers = [request.worker for request in router._inflight]
        gate.set()
        for worker in workers:
            worker.join(1.0)
            assert not worker.is_alive()
        assert not router._inflight
        assert router._cross_check["last"] == last  # late observation not applied
        assert router._source_health == health
        assert router._divergence == divergence
        assert {key: (value["provider"], tuple(value["bars"]))
                for key, value in router._minute_cache.items()} == cache

        router._minute_cache.clear()
        clock.advance(1.0)
        _, report3 = router.get_minute_bars(IID, *WIN, pref=["primary", "observer"])
        assert report3.ok and report3.selected_provider == "primary"
        assert observer.calls == 2  # released capacity permits a real new observation
        assert router._cross_check["count"] == 2
        assert router._cross_check["last"]["status"] == "compared"
        assert not router._inflight
    finally:
        gate.set()
        assert router.close(1.0)


def test_worker_exception_releases_capacity_for_next_call():
    source = FakeSource("flaky", error=OSError("down"))
    router, _ = router_with(source, pol=policy(
        failure_backoff_seconds=0.0, max_inflight_requests=1,
        max_inflight_requests_per_source=1))
    try:
        with pytest.raises(RuntimeError):
            router.get_minute_bars(IID, *WIN, pref=["flaky"])
        assert source.calls == 1 and not router._inflight
        source.error = None
        source.bars = fresh_window("flaky", NOW)
        _, report = router.get_minute_bars(IID, *WIN, pref=["flaky"])
        assert report.ok and report.selected_provider == "flaky"
        assert source.calls == 2 and not router._inflight
    finally:
        assert router.close(1.0)


def test_close_with_registered_but_unstarted_worker_is_bounded_and_honest():
    # Task18-01 repro: a worker registered on its request before start() ran
    # used to make close() raise "cannot join thread before it is started".
    # close must stay bounded, and must not report the slot-holding unstarted
    # worker as finished.
    router, _ = router_with(FakeSource("s", bars=fresh_window("s", NOW)))
    request = router._acquire_minute_request("s")
    request.worker = threading.Thread(target=lambda: None, daemon=True)
    try:
        assert not router.close(0.02)  # bounded False, no RuntimeError
    finally:
        router._release_minute_request(request)
    assert router.close(0.0)  # after the slot is released, close is clean


def test_close_during_worker_start_bounded_and_rejects_new_reservations(monkeypatch):
    # Event-precise start window: close() called while the caller thread sits
    # inside Thread.start() (worker registered, ident still None) returns False
    # without joining the unstarted thread; close then rejects new reservations,
    # while the already-admitted fetch completes per the frozen semantics.
    from trademind.market import router as router_module
    worker_enter, proceed = threading.Event(), threading.Event()

    class PausableStart(threading.Thread):
        def start(self):
            if self.name == "trademind-minute-fetch":
                worker_enter.set()
                proceed.wait(2.0)
            super().start()

    monkeypatch.setattr(router_module.threading, "Thread", PausableStart)
    src = FakeSource("s", bars=fresh_window("s", NOW))
    router, _ = router_with(src, pol=policy(per_source_timeout_seconds=5.0,
                                            round_timeout_seconds=10.0))
    result = {}

    def caller():
        try:
            result["bars"], _ = router.get_minute_bars(IID, *WIN, pref=["s"])
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    caller_thread = threading.Thread(target=caller, daemon=True)
    caller_thread.start()
    try:
        assert worker_enter.wait(2.0)
        assert not router.close(0.02)  # slot held by the not-yet-started worker
        with pytest.raises(RuntimeError) as excinfo:
            router.get_minute_bars(IID, *WIN, pref=["s"])
        assert "closing" in str(excinfo.value)
    finally:
        proceed.set()
        caller_thread.join(2.0)
    assert "error" not in result and result["bars"]
    assert src.calls == 1  # the admitted fetch finished normally after close
    assert router.close(1.0)  # its real exit joined and released the capacity


def test_thread_start_failure_releases_capacity_exactly_once(monkeypatch):
    from trademind.market import router as router_module

    class StartFails(threading.Thread):
        def start(self):
            raise RuntimeError("no thread resources")

    monkeypatch.setattr(router_module.threading, "Thread", StartFails)
    src = FakeSource("s", bars=fresh_window("s", NOW))
    router, _ = router_with(src)
    with pytest.raises(RuntimeError):
        router.get_minute_bars(IID, *WIN, pref=["s"])
    assert src.calls == 0  # no SDK call was started without a worker
    assert not router._inflight and not router._inflight_by_source
    monkeypatch.undo()
    _, report = router.get_minute_bars(IID, *WIN, pref=["s"])
    assert report.ok and report.selected_provider == "s"  # capacity usable again
    assert router.close(1.0)


# -- fixture/replay semantics preserved ------------------------------------------

def test_fixture_minute_path_still_serves_under_acceptance(tmp_path):
    from trademind.market.router import DatasetRouter as R
    min_dir = tmp_path / "fixtures" / "minute"
    min_dir.mkdir(parents=True)
    rows = "\n".join(
        f"2026-10-08,{(datetime(2026, 10, 8, 9, 31, tzinfo=BEIJING) + timedelta(minutes=i)).isoformat()},"
        f"1.50,1.52,1.49,1.51,1000,1510.0" for i in range(20))
    (min_dir / f"{IID}_1m.csv").write_text(
        "trade_date,bar_time,open,high,low,close,volume,amount\n" + rows + "\n", encoding="utf-8")
    router = R(pref=["fixture"], fixture_root=str(tmp_path / "fixtures"))
    as_of = datetime(2026, 10, 8, 9, 52, tzinfo=BEIJING)
    bars, report = router.get_minute_bars(IID, as_of - timedelta(days=2), as_of, period="1m",
                                          as_of=as_of, quote_max_age_seconds=900)
    assert len(bars) == 20 and report.ok
    assert report.selected_provider == "fixture"
