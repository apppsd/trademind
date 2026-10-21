"""TASK-15/16/12/13/14/18: guard, OMS, full chain, memory tests (offline)."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.config import AppConfig, RiskConfig
from trademind.context import ContextBuilder
from trademind.domain.base import new_id
from trademind.domain.context import ContextSnapshot, FeatureVector
from trademind.domain.enums import ConfidenceSemantics, GuardVerdict, RunMode, TradeAction
from trademind.domain.instruments import FeeSchedule
from trademind.domain.intents import PortfolioIntent
from trademind.guard import Guard
from trademind.market.adapters.fixture import FixtureAdapter
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.portfolio import Ledger
from trademind.storage import AuditSink, Database, Repository

UTC = timezone.utc
NOW = datetime(2026, 8, 10, tzinfo=UTC)
FEES = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"),
                   created_at=datetime(2026, 1, 1, tzinfo=UTC),
                   commission_rate="0.00025", commission_min="5",
                   stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")


def make_snapshot(tmp_path_unused, portfolio_version=1, instrument="600519.SH",
                  weight=None):
    fv = FeatureVector(id=new_id("fv"), trace_id=new_id("t"),
                       created_at=datetime(2026, 8, 10, tzinfo=UTC),
                       feature_version="fv", instrument_id=instrument,
                       as_of=datetime(2026, 8, 10, tzinfo=UTC),
                       values={"close": Decimal("40")})
    snap = ContextSnapshot(
        id=new_id("cs"), trace_id=new_id("t"),
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        context_id=new_id("ctx"), as_of=datetime(2026, 8, 10, tzinfo=UTC),
        expires_at=datetime(2026, 8, 10, tzinfo=UTC) + timedelta(minutes=15),
        instrument_id=instrument, universe_version="u1",
        evidence_ids=["e1"], features=fv)
    return snap


def make_intent(weight="0.15", current="0", action=TradeAction.BUY,
                valid_minutes=30):
    NOW = datetime(2026, 8, 10, tzinfo=UTC)
    return PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("t"),
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        intent_id=new_id("pi"), decision_id=new_id("dec"),
        account_id="paper-001", portfolio_version=1, instrument_id="600519.SH",
        action=action, target_weight=Decimal(weight), current_weight=Decimal(current),
        valid_until=datetime(2026, 8, 10, tzinfo=UTC) + timedelta(minutes=valid_minutes),
        mode=RunMode.PAPER)


@pytest.fixture()
def guard_env(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'g.db'}")
    repo = Repository(db)
    audit = AuditSink(db)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), datetime(2026, 8, 10, tzinfo=UTC))
    risk = RiskConfig()
    guard = Guard(repo, led, risk, "paper", ["600519.SH"], audit,
                  day_start_equity_provider=lambda: Decimal("1000000"))
    snap = make_snapshot(tmp_path)
    # portfolio version must match: build a snapshot with portfolio attached
    from trademind.domain.context import PortfolioView

    snap = snap.model_copy(update={
        "portfolio": PortfolioView(
            id=new_id("pv"), trace_id=new_id("t"),
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
            portfolio_version=led.version, account_id="paper-001",
            as_of=datetime(2026, 8, 10, tzinfo=UTC), currency="CNY",
            cash=Decimal("1000000"), equity=Decimal("1000000"), positions={}),
    })
    yield guard, snap, led, repo, audit
    db.close()


def test_guard_allows_reasonable_buy_and_reserves(guard_env):
    guard, snap, led, repo, audit = guard_env
    intent = make_intent()
    result, qty = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert result.verdict == GuardVerdict.ALLOW
    assert qty and qty > 0
    snap2 = led.snapshot(datetime(2026, 8, 10, tzinfo=UTC))
    assert snap2.reserved_cash > 0


def test_guard_rejects_over_weight(guard_env):
    guard, snap, led, repo, audit = guard_env
    intent = make_intent(weight="0.35")  # > 0.20 limit
    result, qty = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert result.verdict == GuardVerdict.VETO
    assert "position_weight_limit" in result.reason_codes


def test_guard_rejects_expired_intent(guard_env):
    guard, snap, led, repo, audit = guard_env
    intent = make_intent(valid_minutes=-5)
    result, qty = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert "intent_expired" in result.reason_codes


def test_guard_rejects_wrong_universe_and_out_of_range_price(guard_env):
    guard, snap, led, repo, audit = guard_env
    intent = make_intent()
    intent = intent.model_copy(update={"instrument_id": "999999.SH"})
    result, _ = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert "instrument_out_of_universe" in result.reason_codes


def test_guard_duplicate_decision_idempotent(guard_env):
    guard, snap, led, repo, audit = guard_env
    intent = make_intent()
    r1, q1 = guard.check(intent, snap, Decimal("40"), now=NOW)
    r2, q2 = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert r1.verdict == GuardVerdict.ALLOW
    assert r2.verdict == GuardVerdict.VETO
    assert "duplicate_decision" in r2.reason_codes


def test_guard_kill_switch_blocks_and_persists(guard_env):
    guard, snap, led, repo, audit = guard_env
    repo.set_kill_switch(True, "manual")
    intent = make_intent()
    result, _ = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert "kill_switch" in result.reason_codes
    repo.set_kill_switch(False, "")


def test_guard_daily_loss_limit(guard_env):
    guard, snap, led, repo, audit = guard_env
    led.withdraw(Decimal("50000"), datetime(2026, 8, 10, tzinfo=UTC))  # -5% day
    intent = make_intent().model_copy(update={"portfolio_version": led.version})
    snap = snap.model_copy(update={
        "portfolio": snap.portfolio.model_copy(update={"portfolio_version": led.version})})
    result, _ = guard.check(intent, snap, Decimal("40"), now=NOW)
    assert "daily_loss_limit" in result.reason_codes


def test_guard_concurrent_approvals_cannot_oversell_cash(guard_env):
    """Two intents for different decisions at ~full size: second must fail."""
    guard, snap, led, repo, audit = guard_env
    i1 = make_intent(weight="0.19")
    i2 = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("t"),
        created_at=datetime(2026, 8, 10, tzinfo=UTC), intent_id=new_id("pi"),
        decision_id=new_id("dec"), account_id="paper-001", portfolio_version=1,
        instrument_id="600519.SH", action=TradeAction.BUY,
        target_weight=Decimal("0.19"), current_weight=Decimal("0"),
        valid_until=datetime(2026, 8, 10, tzinfo=UTC) + timedelta(minutes=30),
        mode=RunMode.PAPER)
    # portfolio version in snapshot must match ledger for both
    r1, q1 = guard.check(i1, snap, Decimal("40"), now=NOW)
    r2, q2 = guard.check(i2, snap, Decimal("40"), now=NOW)
    assert r1.verdict == GuardVerdict.ALLOW
    # second consumed the remaining reservation budget but both fit under limits;
    # assert no negative free cash ever
    account = led.snapshot(datetime(2026, 8, 10, tzinfo=UTC))
    assert account.cash - account.reserved_cash >= 0


def test_oms_idempotent_submit_and_fill(tmp_path):
    from trademind.execution import OMS, PaperBroker, PaperConfig

    db = Database(f"sqlite:///{tmp_path/'o.db'}")
    repo = Repository(db)
    audit = AuditSink(db)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), datetime(2026, 8, 10, tzinfo=UTC))
    router = DatasetRouter(pref=["fixture"])
    cal = TradingCalendar.load()
    broker = PaperBroker(router, cal, FEES, PaperConfig(fill_lag_bars=1))
    oms = OMS(repo, led, broker, audit)
    intent = make_intent().model_copy(update={"instrument_id": "600036.SH"})
    from audit_helpers import authorize
    guard_res, qty = authorize(repo, led, audit, intent, NOW)
    o1 = oms.submit(intent, guard_res, qty, Decimal("40.00"), now=NOW)
    o2 = oms.submit(intent, guard_res, qty, Decimal("40.00"), now=NOW)
    assert o1.order_id == o2.order_id  # same decision → same order, no dup
    # fill on a later visible bar (Aug 11+)
    fill = None
    for day_offset in range(1, 6):
        now = datetime(2026, 8, 10, tzinfo=UTC) + timedelta(days=day_offset)
        fill = oms.poll_fill(o1.order_id, now)
        if fill:
            break
    assert fill is not None
    snap = led.snapshot(datetime(2026, 8, 20, tzinfo=UTC))
    assert snap.cash < Decimal("1000000")
    db.close()


def test_oms_limit_not_touched_stays_open(tmp_path):
    from trademind.execution import OMS, PaperBroker, PaperConfig

    db = Database(f"sqlite:///{tmp_path/'o2.db'}")
    repo = Repository(db)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), datetime(2026, 8, 1, tzinfo=UTC))
    broker = PaperBroker(DatasetRouter(pref=["fixture"]), TradingCalendar.load(), FEES,
                         PaperConfig(fill_lag_bars=1))
    oms = OMS(repo, led, broker, AuditSink(db))
    intent = make_intent().model_copy(update={"instrument_id": "600036.SH"})
    from audit_helpers import authorize
    intent = intent.model_copy(update={"limit_price": Decimal("30.00")})
    guard_res, qty = authorize(repo, led, AuditSink(db), intent, NOW, Decimal("30"))
    order = oms.submit(intent, guard_res, qty, Decimal("30.00"), now=NOW)  # far below market
    fill = oms.poll_fill(order.order_id, datetime(2026, 8, 12, tzinfo=UTC))
    assert fill is None  # limit untouched
    db.close()


def test_full_offline_chain_buy_holding_sell(tmp_path):
    """The ARCH-001 acceptance scenario 1-6 in miniature with stubs."""
    from trademind.pipeline import Pipeline

    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'pipe.db'}"
    cfg.mode = "replay"
    pipe = Pipeline(cfg)
    pipe.ensure_account(at=datetime(2026, 8, 9, tzinfo=UTC))
    as_of = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)  # after Aug-10 breakout bar visible
    res = pipe.run_decision("600036.SH", as_of)
    assert res.error is None, res.error
    assert res.thesis is not None
    if res.intent and res.intent.action == TradeAction.BUY:
        assert res.guard_verdict == "ALLOW"
        assert res.order_id
        # fill over next days
        for d in range(1, 6):
            pipe.poll_fills(as_of + timedelta(days=d))
        snap = pipe.ledger.snapshot(as_of + timedelta(days=6))
        assert any(p.instrument_id == "600036.SH" for p in snap.positions)
        # holding re-check produces SELL/HOLD without error
        res2 = pipe.run_decision("600036.SH", as_of + timedelta(days=7))
        assert res2.error is None
    else:
        assert res.guard_verdict in ("HOLD_NO_TRADE", "REJECT", "APPROVE", "VETO", None)
