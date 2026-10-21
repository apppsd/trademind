"""Unit tests for small capital sizing, T0 hard risk guard, and exit paths (TASK-45)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from trademind.config import RiskConfig, T0ExperimentConfig
from trademind.domain.base import new_id, utcnow
from trademind.domain.context import ContextSnapshot, FeatureVector, PortfolioView
from trademind.domain.enums import GuardVerdict, OrderSide, OrderStatus, RunMode, TradeAction
from trademind.domain.instruments import FeeSchedule
from trademind.domain.intents import PortfolioIntent, create_exit_portfolio_intent
from trademind.domain.orders import Fill, Order
from trademind.guard.guard import Guard, check_stop_loss_exit, check_session_close_exit
from trademind.market.base import BEIJING
from trademind.portfolio import Ledger
from trademind.storage import AuditSink, Repository
from trademind.storage.db import Database


@pytest.fixture
def test_setup(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(f"sqlite:///{db_path}")
    repo = Repository(db)
    audit = AuditSink(db)

    FEES = FeeSchedule(
        id=new_id("fs"), trace_id=new_id("t"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        commission_rate="0.00025", commission_min="0.1",
        stamp_duty_rate_sell="0.0", transfer_fee_rate="0.00001",
    )
    # 500 initial cash ledger
    ledger = Ledger(db, "paper-t0", FEES)
    ledger.deposit(Decimal("500"), datetime(2026, 10, 8, 9, 30, tzinfo=BEIJING))

    risk = RiskConfig(
        max_position_weight=Decimal("0.50"),
        max_total_exposure=Decimal("0.80"),
        max_daily_loss=Decimal("0.03"),
        max_order_notional=Decimal("300"),
    )
    t0_cfg = T0ExperimentConfig(
        enabled=True,
        initial_cash=Decimal("500"),
        max_total_exposure=Decimal("300"),
        max_single_exposure=Decimal("200"),
        min_cash_reserve=Decimal("100"),
        stop_loss_ratio=Decimal("0.008"),
        max_daily_loss_ratio=Decimal("0.03"),
        consecutive_losses_limit=3,
        daily_new_orders_limit=10,
        no_new_entry_time="14:50",
        close_out_time="14:55",
    )

    day_start_equity = Decimal("500")
    guard = Guard(
        repo=repo,
        ledger=ledger,
        risk=risk,
        mode="paper",
        universe=["513050.SH"],
        audit=audit,
        day_start_equity_provider=lambda: day_start_equity,
        t0_config=t0_cfg,
    )

    return repo, ledger, guard, t0_cfg


def make_context(instrument_id: str, as_of: datetime, ledger: Ledger) -> ContextSnapshot:
    acc = ledger.snapshot(as_of, {instrument_id: Decimal("1.50")})
    pv = PortfolioView(
        id=new_id("pv"), trace_id=new_id("tr"), created_at=utcnow(),
        portfolio_version=acc.version, account_id=acc.account_id,
        as_of=as_of, currency="CNY", cash=acc.cash, equity=acc.equity,
        positions={
            p.instrument_id: {"qty": str(p.quantity), "market_value": str(p.market_value or 0)}
            for p in acc.positions
        },
    )
    fv = FeatureVector(
        id=new_id("fv"), trace_id=new_id("tr"), created_at=utcnow(),
        feature_version="feat-test", instrument_id=instrument_id,
        as_of=as_of, values={"close": Decimal("1.50")},
    )
    return ContextSnapshot(
        id=new_id("cs"), trace_id=new_id("tr"), created_at=utcnow(),
        context_id=new_id("ctx"), as_of=as_of,
        expires_at=as_of + timedelta(minutes=15),
        instrument_id=instrument_id, universe_version="univ-t0",
        evidence_ids=[], features=fv, portfolio=pv,
    )


def test_t0_small_capital_sizing_approved(test_setup):
    _, ledger, guard, _ = test_setup
    now = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap = make_context("513050.SH", now, ledger)

    # Buy target weight 0.30 -> notional 150 (< 200 single exposure, < 300 total, 500 - 150 = 350 > 100 reserve)
    intent = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("tr"), created_at=utcnow(),
        intent_id=new_id("pi"), decision_id=new_id("dec"),
        account_id=ledger.account_id, portfolio_version=ledger.version,
        instrument_id="513050.SH", action=TradeAction.BUY,
        target_weight=Decimal("0.30"), current_weight=Decimal("0"),
        valid_until=now + timedelta(minutes=5), mode=RunMode.PAPER,
    )
    res, qty = guard.check(intent, snap, reference_price=Decimal("1.50"), now=now)
    assert res.verdict == GuardVerdict.ALLOW
    assert qty == Decimal("100")
    assert guard.daily_buy_orders_count == 1


def test_t0_single_exposure_limit_veto(test_setup):
    _, ledger, guard, _ = test_setup
    now = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap = make_context("513050.SH", now, ledger)

    # Target weight 0.50 -> 250 notional > 200 max single exposure
    intent = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("tr"), created_at=utcnow(),
        intent_id=new_id("pi"), decision_id=new_id("dec"),
        account_id=ledger.account_id, portfolio_version=ledger.version,
        instrument_id="513050.SH", action=TradeAction.BUY,
        target_weight=Decimal("0.80"), current_weight=Decimal("0"),
        valid_until=now + timedelta(minutes=5), mode=RunMode.PAPER,
    )
    res, qty = guard.check(intent, snap, reference_price=Decimal("1.50"), now=now)
    assert res.verdict == GuardVerdict.VETO
    assert "max_single_exposure_limit" in res.reason_codes


def test_t0_daily_loss_blocks_buy_allows_sell(test_setup):
    _, ledger, guard, _ = test_setup
    now = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    # Simulate equity drop from 500 to 480 (4% loss > 3% limit)
    guard.day_start_equity_provider = lambda: Decimal("500")
    ledger.withdraw(Decimal("20"), at=now)
    snap = make_context("513050.SH", now, ledger)

    # BUY intent should be vetoed by daily_loss_limit
    intent_buy = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("tr"), created_at=utcnow(),
        intent_id=new_id("pi"), decision_id=new_id("dec1"),
        account_id=ledger.account_id, portfolio_version=ledger.version,
        instrument_id="513050.SH", action=TradeAction.BUY,
        target_weight=Decimal("0.20"), current_weight=Decimal("0"),
        valid_until=now + timedelta(minutes=5), mode=RunMode.PAPER,
    )
    res_buy, _ = guard.check(intent_buy, snap, reference_price=Decimal("1.50"), now=now)
    assert res_buy.verdict == GuardVerdict.VETO
    assert "daily_loss_limit" in res_buy.reason_codes

    # But SELL (loss-cutting exit) should NOT be blocked by daily_loss_limit!
    order = Order(
        id=new_id("ord"), trace_id=new_id("tr"), created_at=now, updated_at=now,
        order_id="ord-1", client_order_id="cl-1", intent_id="pi-1", guard_id="gr-1",
        account_id=ledger.account_id, instrument_id="513050.SH", mode=RunMode.PAPER,
        side=OrderSide.BUY, quantity=Decimal("100"), limit_price=Decimal("1.50"),
        status=OrderStatus.NEW,
    )
    fill = Fill(
        id=new_id("fil"), trace_id=new_id("tr"), created_at=now,
        fill_id="fill-1", order_id="ord-1", provider_fill_id="pf-1",
        quantity=Decimal("100"), price=Decimal("1.50"), fee=Decimal("0.05"),
        executed_at=now, available_at=now,
    )
    ledger.apply_fill(fill, order, now)
    snap_pos = make_context("513050.SH", now, ledger)
    snap_pos.portfolio.portfolio_version = ledger.version

    exit_intent = create_exit_portfolio_intent(
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id="513050.SH",
        current_weight=Decimal("0.30"),
        valid_until=now + timedelta(minutes=5),
    )
    res_sell, qty_sell = guard.check(exit_intent, snap_pos, reference_price=Decimal("1.50"), now=now)
    assert res_sell.verdict == GuardVerdict.ALLOW
    assert qty_sell == Decimal("100")


def test_t0_consecutive_losses_and_order_limits(test_setup):
    _, ledger, guard, _ = test_setup
    now = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap = make_context("513050.SH", now, ledger)

    # Recover three closed losing trades from durable account history.
    for _ in range(3):
        ledger._emit('fill_buy', instrument_id='513050.SH', quantity=Decimal('100'), amount=Decimal('150'), effective_at=now)
        ledger._emit('fill_sell', instrument_id='513050.SH', quantity=Decimal('100'), amount=Decimal('145'), effective_at=now)
    guard.sync_counters(now)
    assert guard.consecutive_losses == 3

    intent_buy = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("tr"), created_at=utcnow(),
        intent_id=new_id("pi"), decision_id=new_id("dec2"),
        account_id=ledger.account_id, portfolio_version=ledger.version,
        instrument_id="513050.SH", action=TradeAction.BUY,
        target_weight=Decimal("0.20"), current_weight=Decimal("0"),
        valid_until=now + timedelta(minutes=5), mode=RunMode.PAPER,
    )
    res, _ = guard.check(intent_buy, snap, reference_price=Decimal("1.50"), now=now)
    assert res.verdict == GuardVerdict.VETO
    assert "consecutive_losses_limit" in res.reason_codes


def test_t0_time_cutoffs_and_exit_conditions():
    # 0.8% stop loss trigger
    assert not check_stop_loss_exit(Decimal("1.50"), Decimal("1.49"), Decimal("0.008"))  # 0.67% drop
    assert check_stop_loss_exit(Decimal("1.50"), Decimal("1.485"), Decimal("0.008"))  # 1.0% drop >= 0.8%

    # Time cutoffs
    t_1440 = datetime(2026, 10, 8, 14, 40, tzinfo=BEIJING)
    t_1456 = datetime(2026, 10, 8, 14, 56, tzinfo=BEIJING)
    assert not check_session_close_exit(t_1440, "14:55")
    assert check_session_close_exit(t_1456, "14:55")
