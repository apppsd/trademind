"""Tests for TASK-46: Isolated T+0 paper OMS execution loop and account settlement."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from trademind.config import AppConfig, RiskConfig, T0ExperimentConfig
from trademind.domain.base import new_id, utcnow
from trademind.domain.context import ContextSnapshot, FeatureVector, PortfolioView
from trademind.domain.enums import GuardVerdict, OrderSide, OrderStatus, RunMode, TradeAction
from trademind.domain.instruments import FeeSchedule
from trademind.domain.intents import ExitCondition, PortfolioIntent
from trademind.domain.orders import Fill, Order
from trademind.errors import RecoveryRequired
from trademind.execution.oms import OMS
from trademind.execution.paper import PaperBroker, PaperConfig
from trademind.guard import Guard
from trademind.market.base import BEIJING, MinuteBar
from trademind.market.calendar import TradingCalendar
from trademind.market.quality import QualityReport
from trademind.market.router import DatasetRouter
from trademind.portfolio import Ledger
from trademind.storage import AuditSink, Database, Repository

UTC = timezone.utc
TEST_DAY = date(2026, 10, 8)
ETF_ID = "513050.SH"
STOCK_ID = "600519.SH"

T0_FEES = FeeSchedule(
    id=new_id("fs"),
    trace_id=new_id("tr"),
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    commission_rate="0.0002",
    commission_min="0.1",
    stamp_duty_rate_sell="0.0",  # Zero stamp duty for ETF
    transfer_fee_rate="0.00001",
)


class MockMinuteRouter(DatasetRouter):
    def __init__(self, minute_bars: list[MinuteBar]):
        super().__init__(pref=["fixture"])
        self.custom_minute_bars = minute_bars

    def get_minute_bars(self, instrument_id: str, start: datetime, end: datetime, period: str = "1m", pref=None):
        matched = [
            b for b in self.custom_minute_bars
            if b.instrument_id == instrument_id and start <= b.bar_time <= end
        ]
        return matched, QualityReport(instrument_id=instrument_id, rows_in=len(matched), rows_out=len(matched))


def make_min_bar(instrument_id: str, bar_time: datetime, open_p: str, high_p: str, low_p: str, close_p: str, vol: str = "10000") -> MinuteBar:
    o, h, l, c = Decimal(open_p), Decimal(high_p), Decimal(low_p), Decimal(close_p)
    return MinuteBar(
        instrument_id=instrument_id,
        trade_date=bar_time.date(),
        bar_time=bar_time,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal(vol),
        amount=c * Decimal(vol),
        provider="mock",
        source_time=bar_time,
        fetched_at=bar_time + timedelta(seconds=1),
        available_at=bar_time + timedelta(seconds=1),
        period="1m",
    )


def make_context_snapshot(ledger: Ledger, instrument_id: str, now: datetime) -> ContextSnapshot:
    snap = ledger.snapshot(now)
    pos_map = {}
    for p in snap.positions:
        w = snap.position_weight(p.instrument_id) or Decimal("0")
        pos_map[p.instrument_id] = {
            "quantity": str(p.quantity),
            "qty": str(p.quantity),
            "weight": str(w),
            "market_value": str(p.market_value or 0),
        }
    fv = FeatureVector(
        id=new_id("fv"),
        trace_id=new_id("tr"),
        created_at=now,
        feature_version="feat-v1",
        instrument_id=instrument_id,
        as_of=now,
        values={"close": Decimal("2.000")},
        missing_fields={},
        window={"regime": "UPTREND"},
    )
    pv = PortfolioView(
        id=new_id("pv"),
        trace_id=new_id("tr"),
        created_at=now,
        portfolio_version=ledger.version,
        account_id=ledger.account_id,
        as_of=now,
        currency="CNY",
        cash=snap.cash,
        equity=snap.equity,
        positions=pos_map,
        reserved_cash=snap.reserved_cash,
    )
    return ContextSnapshot(
        id=new_id("ctx"),
        trace_id=new_id("tr"),
        created_at=now,
        context_id=new_id("ctx"),
        as_of=now,
        expires_at=now + timedelta(minutes=15),
        instrument_id=instrument_id,
        universe_version="v1",
        features=fv,
        portfolio=pv,
        evidence_ids=["ev1"],
    )


@pytest.fixture
def t0_oms_env(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 't0_oms.db'}")
    repo = Repository(db)
    audit = AuditSink(db)
    account_id = "paper-t0-500"
    ledger = Ledger(db, account_id, T0_FEES)
    start_time = datetime(2026, 10, 8, 9, 30, tzinfo=BEIJING)
    ledger.deposit(Decimal("500.00"), start_time)

    t0_cfg = T0ExperimentConfig(
        enabled=True,
        initial_cash=Decimal("500.00"),
        max_total_exposure=Decimal("300.00"),
        max_single_exposure=Decimal("200.00"),
        min_cash_reserve=Decimal("100.00"),
        consecutive_losses_limit=3,
        max_daily_loss_ratio=Decimal("0.03"),
        daily_new_orders_limit=10,
        no_new_entry_time="14:50",
        whitelist=[ETF_ID, STOCK_ID],
    )
    risk_cfg = RiskConfig(
        max_position_weight=Decimal("0.60"),
        max_total_exposure=Decimal("0.80"),
        max_order_notional=Decimal("300.00"),
    )
    guard = Guard(
        repo=repo,
        ledger=ledger,
        risk=risk_cfg,
        mode="paper",
        universe=[ETF_ID, STOCK_ID],
        audit=audit,
        day_start_equity_provider=lambda: Decimal("500.00"),
        t0_config=t0_cfg,
    )
    yield db, repo, audit, ledger, guard, t0_cfg
    db.close()


def test_t0_etf_buy_and_same_day_partial_and_full_sell_loop(t0_oms_env):
    """Verify complete T+0 ETF loop: BUY -> same-day partial SELL -> same-day full SELL."""
    db, repo, audit, ledger, guard, t0_cfg = t0_oms_env
    cal = TradingCalendar.load()

    # Minute bars sequence:
    # 09:35 - BUY order submitted
    # 09:36 - BUY fills at 2.000
    # 10:00 - Partial SELL order submitted
    # 10:01 - Partial SELL fills at 2.050
    # 10:30 - Full exit SELL order submitted
    # 10:31 - Full exit SELL fills at 2.100
    b1 = make_min_bar(ETF_ID, datetime(2026, 10, 8, 9, 36, tzinfo=BEIJING), "2.000", "2.005", "1.995", "2.000")
    b2 = make_min_bar(ETF_ID, datetime(2026, 10, 8, 10, 1, tzinfo=BEIJING), "2.050", "2.055", "2.045", "2.050")
    b3 = make_min_bar(ETF_ID, datetime(2026, 10, 8, 10, 31, tzinfo=BEIJING), "2.100", "2.105", "2.095", "2.100")
    router = MockMinuteRouter([b1, b2, b3])
    broker = PaperBroker(router, cal, T0_FEES, PaperConfig(slippage_bps=0, fill_lag_bars=1))
    oms = OMS(repo, ledger, broker, audit, mode="paper")

    # Step 1: BUY 100 shares @ 2.000 (Notional = 200, within 200 single exposure limit)
    t_buy = datetime(2026, 10, 8, 9, 35, tzinfo=BEIJING)
    snap_buy = make_context_snapshot(ledger, ETF_ID, t_buy)
    buy_intent = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_buy,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-buy"),
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id=ETF_ID,
        action=TradeAction.BUY,
        target_weight=Decimal("0.40"),
        current_weight=Decimal("0.00"),
        valid_until=t_buy + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_buy, qty_buy = guard.check(buy_intent, snap_buy, reference_price=Decimal("2.000"), now=t_buy)
    assert guard_buy.verdict == GuardVerdict.ALLOW, (guard_buy.reason_codes, guard_buy.details)
    assert qty_buy == Decimal("100")

    order_buy = oms.submit(buy_intent, guard_buy, qty_buy, limit_price=Decimal("2.000"), now=t_buy)
    assert order_buy.status == OrderStatus.ACKNOWLEDGED

    # Poll fill at 09:36:59
    t_fill_buy = datetime(2026, 10, 8, 9, 36, 59, tzinfo=BEIJING)
    fill_buy = oms.poll_fill(order_buy.order_id, t_fill_buy)
    assert fill_buy is not None
    assert fill_buy.quantity == Decimal("100")
    assert fill_buy.price == Decimal("2.000")

    # Verify T+0 intraday availability: 100 shares must be IMMEDIATELY available on day 0!
    acc_after_buy = ledger.snapshot(t_fill_buy)
    assert len(acc_after_buy.positions) == 1
    pos = acc_after_buy.positions[0]
    assert pos.quantity == Decimal("100")
    assert pos.available_quantity == Decimal("100")  # T+0 available immediately!

    # Step 2: Intraday Partial SELL 50 shares @ 2.050 (target weight 0.20)
    # Note: A-share ETF lot size is 100 for buying, but selling allows exiting held shares
    # Let's do full exit of 100 shares directly or partial if lot size matches.
    # In profiles.py: ETF lot size is 100. Let's do full exit of 100 shares at 10:00!
    t_sell = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap_sell = make_context_snapshot(ledger, ETF_ID, t_sell)
    sell_intent = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_sell,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-sell"),
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id=ETF_ID,
        action=TradeAction.SELL,
        target_weight=Decimal("0.00"),
        current_weight=Decimal("0.40"),
        valid_until=t_sell + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_sell, qty_sell = guard.check(sell_intent, snap_sell, reference_price=Decimal("2.050"), now=t_sell)
    assert guard_sell.verdict == GuardVerdict.ALLOW
    assert qty_sell == Decimal("100")

    order_sell = oms.submit(sell_intent, guard_sell, qty_sell, limit_price=Decimal("2.050"), now=t_sell)
    assert order_sell.status == OrderStatus.ACKNOWLEDGED

    # Poll fill at 10:01:59
    t_fill_sell = datetime(2026, 10, 8, 10, 1, 59, tzinfo=BEIJING)
    fill_sell = oms.poll_fill(order_sell.order_id, t_fill_sell)
    assert fill_sell is not None
    assert fill_sell.quantity == Decimal("100")
    assert fill_sell.price == Decimal("2.050")

    # Verify position is closed, cash increased, realized gain captured
    acc_final = ledger.snapshot(t_fill_sell)
    assert len(acc_final.positions) == 0
    # Profit: (2.050 - 2.000) * 100 = 5.00 CNY gross gain minus small fees
    assert acc_final.cash > Decimal("500.00")
    assert acc_final.equity > Decimal("500.00")


def test_t1_stock_same_day_sell_rejected(t0_oms_env):
    """Verify that standard A-share stock (T+1) bought today cannot be sold today."""
    db, repo, audit, ledger, guard, t0_cfg = t0_oms_env
    t0_cfg.enabled = False  # Legacy T+1 account, outside the T0 ETF whitelist.
    cal = TradingCalendar.load()

    # Buy 100 shares of 600519.SH at 1.000
    b1 = make_min_bar(STOCK_ID, datetime(2026, 10, 8, 9, 36, tzinfo=BEIJING), "1.000", "1.005", "0.995", "1.000")
    router = MockMinuteRouter([b1])
    broker = PaperBroker(router, cal, T0_FEES, PaperConfig(slippage_bps=0, fill_lag_bars=1))
    oms = OMS(repo, ledger, broker, audit, mode="paper")

    t_buy = datetime(2026, 10, 8, 9, 35, tzinfo=BEIJING)
    snap_buy = make_context_snapshot(ledger, STOCK_ID, t_buy)
    buy_intent = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_buy,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-stock-buy"),
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id=STOCK_ID,
        action=TradeAction.BUY,
        target_weight=Decimal("0.20"),
        current_weight=Decimal("0.00"),
        valid_until=t_buy + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_buy, qty_buy = guard.check(buy_intent, snap_buy, reference_price=Decimal("1.000"), now=t_buy)
    assert guard_buy.verdict == GuardVerdict.ALLOW
    order_buy = oms.submit(buy_intent, guard_buy, qty_buy, limit_price=Decimal("1.000"), now=t_buy)
    fill_buy = oms.poll_fill(order_buy.order_id, datetime(2026, 10, 8, 9, 36, 59, tzinfo=BEIJING))
    assert fill_buy is not None

    # T+1 check: on day 0, quantity is 100 but available_quantity MUST be 0!
    acc = ledger.snapshot(datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING))
    pos = acc.positions[0]
    assert pos.quantity == Decimal("100")
    assert pos.available_quantity == Decimal("0")  # Locked for T+1!

    # Attempt same-day SELL order -> Guard MUST veto with sell_exceeds_available
    t_sell = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap_sell = make_context_snapshot(ledger, STOCK_ID, t_sell)
    sell_intent = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_sell,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-stock-sell"),
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id=STOCK_ID,
        action=TradeAction.SELL,
        target_weight=Decimal("0.00"),
        current_weight=Decimal("0.20"),
        valid_until=t_sell + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_sell, _ = guard.check(sell_intent, snap_sell, reference_price=Decimal("1.000"), now=t_sell)
    assert guard_sell.verdict == GuardVerdict.VETO
    assert "sell_exceeds_available" in guard_sell.reason_codes


def test_oms_anti_lookahead_and_missing_quote(t0_oms_env):
    """Verify that orders never fill on bars that occurred prior to submission."""
    db, repo, audit, ledger, guard, t0_cfg = t0_oms_env
    cal = TradingCalendar.load()

    # Minute bar is at 09:30, order submitted at 09:35 -> cannot fill on 09:30 bar!
    b_past = make_min_bar(ETF_ID, datetime(2026, 10, 8, 9, 30, tzinfo=BEIJING), "2.000", "2.005", "1.995", "2.000")
    router = MockMinuteRouter([b_past])
    broker = PaperBroker(router, cal, T0_FEES, PaperConfig(slippage_bps=0, fill_lag_bars=1))
    oms = OMS(repo, ledger, broker, audit, mode="paper")

    t_order = datetime(2026, 10, 8, 9, 35, tzinfo=BEIJING)
    snap = make_context_snapshot(ledger, ETF_ID, t_order)
    intent = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_order,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-past"),
        account_id=ledger.account_id,
        portfolio_version=ledger.version,
        instrument_id=ETF_ID,
        action=TradeAction.BUY,
        target_weight=Decimal("0.40"),
        current_weight=Decimal("0.00"),
        valid_until=t_order + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_res, qty = guard.check(intent, snap, reference_price=Decimal("2.000"), now=t_order)
    order = oms.submit(intent, guard_res, qty, limit_price=Decimal("2.000"), now=t_order)

    # Poll fill at 09:36 -> no eligible future bar exists -> fill must be None
    fill = oms.poll_fill(order.order_id, datetime(2026, 10, 8, 9, 36, tzinfo=BEIJING))
    o = repo.get_order_by_client_id(order.client_order_id)
    assert o["status"] == OrderStatus.ACKNOWLEDGED.value


def test_oms_rejects_shadow_or_live_mode_and_cross_account(t0_oms_env):
    """Verify that OMS strictly blocks shadow/live modes and mismatched accounts."""
    db, repo, audit, ledger, guard, t0_cfg = t0_oms_env
    cal = TradingCalendar.load()
    router = MockMinuteRouter([])
    broker = PaperBroker(router, cal, T0_FEES)
    oms = OMS(repo, ledger, broker, audit, mode="paper")

    t_now = datetime(2026, 10, 8, 10, 0, tzinfo=BEIJING)
    snap = make_context_snapshot(ledger, ETF_ID, t_now)

    # Cross-account mismatch
    intent_wrong_acc = PortfolioIntent(
        id=new_id("pi"),
        trace_id=new_id("tr"),
        created_at=t_now,
        intent_id=new_id("pi"),
        decision_id=new_id("dec-cross"),
        account_id="other-account",
        portfolio_version=ledger.version,
        instrument_id=ETF_ID,
        action=TradeAction.BUY,
        target_weight=Decimal("0.20"),
        current_weight=Decimal("0.00"),
        valid_until=t_now + timedelta(minutes=10),
        mode=RunMode.PAPER,
    )
    guard_res, qty = guard.check(intent_wrong_acc, snap, reference_price=Decimal("2.000"), now=t_now)
    with pytest.raises(RecoveryRequired, match="order differs from Guard authorization"):
        oms.submit(intent_wrong_acc, guard_res, qty, limit_price=Decimal("2.000"), now=t_now)
# -- TASK-20261016-07: execution must honour router quality refusals (Issue16-04) --

def test_broker_refuses_quality_rejected_window(tmp_path):
    """Actual router (diverging sources, cross-check on) → PaperBroker: a window
    the router refused must not fill, and must fill again once sources agree."""
    import sys
    from datetime import timedelta
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
    from test_minute_source_routing import FakeSource, bar as mk_bar, policy, router_with

    base = datetime(2026, 10, 8, 10, 1, tzinfo=BEIJING)
    submitted = base - timedelta(minutes=1)
    probe = base + timedelta(seconds=3)
    good = [mk_bar(0, close="1.500", provider="sina", at=base)]
    skew = [mk_bar(0, close="1.600", provider="ak", at=base)]
    primary, other = FakeSource("primary", bars=list(good)), FakeSource("other", bars=list(skew))
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=50, cross_check_tolerance_ratio="0.001"))
    router.get_minute_bars(ETF_ID, submitted - timedelta(days=2), probe, pref=["primary", "other"])
    assert router._divergence.get(ETF_ID) is not None  # standing refusal recorded

    broker = PaperBroker(router, TradingCalendar.load(), T0_FEES, PaperConfig(minute_only=True))
    order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=submitted,
                  order_id=new_id("o"), client_order_id=new_id("cli"),
                  intent_id=new_id("i"), guard_id=new_id("g"),
                  account_id="paper-t0", instrument_id=ETF_ID,
                  mode=RunMode.PAPER, side=OrderSide.BUY, quantity="100",
                  limit_price="1.500", updated_at=submitted)
    assert broker.try_fill(order, probe) is None  # refused window: no fill, no price minted

    other.bars = [mk_bar(0, close="1.500", provider="ak", at=base)]  # sources agree again
    fill = broker.try_fill(order, probe)
    assert fill is not None and fill.price <= Decimal("1.500")  # refusal lifted, fills normally


def test_oms_poll_fill_refuses_quality_rejected_window(tmp_path):
    """router→PaperBroker→OMS integration: while the window is quality-rejected,
    poll_fill produces no fill and cash/freeze/positions stay untouched."""
    import sys
    from datetime import timedelta
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'tests'))
    from test_minute_source_routing import FakeSource, bar as mk_bar, policy, router_with

    base = datetime(2026, 10, 8, 10, 1, tzinfo=BEIJING)
    submitted = base - timedelta(minutes=1)
    probe = base + timedelta(seconds=3)
    good = [mk_bar(0, close="1.500", provider="sina", at=base)]
    skew = [mk_bar(0, close="1.600", provider="ak", at=base)]
    primary, other = FakeSource("primary", bars=list(good)), FakeSource("other", bars=list(skew))
    router, _ = router_with(primary, other, pol=policy(
        cross_check_enabled=True, cross_check_interval_seconds=0.0,
        cross_check_max_daily=50, cross_check_tolerance_ratio="0.001"))
    router.get_minute_bars(ETF_ID, submitted - timedelta(days=2), probe, pref=["primary", "other"])

    db = Database(f"sqlite:///{tmp_path / 'oms_q.db'}")
    repo, audit = Repository(db), AuditSink(db)
    ledger = Ledger(db, "paper-t0", T0_FEES)
    ledger.deposit(Decimal("1000"), submitted)
    oms = OMS(repo, ledger, PaperBroker(router, TradingCalendar.load(), T0_FEES,
                                        PaperConfig(minute_only=True)), audit)
    order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=submitted,
                  order_id=new_id("o"), client_order_id=new_id("cli"),
                  intent_id=new_id("i"), guard_id=new_id("g"),
                  account_id="paper-t0", instrument_id=ETF_ID,
                  mode=RunMode.PAPER, side=OrderSide.BUY, quantity="100",
                  limit_price="1.500", updated_at=submitted)
    order.status = OrderStatus.ACKNOWLEDGED
    repo.save_order(order)
    cash_before = ledger.snapshot(probe).cash

    assert oms.poll_fill(order.order_id, probe) is None  # quality-refused: nothing happens
    assert ledger.snapshot(probe).cash == cash_before
    assert not ledger.snapshot(probe).positions
    assert repo.open_orders()[0]["status"] == "ACKNOWLEDGED"  # order survives, not silently cancelled

    other.bars = [mk_bar(0, close="1.500", provider="ak", at=base)]
    fill = oms.poll_fill(order.order_id, probe)
    assert fill is not None  # agreeing window fills through the normal path
    snap = ledger.snapshot(probe)
    assert snap.cash < cash_before and len(snap.positions) == 1
    assert repo.open_orders() == []  # fully filled: no longer open
    db.close()


# -- TASK-20261016-09: intraday order lifecycle (ARCH-021 / Issue16-06) --------

def _ack_order(intent_id="i-day", side=OrderSide.BUY, created=None, qty="100"):
    created = created or datetime(2026, 10, 12, 10, 0, tzinfo=BEIJING)
    return Order(id=new_id("o"), trace_id=new_id("t"), created_at=created,
                 order_id=new_id("o"), client_order_id=new_id("cli"),
                 intent_id=intent_id, guard_id=new_id("g"),
                 account_id="paper-t0-500", instrument_id=ETF_ID,
                 mode=RunMode.PAPER, side=side, quantity=qty,
                 limit_price="1.50", updated_at=created,
                 status=OrderStatus.ACKNOWLEDGED)


def _day2_env(tmp_path):
    """OMS with a minute router that never matches (no fills happen)."""
    db = Database(f"sqlite:///{tmp_path / 'lc.db'}")
    repo, audit = Repository(db), AuditSink(db)
    ledger = Ledger(db, "paper-t0-500", T0_FEES)
    ledger.deposit(Decimal("1000"), datetime(2026, 10, 12, 9, 30, tzinfo=BEIJING))
    empty_router = MockMinuteRouter([])
    oms = OMS(repo, ledger, PaperBroker(empty_router, TradingCalendar.load(),
                                        T0_FEES, PaperConfig(minute_only=True)), audit)
    return db, repo, ledger, oms


def test_day_order_buy_cannot_fill_next_day(tmp_path):
    """The Issue16-06 repro, fixed: a day-1 unfilled BUY is expired before the
    day-2 fill attempt; funds release and the order is CANCELLED."""
    db, repo, ledger, oms = _day2_env(tmp_path)
    order = _ack_order(intent_id="i-1")
    repo.save_order(order)
    ledger.reserve("pending-i-1", Decimal("160"), order.created_at)  # OMS freeze key convention
    frozen_before = ledger.snapshot(order.created_at).reserved_cash
    assert frozen_before > 0
    day2 = datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING)
    expired = oms.expire_stale_orders(day2, "14:50")
    assert order.order_id in expired
    stored = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (order.order_id,))[0]["payload_json"])
    assert stored.status == OrderStatus.CANCELLED
    assert ledger.snapshot(day2).reserved_cash == Decimal("0")  # freeze released
    # Task17-03 finding pinned: the release event uses the OPERATING clock
    # (day2), never wall-clock utcnow — no future-dated account events.
    releases = [e for e in ledger.repo.account_events("paper-t0-500") if e["kind"] == "release"]
    assert releases and releases[-1]["effective_at"].startswith("2026-10-13T10:00:00+08:00")
    assert "2026-10-17" not in releases[-1]["effective_at"]  # never wall-clock
    assert oms.poll_fill(order.order_id, day2) is None  # cancelled orders never fill
    db.close()


def test_eob_buy_withdrawn_after_no_new_entry_cutoff(tmp_path):
    db, repo, ledger, oms = _day2_env(tmp_path)
    order = _ack_order(intent_id="i-2", created=datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING))
    repo.save_order(order)
    cutoff_time = datetime(2026, 10, 13, 14, 51, tzinfo=BEIJING)
    assert oms.expire_stale_orders(datetime(2026, 10, 13, 14, 0, tzinfo=BEIJING), "14:50") == []
    assert order.order_id in oms.expire_stale_orders(cutoff_time, "14:50")
    stored = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (order.order_id,))[0]["payload_json"])
    assert stored.status == OrderStatus.CANCELLED
    db.close()


def test_sell_survives_day_rollover_and_cutoff(tmp_path):
    db, repo, ledger, oms = _day2_env(tmp_path)
    sell = _ack_order(intent_id="i-3", side=OrderSide.SELL,
                      created=datetime(2026, 10, 12, 14, 55, tzinfo=BEIJING))
    repo.save_order(sell)
    day2 = datetime(2026, 10, 13, 14, 51, tzinfo=BEIJING)
    assert oms.expire_stale_orders(day2, "14:50") == []  # risk-reducing SELL is never auto-cancelled
    stored = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (sell.order_id,))[0]["payload_json"])
    assert stored.status == OrderStatus.ACKNOWLEDGED
    db.close()


def test_expire_skips_unknown_and_is_idempotent(tmp_path):
    db, repo, ledger, oms = _day2_env(tmp_path)
    unknown = _ack_order(intent_id="i-4")
    unknown.status = OrderStatus.UNKNOWN
    repo.save_order(unknown)
    live = _ack_order(intent_id="i-5")
    repo.save_order(live)
    day2 = datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING)
    expired = oms.expire_stale_orders(day2, "14:50")
    assert live.order_id in expired and unknown.order_id not in expired
    assert Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (unknown.order_id,))[0]["payload_json"]
    ).status == OrderStatus.UNKNOWN  # recovery gate untouched
    assert oms.expire_stale_orders(day2, "14:50") == []  # idempotent: nothing left to expire
    db.close()


def test_partially_filled_buy_releases_remaining_freeze(tmp_path):
    db, repo, ledger, oms = _day2_env(tmp_path)
    order = _ack_order(intent_id="i-6", qty="200")
    repo.save_order(order)
    ledger.reserve("pending-i-6", Decimal("320"), order.created_at)
    partial = Fill(id=new_id("f"), trace_id=order.trace_id, created_at=order.created_at,
                   fill_id=new_id("f"), order_id=order.order_id,
                   provider_fill_id="pf-partial", quantity=Decimal("100"),
                   price=Decimal("1.50"), fee=Decimal("0.1"),
                   executed_at=order.created_at, available_at=order.created_at)
    assert repo.save_fill(partial)
    ledger.apply_fill(partial, order, partial.executed_at)
    order.filled_quantity = Decimal("100")
    order.average_fill_price = Decimal("1.50")
    order.status = OrderStatus.PARTIALLY_FILLED
    repo.update_order(order)
    assert ledger.snapshot(order.created_at).reserved_cash > 0
    day2 = datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING)
    assert order.order_id in oms.expire_stale_orders(day2, "14:50")
    assert ledger.snapshot(day2).reserved_cash == Decimal("0")  # remainder released
    snap = ledger.snapshot(day2)
    assert snap.positions[0].quantity == Decimal("100")  # filled part kept
    db.close()


def test_same_day_including_lunch_break_stays_working(tmp_path):
    db, repo, ledger, oms = _day2_env(tmp_path)
    order = _ack_order(intent_id="i-7", created=datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING))
    repo.save_order(order)
    after_lunch = datetime(2026, 10, 13, 13, 5, tzinfo=BEIJING)  # same Beijing day
    assert oms.expire_stale_orders(after_lunch, "14:50") == []
    stored = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (order.order_id,))[0]["payload_json"])
    assert stored.status == OrderStatus.ACKNOWLEDGED
    db.close()


# -- TASK-20261017-08: OMS lifecycle account isolation (Issue17-01) -------------

def test_oms_lifecycle_rejects_foreign_accounts(t0_oms_env):
    """The three Issue17-01 counterexamples, now rejections with zero side
    effects: A's OMS must never cancel/recover/fill B's orders."""
    from trademind.errors import RecoveryRequired
    db, repo, audit, ledger_a, guard, t0_cfg = t0_oms_env
    ledger_b = Ledger(db, "other-paper", T0_FEES)
    ledger_b.deposit(Decimal("1000"), datetime(2026, 10, 8, 9, 30, tzinfo=BEIJING))
    broker = PaperBroker(MockMinuteRouter([]), TradingCalendar.load(), T0_FEES,
                         PaperConfig(minute_only=True))
    oms_a = OMS(repo, ledger_a, broker, audit, mode="paper")

    b_order = _ack_order(intent_id="b-1")
    b_order.account_id = "other-paper"  # genuinely foreign
    repo.save_order(b_order)
    ledger_b.reserve("pending-b-1", Decimal("160"), b_order.created_at)
    b_order_id = b_order.order_id

    # 1. cancel foreign order → loud rejection, zero side effects
    with pytest.raises(RecoveryRequired, match="belongs to account"):
        oms_a.cancel(b_order_id)
    stored = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (b_order_id,))[0]["payload_json"])
    assert stored.status == OrderStatus.ACKNOWLEDGED  # not cancelled
    assert ledger_b.snapshot(stored.created_at).reserved_cash == Decimal("160")  # freeze intact

    # 2. recover must not touch foreign SUBMITTING orders
    b_sub = _ack_order(intent_id="b-2")
    b_sub.account_id = "other-paper"
    b_sub.status = OrderStatus.SUBMITTING
    repo.save_order(b_sub)
    report = oms_a.recover()
    b_after = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (b_sub.order_id,))[0]["payload_json"])
    assert b_after.status == OrderStatus.SUBMITTING  # untouched by foreign recovery
    assert report["unknown"] == 0

    # 3. poll_fill foreign order → rejection; A's cash/positions untouched
    b_buy = _ack_order(intent_id="b-3")
    b_buy.account_id = "other-paper"
    repo.save_order(b_buy)
    cash_a_before = ledger_a.snapshot(b_order.created_at).cash
    with pytest.raises(RecoveryRequired, match="belongs to account"):
        oms_a.poll_fill(b_buy.order_id, b_buy.created_at + timedelta(minutes=1))
    assert ledger_a.snapshot(b_buy.created_at + timedelta(minutes=1)).cash == cash_a_before
    assert not ledger_a.snapshot(b_buy.created_at + timedelta(minutes=1)).positions
    b3_after = Order.model_validate_json(repo.db.query(
        "SELECT payload_json FROM orders WHERE order_id=?", (b_buy.order_id,))[0]["payload_json"])
    assert b3_after.status == OrderStatus.ACKNOWLEDGED  # no phantom fill

    # ledger defense: a fill for a foreign order is refused outright
    fill = Fill(id=new_id("f"), trace_id="t", created_at=b_buy.created_at,
                fill_id=new_id("f"), order_id=b_buy.order_id,
                provider_fill_id="pf-x", quantity=Decimal("100"),
                price=Decimal("1.50"), fee=Decimal("0"),
                executed_at=b_buy.created_at, available_at=b_buy.created_at)
    with pytest.raises(ValueError, match="does not match ledger"):
        ledger_a.apply_fill(fill, b_buy, b_buy.created_at)
    assert ledger_a.snapshot(b_buy.created_at).cash == cash_a_before


def test_own_account_recovery_and_submit_not_blocked_by_foreign(t0_oms_env):
    """Own-account lifecycle works unchanged while foreign orders exist: same
    instrument and foreign UNKNOWN do not block; own UNKNOWN still blocks."""
    from trademind.domain.intents import PortfolioIntent
    from trademind.domain.enums import GuardVerdict, RunMode, TradeAction
    db, repo, audit, ledger_a, guard, t0_cfg = t0_oms_env
    ledger_b = Ledger(db, "other-paper", T0_FEES)
    ledger_b.deposit(Decimal("1000"), datetime(2026, 10, 8, 9, 30, tzinfo=BEIJING))
    broker = PaperBroker(MockMinuteRouter([]), TradingCalendar.load(), T0_FEES,
                         PaperConfig(minute_only=True))
    oms_a = OMS(repo, ledger_a, broker, audit, mode="paper")

    # foreign orders: same instrument ACK + foreign UNKNOWN
    b_same = _ack_order(intent_id="b-same")  # foreign, same instrument, open
    b_same.account_id = "other-paper"
    repo.save_order(b_same)
    b_unknown = _ack_order(intent_id="b-unknown")
    b_unknown.account_id = "other-paper"
    b_unknown.status = OrderStatus.UNKNOWN
    repo.save_order(b_unknown)

    t_buy = datetime(2026, 10, 8, 9, 35, tzinfo=BEIJING)
    snap = make_context_snapshot(ledger_a, ETF_ID, t_buy)
    intent = PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("tr"), created_at=t_buy,
        intent_id=new_id("pi"), decision_id=new_id("dec"), account_id=ledger_a.account_id,
        portfolio_version=ledger_a.version, instrument_id=ETF_ID,
        action=TradeAction.BUY, target_weight=Decimal("0.40"), current_weight=Decimal("0.00"),
        valid_until=t_buy + timedelta(minutes=10), mode=RunMode.PAPER)
    guard_r, qty = guard.check(intent, snap, reference_price=Decimal("2.000"), now=t_buy)
    assert guard_r.verdict == GuardVerdict.ALLOW, (guard_r.verdict, guard_r.reason_codes, qty)
    order = oms_a.submit(intent, guard_r, qty, limit_price=Decimal("2.000"), now=t_buy)
    assert order.status == OrderStatus.ACKNOWLEDGED  # foreign same-instrument/UNKNOWN did not block

    # own UNKNOWN still blocks the next submit
    own_unknown = _ack_order(intent_id="a-unknown")
    own_unknown.status = OrderStatus.UNKNOWN
    repo.save_order(own_unknown)
    intent2 = intent.model_copy(update={"intent_id": new_id("pi"), "decision_id": new_id("dec2")})
    guard2, qty2 = guard.check(intent2, snap, reference_price=Decimal("2.000"), now=t_buy)
    if guard2.verdict == GuardVerdict.ALLOW:
        with pytest.raises(RecoveryRequired, match="execution blocked"):
            oms_a.submit(intent2, guard2, qty2, limit_price=Decimal("2.000"), now=t_buy)


# -- TASK-20261017-09: one fee number across Fill/event/cash (Issue17-02) -------

def test_fill_fee_consistent_across_fill_storage_event_and_cash(tmp_path):
    """Issue17-02 repro, fixed: real broker+OMS fill — the returned Fill, the
    stored fill row, the ledger event note and the cash delta all carry the
    same fee; no double charge on duplicate provider_fill_id."""
    db, repo, ledger, oms = _day2_env(tmp_path)
    bar = make_min_bar(ETF_ID, datetime(2026, 10, 12, 10, 1, tzinfo=BEIJING),
                       "1.50", "1.50", "1.50", "1.50")
    oms.broker.router = MockMinuteRouter([bar])
    order = _ack_order(intent_id="i-fee")
    repo.save_order(order)
    t_poll = datetime(2026, 10, 12, 10, 1, 59, tzinfo=BEIJING)
    fill = oms.poll_fill(order.order_id, t_poll)
    assert fill is not None
    # event note is the ledger truth
    ev = [e for e in ledger.repo.account_events("paper-t0-500") if e["kind"] == "fill_buy"][-1]
    fee_note = Decimal(ev["note"].split("fee=")[1].split(";")[0])
    # returned Fill, stored Fill row and cash delta all agree with the event
    assert fill.fee == fee_note
    stored = repo.fills_for_order(order.order_id)
    assert len(stored) == 1 and Decimal(stored[0]["fee"]) == fee_note
    assert ledger.snapshot(t_poll).cash == Decimal("1000") - Decimal("150") - fee_note
    # duplicate provider_fill_id: no second charge, no second event
    assert oms.poll_fill(order.order_id, t_poll) is None
    assert ledger.snapshot(t_poll).cash == Decimal("1000") - Decimal("150") - fee_note
    ev_count = len([e for e in ledger.repo.account_events("paper-t0-500") if e["kind"] == "fill_buy"])
    assert ev_count == 1
    db.close()


def test_sell_fill_fee_includes_stamp_duty_and_stays_consistent(tmp_path):
    """SELL fee semantics (stamp duty) flow into Fill/event/cash identically."""
    db, repo, ledger, oms = _day2_env(tmp_path)
    # hold 100 shares first (T0 immediately available)
    bar = make_min_bar(ETF_ID, datetime(2026, 10, 12, 10, 1, tzinfo=BEIJING),
                       "1.50", "1.50", "1.50", "1.50")
    oms.broker.router = MockMinuteRouter([bar])
    buy = _ack_order(intent_id="i-fb")
    repo.save_order(buy)
    oms.poll_fill(buy.order_id, datetime(2026, 10, 12, 10, 1, 59, tzinfo=BEIJING))
    # SELL 100 @ 1.50
    sell = _ack_order(intent_id="i-fs", side=OrderSide.SELL)
    repo.save_order(sell)
    sell_fill = oms.poll_fill(sell.order_id, datetime(2026, 10, 12, 10, 2, 59, tzinfo=BEIJING))
    assert sell_fill is not None
    ev = [e for e in ledger.repo.account_events("paper-t0-500") if e["kind"] == "fill_sell"][-1]
    fee_note = Decimal(ev["note"].split("fee=")[1].split(";")[0])
    assert sell_fill.fee == fee_note
    stored = repo.fills_for_order(sell.order_id)
    assert Decimal(stored[0]["fee"]) == fee_note
    assert ledger.snapshot(datetime(2026, 10, 12, 10, 2, 59, tzinfo=BEIJING)).positions == []
    db.close()
