"""DatasetRouter: pick adapters by dataset/preference, audit switches, never
mix incompatible sources silently (ARCH-001 A-share data section).

Minute windows follow a bounded quality-driven multi-source policy
(ARCH-020 / TASK-20261014-04): a source that raises, returns nothing, or
fails acceptance (quality/staleness/visibility) does not stop later sources;
the wait per source and per round is hard-capped; per-source backoff,
recovery probes and daily request counts live here, never in model budgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable
import threading
import time

from .adapters.fixture import FixtureAdapter
from .base import BEIJING, Bar, FundamentalRecord, MarketDataAdapter, MinuteBar, NewsRecord
from .quality import (
    QualityReport,
    cross_source_close_divergence,
    normalize_bars,
    normalize_minute_bars,
)

AdapterFactory = Callable[[], MarketDataAdapter]


class _MinuteRequestBusy(RuntimeError):
    """No SDK call was started: the router/source in-flight capacity is full."""


@dataclass(eq=False)
class _MinuteRequest:
    """One owned in-flight slot. Only the worker releases it after the SDK call."""
    source: str
    worker: threading.Thread | None = None
    released: bool = False


@dataclass
class MinuteRoutingPolicy:
    """Bounded limits for minute-window source selection. These caps belong to
    market routing; model budgets and JEV/Flash scheduling are untouched."""
    #: bounded wait for one adapter attempt; the SDK request cannot be
    #: cancelled, so on timeout the worker is abandoned (bounded wait, not
    #: post-hoc measurement)
    per_source_timeout_seconds: float = 10.0
    #: adapters tried per call at most
    max_sources_per_round: int = 3
    #: whole selection round budget; a source's wait is clamped to what remains
    round_timeout_seconds: float = 30.0
    #: live SDK calls across this router, including workers abandoned by the
    #: caller after a timeout. No work is queued beyond this capacity.
    max_inflight_requests: int = 4
    #: one adapter is never called concurrently by main/observer/retry paths.
    max_inflight_requests_per_source: int = 1
    #: base wait before a failed source is retried (doubles per consecutive
    #: failure; the expired-backoff retry is the recovery probe)
    failure_backoff_seconds: float = 60.0
    #: backoff ceiling
    max_backoff_seconds: float = 900.0
    #: per-source observation cap per Beijing day
    daily_request_limit: int = 4000
    #: sparse parallel quality observation; disabled = zero extra requests
    cross_check_enabled: bool = False
    cross_check_interval_seconds: float = 900.0
    cross_check_max_daily: int = 24
    #: max relative close divergence on shared closed bars before new risk is refused
    cross_check_tolerance_ratio: str = "0.005"

    @classmethod
    def from_data_config(cls, data) -> "MinuteRoutingPolicy":
        """Map the flat [data] TOML fields (trademind.config.DataConfig)."""
        return cls(
            per_source_timeout_seconds=data.minute_source_timeout_seconds,
            max_sources_per_round=data.minute_max_sources,
            round_timeout_seconds=data.minute_round_timeout_seconds,
            failure_backoff_seconds=data.minute_failure_backoff_seconds,
            max_backoff_seconds=data.minute_max_backoff_seconds,
            daily_request_limit=data.minute_daily_request_limit,
            cross_check_enabled=data.minute_cross_check_enabled,
            cross_check_interval_seconds=data.minute_cross_check_interval_seconds,
            cross_check_max_daily=data.minute_cross_check_max_daily,
            cross_check_tolerance_ratio=data.minute_cross_check_tolerance_ratio,
        )


#: Engineering-level protocol qualification per minute source: offline-verifiable
#: evidence only (interface, unit mapping, time semantics). This is deliberately
#: NOT a claim of live intraday quality — that stays with its own acceptance
#: tasks and must never be reported as ready by startup checks.
MINUTE_SOURCE_PROTOCOL_STATUS = {
    "sina_etf": "protocol_qualified",  # ARCH-008 implemented; intraday: TASK-20261009-16
    "akshare": "protocol_qualified",  # unit mapping vs official sample: TASK-20261014-03; live sample pending
    "fixture": "offline_fixture_only",
    "baostock": "not_available_1m",
    "tushare": "not_available_1m",
}


class DatasetRouter:
    def __init__(self, pref: list[str] | None = None, fixture_root: str | None = None,
                 tushare_token: str = "", audit_cb: Callable[[str, str], None] | None = None,
                 minute_policy: MinuteRoutingPolicy | None = None) -> None:
        pref = pref or ["fixture"]
        self._factories: dict[str, AdapterFactory] = {}
        if "fixture" in pref:
            self._factories["fixture"] = (lambda root=fixture_root: FixtureAdapter(root))
        if "sina_etf" in pref:
            self._factories["sina_etf"] = lambda: _lazy("SinaETFAdapter")
        if "akshare" in pref:
            self._factories["akshare"] = lambda: _lazy("AKShareAdapter")
        if "baostock" in pref:
            self._factories["baostock"] = lambda: _lazy("BaoStockAdapter")
        if "tushare" in pref:
            self._factories["tushare"] = (lambda token=tushare_token: _lazy("TushareAdapter", token))
        self._factories = {name: self._factories[name] for name in pref if name in self._factories}
        self._instances: dict[str, MarketDataAdapter] = {}
        self.audit_cb = audit_cb
        self.last_provider_by_dataset: dict[str, str] = {}
        self.last_errors_by_dataset = {}
        self._minute_cache = {}
        self.minute_cache_seconds = 30
        self.minute_policy = minute_policy or MinuteRoutingPolicy()
        self._source_health: dict[str, dict] = {}
        # "Never observed" is an explicit state, not a zero timestamp: a fresh
        # boot's monotonic value can be smaller than the observation interval,
        # which silently skipped the first cross-check (ISSUE-20261015-01).
        self._cross_check = {"day": None, "count": 0, "last_mono": None, "last": None}
        # instrument_id -> {"note", "observer"}: once a cross-check finds a
        # divergence, that instrument's windows stay refused — across later pref
        # sources and re-requests — until an agreeing observation runs. A
        # re-selection must not launder the refusal (ISSUE-20261015-01).
        self._divergence: dict[str, dict] = {}
        # Injectable monotonic clock: observation cadence must be testable
        # without depending on how long the process/machine has been up.
        self._clock = time.monotonic
        self.minute_source_log: dict[str, list[dict]] = {}
        # In-flight capacity follows actual SDK worker lifetime, not caller
        # wait lifetime. The lock also makes concurrent router callers atomic;
        # there is deliberately no pending-work queue (ISSUE-20261017-03).
        self._inflight_lock = threading.Lock()
        self._inflight: set[_MinuteRequest] = set()
        self._inflight_by_source: dict[str, int] = {}
        self._minute_closed = False

    def _get(self, name: str) -> MarketDataAdapter:
        if name not in self._instances:
            if name not in self._factories:
                raise KeyError(f"adapter not configured: {name}")
            self._instances[name] = self._factories[name]()
        return self._instances[name]

    def _audit(self, dataset: str, provider: str, note: str = "") -> None:
        self.last_provider_by_dataset[dataset] = provider
        if self.audit_cb:
            self.audit_cb(f"{dataset}->{provider}", note)

    # -- minute routing helpers (TASK-20261014-04) ----------------------------

    def _beijing_day(self) -> date:
        return datetime.now(tz=BEIJING).date()

    def _health(self, name: str) -> dict:
        h = self._source_health.setdefault(
            name, {"failed_at": None, "fail_count": 0, "requests": 0, "day": self._beijing_day()})
        today = self._beijing_day()
        if h["day"] != today:
            h.update({"requests": 0, "day": today})
        return h

    def _source_skip_reason(self, name: str, now_mono: float) -> str | None:
        h = self._health(name)
        if h["requests"] >= self.minute_policy.daily_request_limit:
            return "daily request limit reached"
        if h["failed_at"] is None:
            return None
        backoff = min(self.minute_policy.failure_backoff_seconds * (2 ** (h["fail_count"] - 1)),
                      self.minute_policy.max_backoff_seconds)
        remaining = backoff - (now_mono - h["failed_at"])
        if remaining > 0:
            return f"backoff {remaining:.0f}s after {h['fail_count']} failure(s)"
        return None  # backoff expired: the next call is the bounded recovery probe

    def _mark_success(self, name: str) -> None:
        self._health(name).update({"failed_at": None, "fail_count": 0})

    def _mark_failure(self, name: str, now_mono: float) -> None:
        h = self._health(name)
        h["fail_count"] += 1
        h["failed_at"] = now_mono

    def _log(self, instrument_id: str, entry: dict) -> None:
        entry["at"] = datetime.now(tz=timezone.utc).isoformat()
        log = self.minute_source_log.setdefault(instrument_id, [])
        log.append(entry)
        del log[:-20]

    def _acquire_minute_request(self, name: str) -> _MinuteRequest:
        """Atomically reserve capacity without queueing or consuming a request."""
        policy = self.minute_policy
        with self._inflight_lock:
            if self._minute_closed:
                raise _MinuteRequestBusy("router closing: no new minute request")
            source_count = self._inflight_by_source.get(name, 0)
            if source_count >= policy.max_inflight_requests_per_source:
                raise _MinuteRequestBusy(
                    f"busy: source {name} has {source_count} in-flight request(s) "
                    f"(limit {policy.max_inflight_requests_per_source})")
            total = len(self._inflight)
            if total >= policy.max_inflight_requests:
                raise _MinuteRequestBusy(
                    f"busy: router has {total} in-flight request(s) "
                    f"(limit {policy.max_inflight_requests})")
            request = _MinuteRequest(source=name)
            self._inflight.add(request)
            self._inflight_by_source[name] = source_count + 1
            return request

    def _begin_minute_request(self, name: str) -> _MinuteRequest:
        """Reserve a slot before any daily/observation request count is consumed."""
        request = self._acquire_minute_request(name)
        try:
            self._health(name)["requests"] += 1
        except BaseException:
            self._release_minute_request(request)
            raise
        return request

    def _release_minute_request(self, request: _MinuteRequest) -> None:
        """Idempotent, exception-safe release after the actual SDK call ends."""
        with self._inflight_lock:
            if request.released:
                return
            request.released = True
            self._inflight.discard(request)
            count = self._inflight_by_source.get(request.source, 0)
            if count <= 1:
                self._inflight_by_source.pop(request.source, None)
            else:
                self._inflight_by_source[request.source] = count - 1

    def _bounded_fetch(self, attempt, timeout_s: float, request: _MinuteRequest):
        """Run one adapter attempt under a bounded wait. The SDK request cannot
        be cancelled, so on timeout the caller abandons the wait while the slot
        remains owned by the worker until the call really finishes."""
        if timeout_s <= 0:
            try:
                return attempt()
            finally:
                self._release_minute_request(request)
        box: dict = {}

        def _run():
            try:
                box["result"] = attempt()
            except BaseException as exc:  # noqa: BLE001 - relayed to the caller
                box["error"] = exc
            finally:
                # A late answer writes only this invocation's local box. It
                # cannot touch cache/selection/health/divergence; it only frees
                # the resource after the SDK has actually returned or raised.
                self._release_minute_request(request)

        worker = threading.Thread(target=_run, daemon=True, name="trademind-minute-fetch")
        request.worker = worker
        try:
            worker.start()
        except BaseException:
            self._release_minute_request(request)
            raise
        worker.join(timeout_s)
        if worker.is_alive():
            raise TimeoutError(
                f"source did not answer within {timeout_s:.1f}s; wait bounded, request still in flight")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def close(self, timeout_seconds: float = 0.0) -> bool:
        """Reject new minute SDK calls and join current workers within a real
        time budget. Returns False if requests remain; SDK threads are never
        portrayed as forcibly cancellable. A later close may wait again.

        A worker registered but not yet started (the assignment->start window)
        still owns its slot: it is never joined here - joining would raise -
        and it keeps the result False until its real start and finish release
        the capacity (TASK-20261018-01)."""
        with self._inflight_lock:
            self._minute_closed = True
            workers = [request.worker for request in self._inflight
                       if request.worker is not None]
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for worker in workers:
            if worker.ident is None:
                continue  # not started yet: never join it, never call it done
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)
        with self._inflight_lock:
            return not self._inflight

    @staticmethod
    def _minute_rejection_reason(bars: list[MinuteBar], report: QualityReport,
                                 as_of: datetime | None, max_age_s) -> str | None:
        if not bars:
            return "empty window"
        if report.limit_violations:
            return f"quality violations: {report.limit_violations[0]}"
        if as_of is not None and max_age_s is not None:
            age = (as_of - bars[-1].bar_time).total_seconds()
            if age > max_age_s:
                return f"stale: latest visible bar {age:.0f}s old > {max_age_s}s"
        return None

    def _maybe_cross_check(self, selected_name: str, selected_bars: list[MinuteBar],
                           instrument_id: str, start: datetime, end: datetime, period: str,
                           as_of: datetime | None = None, quote_max_age_seconds=None,
                           deadline: float | None = None) -> list[str]:
        """Sparse parallel quality observation (ARCH-020). Rate-limited, counted
        against its own daily cap; failures are reported independently and never
        poison routing state or trigger model calls.

        The observer is held to the same constraints as the candidate: it shares
        the caller's round `deadline` (its wait is clamped to the remaining
        budget, and an exhausted budget starts no request), and the observation
        must pass the same visibility (`as_of`), structural quality and
        freshness acceptance before it may serve as a comparison basis — a
        future-visible, unclosed or stale observation is recorded as
        "unqualified", never compared, and never counted as agreement.

        An observed divergence beyond tolerance is remembered per instrument
        (`_divergence`) so the refusal survives later pref sources and
        re-requests until an agreeing observation lifts it. Returns violation
        notes for the current window."""
        policy = self.minute_policy
        if not policy.cross_check_enabled or selected_name == "fixture" or not selected_bars:
            return []
        now_mono = self._clock()
        cc = self._cross_check
        today = self._beijing_day()
        if cc["day"] != today:
            cc.update({"day": today, "count": 0})
        if cc["last_mono"] is not None and \
                now_mono - cc["last_mono"] < policy.cross_check_interval_seconds:
            return []
        if cc["count"] >= policy.cross_check_max_daily:
            return []
        others = [n for n in self._factories if n != selected_name]
        if not others:
            return []
        observer = others[0]
        skip = self._source_skip_reason(observer, now_mono)
        if skip:
            cc["last"] = {"observer": observer, "status": "skipped", "reason": skip}
            return []
        remaining = policy.per_source_timeout_seconds if deadline is None \
            else deadline - now_mono
        if remaining <= 0:
            # The round budget is spent: no out-of-budget observation request,
            # and none of the observation bookkeeping is consumed.
            cc["last"] = {"observer": observer, "status": "skipped",
                          "reason": "round limit: total time budget exhausted"}
            return []
        try:
            request = self._begin_minute_request(observer)
        except _MinuteRequestBusy as exc:
            # Capacity is not an observation: no SDK call, daily request,
            # observation count or cadence timestamp is consumed.
            cc["last"] = {"observer": observer, "status": "busy", "reason": str(exc)}
            return []
        cc["last_mono"] = now_mono
        cc["count"] += 1
        try:
            def _attempt():
                adapter = self._get(observer)
                return adapter.get_minute_bars(instrument_id, start, end, period=period)
            bars = self._bounded_fetch(
                _attempt, min(policy.per_source_timeout_seconds, remaining), request)
        except Exception as exc:  # noqa: BLE001
            # The abandoned worker writes only into its own local box (see
            # _bounded_fetch): a late answer cannot touch routing state.
            cc["last"] = {"observer": observer, "status": "error", "reason": type(exc).__name__}
            return []
        # Same acceptance as the candidate: visibility (as_of), structural
        # quality, freshness. Unqualified observations are never a comparison
        # basis and never count as agreement (the standing divergence, if any,
        # stays).
        if as_of is not None:
            bars = [b for b in bars if b.available_at <= as_of and b.bar_time <= as_of]
        norm, rep = normalize_minute_bars(bars, as_of=as_of)
        reason = self._minute_rejection_reason(norm, rep, as_of, quote_max_age_seconds)
        if reason is not None:
            cc["last"] = {"observer": observer, "status": "unqualified", "reason": reason}
            return []
        worst, at = cross_source_close_divergence(selected_bars, norm)
        cc["last"] = {"observer": observer, "status": "compared",
                      "max_divergence": str(worst) if worst is not None else None,
                      "at": at.isoformat() if at else None}
        if worst is None:
            return []  # no shared closed bar times: inconclusive, not agreement
        if worst > Decimal(policy.cross_check_tolerance_ratio):
            note = (f"cross-source divergence vs {observer} at {at.isoformat()}: "
                    f"{worst:.6f} > tolerance {policy.cross_check_tolerance_ratio}; "
                    f"windows refused until an agreeing cross-check")
            self._divergence[instrument_id] = {"note": note, "observer": observer}
            return [note]
        # Agreeing observation: the standing refusal for this instrument is lifted.
        self._divergence.pop(instrument_id, None)
        return []

    # -- datasets --------------------------------------------------------------

    def get_daily_bars(self, instrument_id: str, start: date, end: date,
                       pref: list[str] | None = None,
                       as_of: datetime | None = None) -> tuple[list[Bar], QualityReport]:
        """`as_of` carries replay visibility: only rows already visible at
        that instant enter the per-date revision merge, so a future
        correction can never evict a historically visible row
        (ISSUE-20261019-02). `as_of=None` keeps the live default — rows
        returned by a live fetch are already visible."""
        errors: list[str] = []
        for name in (pref or list(self._factories)):
            try:
                adapter = self._get(name)
                bars = adapter.get_daily_bars(instrument_id, start, end)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if as_of is not None:
                visible = [b for b in bars if b.available_at <= as_of]
                # Dedup within the visible set is deterministic regardless of
                # input order: "later fetch wins" sees ascending availability.
                visible.sort(key=lambda b: b.available_at)
            else:
                visible = bars
            bars, report = normalize_bars(visible)
            self._audit("daily_bars", name)
            return bars, report
        raise RuntimeError(f"no adapter served daily_bars for {instrument_id}: {errors}")

    def get_minute_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        period: str = "1m",
        pref: list[str] | None = None,
        as_of: datetime | None = None,
        quote_max_age_seconds: int | float | None = None,
    ) -> tuple[list[MinuteBar], QualityReport]:
        """Quality-driven multi-source minute fetch. `as_of` + `quote_max_age_seconds`
        carry the caller's acceptance policy (replay/fixture semantics preserved:
        visibility is decided by the records, never by wall clock)."""
        policy = self.minute_policy
        pref = pref or list(self._factories)
        cache_key = (instrument_id, period, tuple(pref))
        now_mono = self._clock()
        cached = self._minute_cache.get(cache_key)
        # A standing cross-source divergence refuses cached windows too: an
        # entry cached before the divergence was detected must not launder the
        # refusal — the re-request falls through to a fresh, observed fetch
        # (acceptance 2026-10-15, TASK-20261015-03).
        if (cached and instrument_id not in self._divergence
                and now_mono - cached["observed"] <= self.minute_cache_seconds
                and cached.get("period") == period):
            if start >= cached["start"] and 0 <= (end - cached["end"]).total_seconds() <= self.minute_cache_seconds:
                # Replay reuse rule (TASK-08 semantics): a cached bar is handed
                # out only once it is available. With no as_of the boundary is
                # the window end, and an empty filtered result is the
                # legitimate cached answer; a rejected non-empty window (stale
                # or quality-failed vs an explicit as_of policy) falls through
                # to a fresh fetch instead of blocking a backup attempt.
                boundary = as_of if as_of is not None else end
                bars = [b for b in cached["bars"]
                        if start <= b.bar_time <= end and b.available_at <= boundary]
                bars, report = normalize_minute_bars(bars, as_of=as_of)
                reason = self._minute_rejection_reason(bars, report, as_of, quote_max_age_seconds)
                if reason is None or (not bars and as_of is None):
                    report.selected_provider = cached["provider"]
                    return bars, report
        errors: list[str] = []
        rejections: list[str] = []
        self.last_errors_by_dataset["minute_bars"] = []
        deadline = now_mono + policy.round_timeout_seconds
        attempts = 0
        candidates: list[tuple[str, list[MinuteBar], QualityReport]] = []
        for name in pref:
            if attempts >= policy.max_sources_per_round:
                rejections.append("round limit: max sources reached")
                break
            remaining = deadline - self._clock()
            if remaining <= 0:
                rejections.append("round limit: total time budget exhausted")
                break
            skip = self._source_skip_reason(name, self._clock())
            if skip:
                rejections.append(f"{name}: {skip}")
                continue
            timeout_s = min(policy.per_source_timeout_seconds, remaining)
            try:
                request = self._begin_minute_request(name)
            except _MinuteRequestBusy as exc:
                reason = str(exc)
                rejections.append(f"{name}: {reason}")
                self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                          "status": "busy", "reason": reason})
                continue
            attempts += 1
            try:
                def _attempt(name=name):
                    adapter = self._get(name)
                    return adapter, adapter.get_minute_bars(instrument_id, start, end, period=period)
                adapter, bars = self._bounded_fetch(_attempt, timeout_s, request)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                root = getattr(exc, "__cause__", None) or exc
                self.last_errors_by_dataset["minute_bars"].append(
                    {"provider": name, "error_type": type(root).__name__})
                self._mark_failure(name, self._clock())
                self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                          "status": "error", "reason": type(exc).__name__})
                if isinstance(exc, TimeoutError) and timeout_s < policy.per_source_timeout_seconds:
                    # The wait was clamped to the round budget and it expired:
                    # the round's time is spent; later sources cannot get a
                    # real budget, so stop instead of burning a request on a
                    # guaranteed timeout.
                    rejections.append("round limit: total time budget exhausted")
                    break
                continue
            if not bars and not adapter.capabilities().get("minute_bars", False):
                continue  # not a minute source at all
            # Visibility: with a replay as_of, only rows already visible then
            # count (availability decided by the records, never wall clock).
            # Without as_of the fetch path returns the source's rows as-is —
            # replay/live availability filtering stays with the cache rule and
            # the caller (TASK-08 semantics).
            if as_of is not None:
                bars = [b for b in bars if b.available_at <= as_of and b.bar_time <= as_of]
            norm_bars, report = normalize_minute_bars(bars, as_of=as_of)
            reason = self._minute_rejection_reason(norm_bars, report, as_of, quote_max_age_seconds)
            if reason is None:
                notes = self._maybe_cross_check(
                    name, norm_bars, instrument_id, start, end, period,
                    as_of=as_of, quote_max_age_seconds=quote_max_age_seconds,
                    deadline=deadline)
                if notes:
                    # divergence beyond tolerance refuses this window: no caching,
                    # no averaging, no splicing; the reason travels on the report.
                    # No backoff mark: the blame is ambiguous between the pair,
                    # the standing refusal below carries the protection, and
                    # request burn is bounded by the daily limits.
                    report.limit_violations.extend(notes)
                    rejections.extend(notes)
                    self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                              "status": "rejected", "reason": notes[0]})
                    candidates.append((name, norm_bars, report))
                    continue
                divergence = self._divergence.get(instrument_id)
                if divergence is not None:
                    # A prior observation diverged and no agreeing cross-check
                    # has run since (interval, daily cap, budget or observer
                    # quality can all suppress one): the refusal stands for
                    # this later pref source and for every re-request — a
                    # re-selection must not wash it out, and it is never cached.
                    note = divergence["note"]
                    report.limit_violations.append(note)
                    rejections.append(note)
                    self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                              "status": "rejected", "reason": note})
                    candidates.append((name, norm_bars, report))
                    continue
                self._mark_success(name)
                self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                          "status": "selected", "rows": len(norm_bars)})
                self._audit(f"minute_bars_{period}", name)
                if name != "fixture":
                    self._minute_cache[cache_key] = {"observed": self._clock(), "start": start,
                                                     "end": end, "bars": norm_bars,
                                                     "provider": name, "period": period}
                report.selected_provider = name
                report.source_rejections = list(rejections)
                return norm_bars, report
            candidates.append((name, norm_bars, report))
            rejections.append(f"{name}: {reason}")
            # Empty/stale windows are properties of this (instrument, window),
            # not of source health — they must not put a source into backoff
            # (replay repeatedly re-asks neighbouring windows). Backoff is
            # reserved for transport failures and quality violations.
            if reason.startswith("quality violations"):
                self._mark_failure(name, self._clock())
            self._log(instrument_id, {"instrument": instrument_id, "provider": name,
                                      "status": "rejected", "reason": reason})
        for r in rejections:
            self.last_errors_by_dataset["minute_bars"].append(
                {"provider": "router", "error_type": "source_rejected", "reason": r})
        if not candidates:
            raise RuntimeError(
                f"no adapter served minute_bars for {instrument_id}: "
                f"{errors or rejections or 'no source attempted'}")
        # No source qualified: return the least-bad window (most visible rows,
        # then freshest) with its report so the downstream quality/freshness
        # gates keep rejecting exactly as before. Nothing is fabricated or
        # averaged; only a qualified window is ever cached.
        best_name, best_bars, best_report = max(
            candidates,
            key=lambda c: (len(c[1]), c[1][-1].bar_time.timestamp() if c[1] else float("-inf")))
        best_report.selected_provider = best_name
        best_report.source_rejections = list(rejections)
        self._audit(f"minute_bars_{period}", best_name, note="no qualified source")
        return best_bars, best_report

    def get_news(self, instrument_id: str, start: datetime, end: datetime) -> list[NewsRecord]:
        for name in list(self._factories):
            try:
                adapter = self._get(name)
                recs = adapter.get_news(instrument_id, start, end)
            except Exception:  # noqa: BLE001
                continue
            if recs:
                self._audit("news", name)
            return recs
        return []

    def get_fundamentals(self, instrument_id: str) -> list[FundamentalRecord]:
        for name in list(self._factories):
            try:
                adapter = self._get(name)
                recs = adapter.get_fundamentals(instrument_id)
            except Exception:  # noqa: BLE001
                continue
            if recs:
                self._audit("fundamentals", name)
            return recs
        return []


def _lazy(cls_name: str, *args):
    from . import adapters as m

    return getattr(m, cls_name)(*args) if args else getattr(m, cls_name)()
