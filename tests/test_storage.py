"""TASK-07 storage tests: migrations, idempotency, immutability, audit."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from conftest import T0, legal_evidence

from trademind.domain.orders import AccountEvent, Fill
from trademind.domain.base import new_id
from trademind.errors import DuplicateKey, SchemaVersionError
from trademind.storage import (AuditSink, CriticalAuditFailure, Database,
                               Repository, verify_database)
from trademind.storage import db as db_module


@pytest.fixture()
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path/'t.db'}")
    yield d
    d.close()


def test_migration_idempotent(db):
    v1 = db.applied_migrations()
    db.migrate()
    db.migrate()
    assert db.applied_migrations() == v1
    assert max(db.applied_migrations()) == db_module.LATEST_SCHEMA_VERSION


# -- ISSUE-20261013-09: version lifecycle (TASK-20261013-17) -------------------

def test_future_schema_version_refused_untouched(tmp_path):
    p = tmp_path/'future.db'
    d = Database(f'sqlite:///{p}')
    d.close()
    raw = sqlite3.connect(str(p))
    raw.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (99, 'x')")
    raw.commit()
    before_master = sorted(raw.execute("SELECT type,name FROM sqlite_master").fetchall())
    before_intents = raw.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
    raw.close()
    with pytest.raises(SchemaVersionError, match='newer program'):
        Database(f'sqlite:///{p}')
    # The refusal must leave bytes and structure exactly as they were.
    raw = sqlite3.connect(str(p))
    assert sorted(raw.execute("SELECT type,name FROM sqlite_master").fetchall()) == before_master
    assert raw.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == before_intents
    assert [r[0] for r in raw.execute("SELECT version FROM schema_migrations")] == [1, 2, 99]
    raw.close()


def test_v1_database_upgrades_in_place_with_backfill(tmp_path):
    p = tmp_path/'v1.db'
    d = Database(f'sqlite:///{p}')  # creates v2 directly; hand-roll back to v1 shape
    raw = sqlite3.connect(str(p))
    raw.execute("INSERT INTO intents (decision_id, intent_version, instrument_id, action, "
                "valid_until, status, payload_json) VALUES ('dec-1', 1, '513050.SH', 'BUY', "
                "'2026-01-01T00:00:00+00:00', 'SAVED', "
                "('{\"decision_id\":\"dec-1\",\"created_at\":\"2026-01-01T09:30:00+00:00\"}'))")
    for idx in ('ix_intents_created', 'ix_calls_created', 'ix_ctx_asof', 'ix_plan_hist_acct_avail'):
        raw.execute(f'DROP INDEX {idx}')
    raw.execute('ALTER TABLE intents DROP COLUMN created_at')
    raw.execute('DELETE FROM schema_migrations WHERE version=2')
    raw.commit()
    raw.close()
    d.close()
    reopened = Database(f'sqlite:///{p}')
    assert reopened.applied_migrations() == [1, 2]
    row = reopened.query("SELECT created_at, payload_json FROM intents WHERE decision_id='dec-1'")[0]
    assert row['created_at'] == '2026-01-01T09:30:00+00:00'  # backfilled from payload
    reopened.close()


def test_failed_migration_rolls_back_and_recovers(tmp_path, monkeypatch):
    p = tmp_path/'probe.db'
    with monkeypatch.context() as m:
        m.setattr(db_module, 'MIGRATIONS',
                  {2: ("CREATE TABLE migration_probe (a INT)", "THIS IS NOT SQL")})
        with pytest.raises(sqlite3.OperationalError):
            Database(f'sqlite:///{p}')
    # Reopen with the real registry: the failed migration left no stamp and
    # no partial structure, and the database upgrades normally.
    reopened = Database(f'sqlite:///{p}')
    assert reopened.applied_migrations() == [1, 2]
    assert 'migration_probe' not in {r[0] for r in reopened.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    reopened.close()


def test_save_intent_records_created_at(db):
    from conftest import legal_intent
    repo = Repository(db)
    intent = legal_intent()
    repo.save_intent(intent, 'SAVED')
    row = db.query('SELECT created_at FROM intents WHERE decision_id=?', (intent.decision_id,))[0]
    assert row['created_at'] == T0.isoformat()


def test_backup_roundtrip_and_verify(tmp_path, db):
    repo = Repository(db)
    repo.set_kill_switch(True, 'halt')
    target = tmp_path/'backup.db'
    db.backup_to(target)
    assert target.exists()
    with pytest.raises(FileExistsError):
        db.backup_to(target)  # never overwrite a backup silently
    report = verify_database(target)
    assert report['ok'] is True
    assert report['integrity_ok'] and report['references_ok']
    assert report['schema_versions'] == [1, 2]
    assert Repository(Database(f'sqlite:///{target}')).get_kill_switch()['engaged'] is True
    # A corrupted copy is reported, not silently passed.
    corrupted = tmp_path/'corrupt.db'
    corrupted.write_bytes(target.read_bytes()[:len(target.read_bytes()) // 2])
    assert verify_database(corrupted)['ok'] is False


# -- ISSUE-20261013-09 reopen: refuse before any write; financial reconciliation

def test_future_delete_mode_database_refused_bytes_unchanged(tmp_path):
    """The Issue's repro: a DELETE-journal database stamped v99 is refused
    with byte-identical content and no -wal/-shm side files."""
    p = tmp_path/'future99.db'
    raw = sqlite3.connect(str(p))  # default journal mode: DELETE
    raw.execute("CREATE TABLE schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    raw.execute("CREATE TABLE keepme (a INT)")
    raw.execute("INSERT INTO keepme VALUES (7)")
    raw.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
    raw.commit()
    raw.close()
    before = p.read_bytes()
    with pytest.raises(SchemaVersionError, match='newer program'):
        Database(f'sqlite:///{p}')
    assert p.read_bytes() == before
    assert not (tmp_path/'future99.db-wal').exists()
    assert not (tmp_path/'future99.db-shm').exists()
    raw = sqlite3.connect(str(p))
    assert raw.execute("SELECT a FROM keepme").fetchall() == [(7,)]
    assert [r[0] for r in raw.execute("SELECT version FROM schema_migrations")] == [99]
    raw.close()


def test_future_wal_database_refused_bytes_unchanged(tmp_path):
    p = tmp_path/'future-wal.db'
    d = Database(f'sqlite:///{p}')
    d.close()
    raw = sqlite3.connect(str(p))
    raw.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
    raw.commit()
    raw.close()
    before = p.read_bytes()
    with pytest.raises(SchemaVersionError):
        Database(f'sqlite:///{p}')
    assert p.read_bytes() == before
    assert not (tmp_path/'future-wal.db-wal').exists()


def _ledgered_database(tmp_path):
    """A real Ledger history: deposit, reserved buy fill, T+1 rollover, sell
    fill, release, withdraw — every writer path that moves cash or reserves."""
    from trademind.domain.base import new_id
    from trademind.domain.enums import OrderSide, RunMode
    from trademind.domain.instruments import FeeSchedule
    from trademind.domain.orders import Fill, Order
    from trademind.portfolio import Ledger

    at = T0
    def stamp():
        nonlocal at
        at = at + timedelta(minutes=1)
        return at
    fees = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"), created_at=T0,
                       commission_rate="0.00025", commission_min="5",
                       stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")
    p = tmp_path/'books.db'
    d = Database(f'sqlite:///{p}')
    repo = Repository(d)
    led = Ledger(d, "paper-001", fees)
    led.deposit(Decimal("1000000"), stamp())
    buy = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id=new_id("o"),
                client_order_id=new_id("cli"), intent_id=new_id("i"), guard_id=new_id("g"),
                account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
                side=OrderSide.BUY, quantity="1000", limit_price="10.00", updated_at=T0)
    buy_fill = Fill(id=new_id("f"), trace_id=new_id("t"), created_at=T0,
                    fill_id=new_id("f"), order_id=buy.order_id,
                    provider_fill_id=new_id("pf"), quantity="1000", price="10.00",
                    fee="0", executed_at=stamp(), available_at=T0)
    led.apply_fill(buy_fill, buy, at)
    pending = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id=new_id("o"),
                    client_order_id=new_id("cli"), intent_id=new_id("i"), guard_id=new_id("g"),
                    account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
                    side=OrderSide.BUY, quantity="100", limit_price="11.00", updated_at=T0)
    led.reserve(pending.order_id, Decimal("1100"), stamp())
    led.on_new_trading_day(T0.date() + timedelta(days=1))
    sell = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id=new_id("o"),
                 client_order_id=new_id("cli"), intent_id=new_id("i"), guard_id=new_id("g"),
                 account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
                 side=OrderSide.SELL, quantity="400", limit_price="10.50", updated_at=T0)
    sell_fill = Fill(id=new_id("f"), trace_id=new_id("t"), created_at=T0,
                     fill_id=new_id("f"), order_id=sell.order_id,
                     provider_fill_id=new_id("pf"), quantity="400", price="10.50",
                     fee="0", executed_at=stamp(), available_at=T0)
    led.apply_fill(sell_fill, sell, at)
    led.release(pending.order_id, stamp())
    led.withdraw(Decimal("1000"), stamp())
    # Orders and fills as the OMS would have stored them, so reference
    # closure holds for the event log's order_id/fill_id references.
    for order, fill in ((buy, buy_fill), (pending, None), (sell, sell_fill)):
        repo.save_order(order)
        if fill is not None:
            repo.save_fill(fill)
    return d, p


def test_verify_database_reconciles_real_books(tmp_path):
    d, p = _ledgered_database(tmp_path)
    backup = d.backup_to(tmp_path/'books-copy.db')
    d.close()
    report = verify_database(backup)
    assert report['integrity_ok'] and report['references_ok']
    assert report['ledger_ok'] is True and report['ok'] is True
    detail = report['accounts_reconciled']['paper-001']
    assert detail['ok'] is True
    assert Decimal(detail['cash_expected']) == Decimal(detail['cash_stored'])
    assert detail['positions_expected']['600519.SH']['quantity'] == '600'
    assert Decimal(detail['active_reservations_sum']) == 0
    assert detail['version_expected'] == detail['version_stored']


def test_verify_database_rejects_tampered_cash(tmp_path):
    """The Issue's counterexample: stored cash 999 against deposit/fill
    events that replay to a different number must NOT verify ok."""
    d, p = _ledgered_database(tmp_path)
    backup = d.backup_to(tmp_path/'tampered.db')
    d.close()
    raw = sqlite3.connect(str(backup))
    stored_cash = raw.execute("SELECT cash FROM account_state").fetchone()[0]
    raw.execute("UPDATE account_state SET cash='999'")
    raw.commit()
    raw.close()
    report = verify_database(backup)
    assert report['ok'] is False and report['ledger_ok'] is False
    detail = report['accounts_reconciled']['paper-001']
    assert detail['cash_stored'] == '999'
    assert Decimal(detail['cash_expected']) == Decimal(stored_cash)
    assert Decimal(detail['cash_expected']) != Decimal('999')


def test_verify_database_rejects_tampered_position(tmp_path):
    """A physically intact copy whose stored position quantity was altered
    without a matching event must fail reconciliation (positions replay)."""
    d, p = _ledgered_database(tmp_path)
    backup = d.backup_to(tmp_path/'tampered-pos.db')
    d.close()
    raw = sqlite3.connect(str(backup))
    payload = json.loads(raw.execute(
        "SELECT payload_json FROM account_state").fetchone()[0])
    pos = payload['positions'][0]
    assert pos['instrument_id'] == '600519.SH'
    pos['quantity'] = str(Decimal(pos['quantity']) + 1)
    raw.execute("UPDATE account_state SET payload_json=?",
                (json.dumps(payload, ensure_ascii=False),))
    raw.commit()
    raw.close()
    report = verify_database(backup)
    assert report['ok'] is False and report['ledger_ok'] is False
    detail = report['accounts_reconciled']['paper-001']
    assert detail['positions_stored']['600519.SH']['quantity'] != \
        detail['positions_expected']['600519.SH']['quantity']


def test_verify_database_rejects_missing_event(tmp_path):
    d, p = _ledgered_database(tmp_path)
    backup = d.backup_to(tmp_path/'missing-event.db')
    d.close()
    raw = sqlite3.connect(str(backup))
    raw.execute("DELETE FROM account_events WHERE seq=("
                "SELECT max(seq) FROM account_events WHERE account_id='paper-001')")
    raw.commit()
    raw.close()
    report = verify_database(backup)
    assert report['ok'] is False and report['ledger_ok'] is False


def test_verify_database_rejects_inconsistent_active_reservation(tmp_path):
    d, p = _ledgered_database(tmp_path)
    backup = d.backup_to(tmp_path/'reservations.db')
    d.close()
    raw = sqlite3.connect(str(backup))
    # A released reservation silently re-activated: reserved_cash stays 0 but
    # the frozen amount would be spendable twice.
    raw.execute("UPDATE reservations SET active=1")
    raw.commit()
    raw.close()
    report = verify_database(backup)
    assert report['ok'] is False and report['ledger_ok'] is False


def test_evidence_write_read_roundtrip(db):
    repo = Repository(db)
    ev = legal_evidence()
    repo.save_evidence(ev)
    rows = repo.evidence_visible("600519.SH", T0 + timedelta(days=1))
    assert rows and rows[0]["evidence_id"] == ev.evidence_id
    # point-in-time: not visible before available_at
    assert repo.evidence_visible("600519.SH", T0 - timedelta(days=2)) == []


def test_duplicate_content_hash_same_revision_rejected(db):
    repo = Repository(db)
    ev = legal_evidence()
    repo.save_evidence(ev)
    with pytest.raises(DuplicateKey):
        repo.save_evidence(ev)
    # a revision bump is a correction, not a duplicate
    from trademind.domain.evidence import Evidence

    rev2 = ev.model_copy(update={"revision": 2, "supersedes_evidence_id": ev.evidence_id,
                                 "evidence_id": new_id("ev")})
    repo.save_evidence(rev2)
    assert len(repo.evidence_visible("600519.SH", T0 + timedelta(days=1))) == 2


def test_fill_idempotency(db):
    repo = Repository(db)
    f = Fill(id=new_id("f"), trace_id=new_id("t"), created_at=T0, fill_id=new_id("f"),
             order_id="o1", provider_fill_id="PF-1", quantity="100", price="10.5",
             fee="5", executed_at=T0, available_at=T0)
    assert repo.save_fill(f) is True
    dup = f.model_copy(update={"fill_id": new_id("f")})  # redelivery, same provider id
    assert repo.save_fill(dup) is False


def test_account_event_fill_dedupe(db):
    repo = Repository(db)
    ev = AccountEvent(id=new_id("ae"), trace_id=new_id("t"), created_at=T0, seq=1,
                      account_id="paper-001", kind="fill_buy", instrument_id="600519.SH",
                      quantity="100", amount="150000", fill_id="F-1", effective_at=T0)
    assert repo.append_account_event(ev) is True
    dup = ev.model_copy(update={"seq": 2})  # same fill redelivered under new seq
    assert repo.append_account_event(dup) is False


def test_order_client_id_unique(db):
    from trademind.domain.enums import OrderSide, RunMode
    from trademind.domain.orders import Order

    repo = Repository(db)
    o = Order(id=new_id("o"), trace_id=new_id("t"), created_at=T0, order_id="ord-1",
              client_order_id="cli-1", intent_id="i1", guard_id="g1",
              account_id="paper-001", instrument_id="600519.SH", mode=RunMode.PAPER,
              side=OrderSide.BUY, quantity="100", limit_price="1500",
              updated_at=T0)
    repo.save_order(o)
    with pytest.raises(DuplicateKey):
        repo.save_order(o.model_copy(update={"order_id": "ord-2"}))
    assert repo.get_order_by_client_id("cli-1")["order_id"] == "ord-1"


def test_account_state_upsert_and_events(db):
    from trademind.domain.orders import AccountSnapshot, PositionState

    repo = Repository(db)
    snap = AccountSnapshot(id=new_id("as"), trace_id=new_id("t"), created_at=T0,
                           account_id="paper-001", version=1, as_of=T0,
                           cash="1000000", market_value="0", equity="1000000",
                           positions=[])
    repo.save_account_state(snap)
    snap2 = snap.model_copy(update={"version": 2})
    repo.save_account_state(snap2)
    loaded = repo.load_account_state("paper-001")
    assert loaded["version"] == 2


def test_memory_point_in_time_visibility(db):
    from trademind.domain.enums import MemoryType
    from trademind.domain.memory import MemoryRecord

    repo = Repository(db)
    m = MemoryRecord(id=new_id("m"), trace_id=new_id("t"), created_at=T0,
                     memory_id="mem-1", type=MemoryType.LESSON, available_at=T0,
                     summary="cut losers early")
    repo.save_memory(m)
    assert repo.memories_visible(T0 + timedelta(hours=1))
    assert repo.memories_visible(T0 - timedelta(hours=1)) == []


def test_audit_and_critical_failure(db, tmp_path, monkeypatch):
    sink = AuditSink(db)
    audit_id = sink.record("tester", "set_mode", "paper")
    assert audit_id

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("disk full")

    broken = AuditSink.__new__(AuditSink)
    broken.db = Boom()
    with pytest.raises(CriticalAuditFailure):
        broken.record_critical("system", "approve_order", "ord-1")


def test_kill_switch_persistence(db):
    repo = Repository(db)
    assert repo.get_kill_switch()["engaged"] is False
    repo.set_kill_switch(True, "manual stop")
    assert repo.get_kill_switch()["engaged"] is True
