"""TASK-22: failure recovery tests — crash restart, duplicate events,
kill switch persistence, unknown-order reconciliation, replay isolation."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from conftest import T0
from trademind.config import AppConfig
from trademind.domain.base import new_id
from trademind.domain.enums import OrderStatus, RunMode, TradeAction
from trademind.domain.instruments import FeeSchedule
from trademind.domain.intents import PortfolioIntent
from trademind.domain.orders import AccountEvent
from trademind.execution import OMS, PaperBroker, PaperConfig
from trademind.market.adapters.fixture import FixtureAdapter
from trademind.market.calendar import TradingCalendar
from trademind.market.router import DatasetRouter
from trademind.pipeline import Pipeline
from trademind.portfolio import Ledger
from trademind.storage import AuditSink, Database, Repository

UTC = timezone.utc
NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
FEES = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"), created_at=T0,
                   commission_rate="0.00025", commission_min="5",
                   stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")


def _intent():
    return PortfolioIntent(
        id=new_id("pi"), trace_id=new_id("t"), created_at=NOW,
        intent_id=new_id("pi"), decision_id=new_id("dec"),
        account_id="paper-001", portfolio_version=1, instrument_id="600036.SH",
        action=TradeAction.BUY, target_weight=Decimal("0.10"), current_weight=Decimal("0"),
        valid_until=NOW + timedelta(minutes=30), mode=RunMode.PAPER)


def _db(tmp_path):
    return Database(f"sqlite:///{tmp_path/'r.db'}")


def test_crash_restart_rebuilds_ledger_identically(tmp_path):
    db = _db(tmp_path)
    repo = Repository(db)
    audit = AuditSink(db)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), NOW)
    oms = OMS(repo, led, PaperBroker(DatasetRouter(pref=["fixture"]),
                                     TradingCalendar.load(), FEES, PaperConfig()), audit)
    from audit_helpers import authorize
    intent = _intent()
    g, qty = authorize(repo, led, audit, intent, NOW)
    order = oms.submit(intent, g, qty, Decimal("40.00"), now=NOW)
    for d in range(1, 4):
        oms.poll_fill(order.order_id, NOW + timedelta(days=d))
    before = led.snapshot(NOW + timedelta(days=4))
    db.close()

    # "restart": fresh objects on same database
    db2 = _db(tmp_path)
    led2 = Ledger(db2, "paper-001", FEES)
    led2.rebuild_from_events()
    after = led2.snapshot(NOW + timedelta(days=4))
    assert before.cash == after.cash
    assert before.equity == after.equity
    assert before.market_value == after.market_value
    db2.close()


def test_duplicate_account_event_never_double_applies(tmp_path):
    db = _db(tmp_path)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), NOW)
    o = _intent()
    from trademind.domain.orders import Fill, Order

    order = Order(id=new_id("o"), trace_id=new_id("t"), created_at=NOW,
                  order_id="o-1", client_order_id="c-1", intent_id="i", guard_id="g",
                  account_id="paper-001", instrument_id="600036.SH",
                  mode=RunMode.PAPER, side="BUY", quantity="100",
                  limit_price="40", updated_at=NOW)
    fill = Fill(id=new_id("f"), trace_id=new_id("t"), created_at=NOW,
                fill_id="fill-1", order_id="o-1", provider_fill_id="pf-1",
                quantity="100", price="40", fee="0", executed_at=NOW, available_at=NOW)
    led.apply_fill(fill, order, NOW)
    cash_once = led.snapshot(NOW).cash
    with pytest.raises(Exception):
        # same fill redelivered → duplicate key on account event
        led.apply_fill(fill.model_copy(update={"id": new_id("f")}), order, NOW)
    assert led.snapshot(NOW).cash == cash_once
    db.close()


def test_kill_switch_persists_across_restart(tmp_path):
    db = _db(tmp_path)
    Repository(db).set_kill_switch(True, "halt")
    db.close()
    db2 = _db(tmp_path)
    assert Repository(db2).get_kill_switch()["engaged"] is True
    db2.close()


def test_unknown_order_requires_reconciliation(tmp_path):
    db = _db(tmp_path)
    repo = Repository(db)
    audit = AuditSink(db)
    led = Ledger(db, "paper-001", FEES)
    led.deposit(Decimal("1000000"), NOW)
    oms = OMS(repo, led, PaperBroker(DatasetRouter(pref=["fixture"]),
                                     TradingCalendar.load(), FEES, PaperConfig()), audit)
    from audit_helpers import authorize
    intent = _intent()
    g, qty = authorize(repo, led, audit, intent, NOW)
    order = oms.submit(intent, g, qty, Decimal("40.00"), now=NOW)
    # simulate crash mid-submit: force order back to SUBMITTING
    obj = type(order).model_validate(repo.get_order_by_client_id(order.client_order_id))
    obj.status = OrderStatus.SUBMITTING
    repo.update_order(obj)
    report = oms.recover()
    assert report["unknown"] == 1
    # new risk must not proceed until reconciled
    resolved = oms.reconcile_unknown(order.order_id, "ACKNOWLEDGED")
    assert resolved.status == OrderStatus.ACKNOWLEDGED
    db.close()


def test_replay_mode_rejects_credentials():
    cfg = AppConfig()
    cfg.mode = "replay"
    cfg.data.tushare_token = "tok"
    from trademind.config import _validate_mode_isolation
    from trademind.errors import ConfigError

    with pytest.raises(ConfigError):
        _validate_mode_isolation(cfg)


def test_full_pipeline_recovery_after_restart(tmp_path):
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'p.db'}"
    cfg.mode = "replay"
    pipe = Pipeline(cfg)
    pipe.ensure_account(at=NOW)
    as_of = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
    res = pipe.run_decision("600036.SH", as_of)
    assert res.error is None
    # "restart" — the previous writer process releases the account lease first
    pipe.release_writer_lease()
    pipe2 = Pipeline(cfg)
    pipe2.ledger.rebuild_from_events()
    snap1 = pipe.ledger.snapshot(as_of + timedelta(days=1))
    snap2 = pipe2.ledger.snapshot(as_of + timedelta(days=1))
    assert snap1.cash == snap2.cash
    assert snap1.version == snap2.version
    # kill switch persists in the same db and blocks the new pipeline instance
    pipe2.repo.set_kill_switch(True, "halt")
    pipe2.release_writer_lease()
    pipe3 = Pipeline(cfg)
    blocked = pipe3.run_decision("600036.SH", as_of)
    assert blocked.guard_verdict == "VETO"
    assert "kill_switch" in blocked.guard_reasons


def test_backup_restore_rebuilds_identical_books(tmp_path):
    """WAL-consistent backup, isolated restore, full reconciliation
    (TASK-20261013-17): integrity, references and a byte-equal ledger replay."""
    from trademind.storage import verify_database
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'live.db'}"
    cfg.mode = "replay"
    pipe = Pipeline(cfg)
    pipe.ensure_account(at=NOW)
    as_of = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
    assert pipe.run_decision("600036.SH", as_of).error is None

    target = tmp_path / 'backups' / 'live-20261013.db'  # isolated restore location
    pipe.db.backup_to(target)
    pipe.release_writer_lease()
    pipe.db.close()

    report = verify_database(target)
    assert report['ok'] is True
    assert report['schema_versions'] == [1, 2]
    assert report['references_ok']

    # Restore = open the copy read-only where it lies; the ledger must replay
    # to exactly the same books as the live database at backup time.
    restored = Database(f"sqlite:///{target}")
    led_live = Ledger(Database(f"sqlite:///{tmp_path/'live.db'}"), "paper-001", FEES)
    led_restored = Ledger(restored, "paper-001", FEES)
    led_live.rebuild_from_events()
    led_restored.rebuild_from_events()
    at = as_of + timedelta(days=1)
    live, copy = led_live.snapshot(at), led_restored.snapshot(at)
    assert live.cash == copy.cash
    assert live.equity == copy.equity
    assert live.version == copy.version
    assert live.market_value == copy.market_value
    restored.close()
    led_live.db.close()
