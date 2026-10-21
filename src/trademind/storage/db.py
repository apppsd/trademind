"""SQLite storage: connection, migrations, repositories, audit sink (TASK-07).

- Migrations are versioned, ordered and idempotent (schema_migrations table).
  schema.sql is the frozen v1 baseline; later versions are statement lists in
  MIGRATIONS applied in one transaction each (TASK-20261013-17).
- A database stamped by a NEWER program is refused before any structural or
  account write: opening it must not modify, "fix" or mask it (ISSUE-20261013-09).
- Fill/apply-to-ledger happens in one transaction; idempotency keys
  (client_order_id, provider_fill_id, account fill_id) are UNIQUE.
- Critical audit failures raise CriticalAuditFailure: callers must stop
  adding new risk (ARCH-001) instead of losing key logs silently.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from ..domain.base import new_id, utcnow
from ..errors import DuplicateKey, SchemaVersionError
from .sqlite_url import sqlite_url_to_path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Ordered upgrades after the schema.sql v1 baseline. Each entry is a list of
# single statements (executescript cannot run inside a transaction). Every
# migration must be additive and re-runnable only through its version stamp.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    # v2: intent creation timestamps (windowed queries need a real column,
    # backfilled from the payload) plus report-window indexes.
    2: (
        "ALTER TABLE intents ADD COLUMN created_at TEXT",
        "UPDATE intents SET created_at = json_extract(payload_json, '$.created_at') "
        "WHERE created_at IS NULL",
        "CREATE INDEX IF NOT EXISTS ix_intents_created ON intents (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_calls_created ON model_calls (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_ctx_asof ON context_snapshots (as_of)",
        "CREATE INDEX IF NOT EXISTS ix_plan_hist_acct_avail "
        "ON strategy_plan_history (account_id, available_at)",
    ),
}

LATEST_SCHEMA_VERSION = max({1} | set(MIGRATIONS))


def _refuse_newer_schema(path: Path) -> None:
    """Refuse a database stamped by a newer program before any read-write
    connection exists. The check runs through an ``immutable=1`` read-only
    connection, so a refusal cannot flip the journal mode, create
    ``-wal``/``-shm`` side files or otherwise change a single byte of the
    database (ISSUE-20261013-09). An ``immutable`` read skips WAL replay —
    a stale version there means the inner :meth:`Database.migrate` gate
    still refuses after the read-write open, and a WAL database needs no
    journal flip anyway. Databases without a version registry yet are left
    to migrate(), which bootstraps it."""
    if not path.exists():
        return
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        try:
            row = ro.execute("SELECT max(version) FROM schema_migrations").fetchone()
        except sqlite3.OperationalError:
            return  # no version registry yet; migrate() will create it
    finally:
        ro.close()
    if row and row[0] is not None and row[0] > LATEST_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database {path} was written by a newer program "
            f"(schema v{row[0]} > supported v{LATEST_SCHEMA_VERSION}); "
            "refusing to open or modify it")


class CriticalAuditFailure(RuntimeError):
    """A key intent/order audit write failed; new risk must be halted."""


class Database:
    """Thread-safe SQLite wrapper. Single connection + lock is sufficient for
    the single-process local design (single-writer ledger principle)."""

    def __init__(self, url: str = "sqlite:///data/runtime/trademind.db") -> None:
        # Shared URL semantics with the writer lease: both must name the same
        # file for the same URL, relative forms included (ISSUE-20261013-06).
        self.path = sqlite_url_to_path(url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        # Version gate before the read-write connection: the refusal path
        # below must not run a single persistent statement (ISSUE-20261013-09).
        _refuse_newer_schema(self.path)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self.migrate()
        except BaseException:
            # A failed open (newer schema refused in a race window, broken
            # migration) releases its handle and leaves the file alone.
            self._conn.close()
            raise

    # -- core ------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def commit(self):
        with self._lock:
            if self._depth == 0:
                self._conn.commit()

    def transaction(self):
        return _Txn(self)

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    # -- migrations --------------------------------------------------------
    def migrate(self) -> None:
        with self._lock:
            # Version registry first, and nothing else: a database from a
            # newer program must be refused before any structural write
            # (ISSUE-20261013-09). On such a database this CREATE is a no-op.
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            self._conn.commit()
            applied = self.applied_migrations()
            if applied and max(applied) > LATEST_SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database {self.path} was written by a newer program "
                    f"(schema v{max(applied)} > supported v{LATEST_SCHEMA_VERSION}); "
                    "refusing to open or modify it")
            # v1 baseline: schema.sql is idempotent (IF NOT EXISTS).
            with open(SCHEMA_PATH, encoding="utf-8") as f:
                self._conn.executescript(f.read())
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (utcnow().isoformat(),),
            )
            self._conn.commit()
            # Ordered upgrades, one transaction each: a failed migration rolls
            # back completely (stamp included) and retries on next open.
            for version in sorted(MIGRATIONS):
                if version in applied:
                    continue
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in MIGRATIONS[version]:
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                        (version, utcnow().isoformat()))
                except BaseException:
                    self._conn.rollback()
                    raise
                self._conn.commit()

    def applied_migrations(self) -> list[int]:
        return [int(r["version"]) for r in self.query("SELECT version FROM schema_migrations ORDER BY version")]

    # -- backup / verify ----------------------------------------------------
    def backup_to(self, target: str | Path) -> Path:
        """Consistent online backup through SQLite's backup API (WAL content
        included). Refuses to overwrite; the caller owns the target location."""
        target = Path(target)
        if target.exists():
            raise FileExistsError(f"backup target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._conn.backup(dest)
                dest.commit()
            finally:
                dest.close()
        return target


class _Txn:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> "_Txn":
        self.db._lock.acquire()
        self.level = self.db._depth
        try:
            self.db._conn.execute("BEGIN IMMEDIATE" if self.level == 0 else f"SAVEPOINT tx_{self.level}")
        except BaseException:
            self.db._lock.release()
            raise
        self.db._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.db._depth -= 1
            if self.level == 0:
                if exc_type is None:
                    self.db._conn.commit()
                else:
                    self.db._conn.rollback()
            else:
                if exc_type is not None:
                    self.db._conn.execute(f"ROLLBACK TO tx_{self.level}")
                self.db._conn.execute(f"RELEASE tx_{self.level}")
        finally:
            self.db._lock.release()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.db._conn.execute(sql, tuple(params))


def _note_fee_price(note: str) -> tuple[Decimal, Decimal]:
    """fee=/price= pairs from an account event note. Local copy of the ledger
    parser: portfolio imports storage, so storage cannot import portfolio."""
    fee = price = Decimal("0")
    for part in note.split(";"):
        if part.startswith("fee="):
            fee = Decimal(part[4:])
        elif part.startswith("price="):
            price = Decimal(part[6:])
    return fee, price


def _replay_account_ledger(conn: sqlite3.Connection, account_id: str) -> dict:
    """Deterministic replay of one account's event log, mirroring
    Ledger.rebuild_from_events cash/reservation/position arithmetic (T+1
    availability needs market-profile knowledge and is deliberately not
    re-derived here). Used by verify_database for financial reconciliation.
    """
    cash = reserved = Decimal("0")
    positions: dict[str, dict[str, Decimal]] = {}
    max_seq = 0
    events = 0
    for r in conn.execute(
            "SELECT seq, kind, amount, payload_json FROM account_events "
            "WHERE account_id=? ORDER BY seq", (account_id,)):
        events += 1
        max_seq = max(max_seq, int(r["seq"]))
        payload = json.loads(r["payload_json"])
        amount = Decimal(r["amount"]) if r["amount"] else None
        qty = Decimal(payload["quantity"]) if payload.get("quantity") else None
        kind = r["kind"]
        if kind == "deposit" and amount is not None:
            cash += amount
        elif kind == "withdraw" and amount is not None:
            cash -= amount
        elif kind == "reserve" and amount is not None:
            reserved += amount
        elif kind == "release" and amount is not None:
            reserved -= amount
        elif kind in ("fill_buy", "fill_sell") and qty is not None:
            fee, price = _note_fee_price(payload.get("note", ""))
            lot = positions.setdefault(payload["instrument_id"],
                                       {"quantity": Decimal("0"), "avg_cost": Decimal("0")})
            if kind == "fill_buy":
                cash -= qty * price + fee
                total_cost = lot["avg_cost"] * lot["quantity"] + qty * price + fee
                lot["quantity"] += qty
                lot["avg_cost"] = (total_cost / lot["quantity"]).quantize(Decimal("0.0001"))
            else:
                cash += qty * price - fee
                lot["quantity"] -= qty
                if lot["quantity"] <= 0:
                    positions.pop(payload["instrument_id"], None)
    return {'cash': cash, 'reserved': reserved, 'positions': positions,
            'events': events, 'max_seq': max_seq}


def verify_database(path: str | Path) -> dict:
    """Read-only verification of a database file (typically a restored backup).

    Opens the copy through a raw read-only connection — never through
    Database(), whose open would migrate or stamp it. Beyond physical checks
    (integrity, foreign keys, schema version, reference closure) it replays
    each account's event ledger and reconciles the stored snapshot: cash and
    reserved cash against the events, reserved cash against the active
    reservations, positions against the replay, and the account version
    against the highest event seq. A physically intact copy with books that
    do not add up is NOT ok (ISSUE-20261013-09).
    """
    p = Path(path)
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            integrity = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        except sqlite3.DatabaseError as exc:
            # A truncated or otherwise broken file may fail before any check
            # can run; that is itself the verdict.
            return {'path': str(p), 'ok': False, 'integrity_ok': False,
                    'integrity_messages': [f'database unreadable: {exc}'],
                    'foreign_key_violations': [], 'tables': 0,
                    'dangling_references': {}, 'references_ok': False,
                    'accounts_reconciled': {}, 'ledger_ok': False}
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        summary: dict[str, Any] = {
            'path': str(p),
            'integrity_ok': integrity == ['ok'],
            'integrity_messages': integrity,
            'foreign_key_violations': [tuple(r) for r in fk],
            'tables': len(names),
        }
        if 'schema_migrations' in names:
            summary['schema_versions'] = [int(r[0]) for r in
                                          conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            summary['schema_supported'] = bool(summary['schema_versions']) and \
                max(summary['schema_versions']) <= LATEST_SCHEMA_VERSION
        def count(table: str) -> int:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in names else 0
        summary['accounts'] = count('account_state')
        summary['orders'] = count('orders')
        summary['fills'] = count('fills')
        summary['account_events'] = count('account_events')
        summary['reservations'] = count('reservations')
        # Reference closure: every stored reference must resolve inside the copy.
        dangling: dict[str, int] = {}
        if {'fills', 'orders'} <= names:
            dangling['fills_without_order'] = conn.execute(
                "SELECT COUNT(*) FROM fills f LEFT JOIN orders o ON o.order_id=f.order_id "
                "WHERE o.order_id IS NULL").fetchone()[0]
        if {'account_events', 'fills'} <= names:
            dangling['events_without_fill'] = conn.execute(
                "SELECT COUNT(*) FROM account_events e LEFT JOIN fills f ON f.fill_id=e.fill_id "
                "WHERE e.fill_id IS NOT NULL AND f.fill_id IS NULL").fetchone()[0]
        if {'reservations', 'orders'} <= names:
            dangling['reservations_without_order'] = conn.execute(
                "SELECT COUNT(*) FROM reservations r LEFT JOIN orders o ON o.order_id=r.order_id "
                "WHERE o.order_id IS NULL").fetchone()[0]
        summary['dangling_references'] = dangling
        summary['references_ok'] = not any(dangling.values())
        # Financial reconciliation (ISSUE-20261013-09): integrity, foreign
        # keys and reference closure say nothing about whether the books add
        # up. Replay every account's event log and compare against the stored
        # snapshot, the active reservations and the account version.
        ledger_details: dict[str, Any] = {}
        ledger_ok = True
        if 'account_state' in names:
            for row in conn.execute("SELECT account_id, version, cash, reserved_cash, "
                                    "payload_json FROM account_state"):
                acct = row["account_id"]
                replay = _replay_account_ledger(conn, acct)
                stored_cash = Decimal(row["cash"])
                stored_reserved = Decimal(row["reserved_cash"])
                active_reserved = sum(
                    (Decimal(r[0]) for r in conn.execute(
                        "SELECT amount FROM reservations WHERE account_id=? AND active=1",
                        (acct,))), Decimal("0"))
                try:
                    stored_snapshot = json.loads(row["payload_json"])
                    pos_stored = {p["instrument_id"]: {"quantity": Decimal(p["quantity"]),
                                                       "avg_cost": Decimal(p["avg_cost"])}
                                  for p in stored_snapshot.get("positions", [])}
                except (ValueError, KeyError, TypeError):
                    pos_stored = {}
                detail = {
                    'events': replay['events'],
                    'cash_expected': str(replay['cash']), 'cash_stored': str(stored_cash),
                    'reserved_expected': str(replay['reserved']),
                    'reserved_stored': str(stored_reserved),
                    'active_reservations_sum': str(active_reserved),
                    'positions_expected': {i: {'quantity': str(v['quantity']),
                                               'avg_cost': str(v['avg_cost'])}
                                           for i, v in replay['positions'].items()},
                    'positions_stored': {i: {'quantity': str(v['quantity']),
                                             'avg_cost': str(v['avg_cost'])}
                                         for i, v in pos_stored.items()},
                    'version_expected': replay['max_seq'], 'version_stored': int(row["version"]),
                }
                ok_acct = (replay['cash'] == stored_cash
                           and replay['reserved'] == stored_reserved
                           and active_reserved == stored_reserved
                           and replay['positions'] == pos_stored
                           and replay['max_seq'] == int(row["version"]))
                detail['ok'] = ok_acct
                ledger_details[acct] = detail
                ledger_ok = ledger_ok and ok_acct
        summary['accounts_reconciled'] = ledger_details
        summary['ledger_ok'] = ledger_ok
        summary['ok'] = (summary['integrity_ok'] and not summary['foreign_key_violations']
                         and summary.get('schema_supported', False) and summary['references_ok']
                         and summary['ledger_ok'])
        return summary
    finally:
        conn.close()


def dumps(obj: BaseModel | dict) -> str:
    if isinstance(obj, BaseModel):
        return json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


class Repository:
    """Generic JSON-payload repository with typed index columns."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        try:
            self.db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"{table}: {exc}") from exc

    def save_evidence(self, ev) -> None:
        instrument_id = ev.instrument_ids[0] if ev.instrument_ids else None
        self._insert("evidence", {
            "evidence_id": ev.evidence_id,
            "dataset": ev.dataset.value,
            "instrument_id": instrument_id,
            "source_id": ev.source_id,
            "content_hash": ev.content_hash,
            "revision": ev.revision,
            "event_time": ev.event_time.isoformat() if ev.event_time else None,
            "published_at": ev.published_at.isoformat() if ev.published_at else None,
            "available_at": ev.available_at.isoformat(),
            "ingested_at": ev.ingested_at.isoformat(),
            "backfill": int(ev.backfill),
            "payload_json": dumps(ev),
        })

    def evidence_visible(self, instrument_id: str | None, as_of: datetime, dataset: str | None = None) -> list[dict]:
        sql = "SELECT payload_json FROM evidence WHERE available_at <= ?"
        params: list[Any] = [as_of.isoformat()]
        if instrument_id:
            sql += " AND (instrument_id = ? OR instrument_id IS NULL)"
            params.append(instrument_id)
        if dataset:
            sql += " AND dataset = ?"
            params.append(dataset)
        sql += " ORDER BY available_at"
        return [json.loads(r["payload_json"]) for r in self.db.query(sql, params)]

    def save_context(self, snap) -> None:
        self._insert("context_snapshots", {
            "context_id": snap.context_id,
            "instrument_id": snap.instrument_id,
            "as_of": snap.as_of.isoformat(),
            "expires_at": snap.expires_at.isoformat(),
            "content_hash": snap.content_hash,
            "payload_json": dumps(snap),
        })

    def get_context(self, context_id: str):
        rows = self.db.query("SELECT payload_json FROM context_snapshots WHERE context_id=?", (context_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def save_report(self, report) -> None:
        self._insert("analyst_reports", {
            "report_id": report.id, "context_id": report.context_id,
            "role": report.role, "payload_json": dumps(report),
        })

    def save_thesis(self, thesis) -> None:
        self._insert("theses", {
            "thesis_id": thesis.thesis_id,
            "instrument_id": thesis.instrument_id,
            "context_id": thesis.context_id,
            "valid_until": thesis.valid_until.isoformat(),
            "payload_json": dumps(thesis),
        })

    def latest_valid_thesis(self, instrument_id: str, as_of: datetime):
        rows = self.db.query(
            "SELECT t.payload_json FROM theses t JOIN context_snapshots c ON c.context_id=t.context_id "
            "WHERE t.instrument_id=? AND t.valid_until>=? AND c.as_of<=? "
            "AND (json_extract(t.payload_json,'$.available_at') IS NULL OR json_extract(t.payload_json,'$.available_at')<=?) "
            "ORDER BY t.rowid DESC LIMIT 1",
            (instrument_id, as_of.isoformat(), as_of.isoformat(), as_of.isoformat()),
        )
        return json.loads(rows[0]["payload_json"]) if rows else None

    def save_judgments(self, batch) -> None:
        self._insert("judgment_batches", {
            "batch_id": batch.id, "context_id": batch.context_id,
            "thesis_id": batch.thesis_id, "instrument_id": batch.instrument_id,
            "as_of": batch.as_of.isoformat(), "is_stub": int(batch.is_stub),
            "payload_json": dumps(batch),
        })

    def save_intent(self, intent, status: str) -> None:
        self._insert("intents", {
            "decision_id": intent.decision_id,
            "intent_version": intent.intent_version,
            "instrument_id": intent.instrument_id,
            "action": intent.action.value,
            "valid_until": intent.valid_until.isoformat(),
            "status": status,
            "created_at": intent.created_at.isoformat(),
            "payload_json": dumps(intent),
        })

    def set_intent_status(self, decision_id: str, intent_version: int, status: str) -> None:
        self.db.execute(
            "UPDATE intents SET status=? WHERE decision_id=? AND intent_version=?",
            (status, decision_id, intent_version),
        )
        self.db.commit()

    def save_risk_review(self, review) -> None:
        self._insert("risk_reviews", {
            "review_id": review.id, "decision_id": review.decision_id,
            "verdict": review.verdict.value, "payload_json": dumps(review),
        })

    def save_guard_result(self, guard_id: str, result) -> None:
        payload = dumps(result)
        self._insert("guard_results", {
            "guard_id": guard_id, "intent_id": result["intent_id"] if isinstance(result, dict) else result.intent_id,
            "verdict": result["verdict"] if isinstance(result, dict) else result.verdict.value,
            "payload_json": payload,
        })

    def save_model_call(self, call) -> None:
        self._insert("model_calls", {
            "call_id": call.call_id, "trace_id": call.trace_id, "role": call.role,
            "provider": call.provider.value, "model": call.model,
            "status": call.status, "is_stub": int(call.is_stub),
            "latency_ms": call.latency_ms, "created_at": call.created_at.isoformat(),
            "payload_json": dumps(call),
        })

    def save_order(self, order) -> None:
        self._insert("orders", {
            "order_id": order.order_id, "client_order_id": order.client_order_id,
            "intent_id": order.intent_id, "account_id": order.account_id,
            "instrument_id": order.instrument_id, "side": order.side.value,
            "status": order.status.value, "created_at": order.created_at.isoformat(),
            "payload_json": dumps(order),
        })

    def update_order(self, order) -> None:
        self.db.execute(
            "UPDATE orders SET status=?, payload_json=? WHERE order_id=?",
            (order.status.value, dumps(order), order.order_id),
        )
        self.db.commit()

    def get_order_by_client_id(self, client_order_id: str):
        rows = self.db.query("SELECT payload_json FROM orders WHERE client_order_id=?", (client_order_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def open_orders(self) -> list[dict]:
        rows = self.db.query(
            "SELECT payload_json FROM orders WHERE status IN ('NEW','SUBMITTING','ACKNOWLEDGED','PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')"
        )
        return [json.loads(r["payload_json"]) for r in rows]

    def save_fill(self, fill) -> bool:
        """Insert fill; returns False when duplicate provider_fill_id."""
        try:
            self._insert("fills", {
                "fill_id": fill.fill_id, "provider_fill_id": fill.provider_fill_id,
                "order_id": fill.order_id, "executed_at": fill.executed_at.isoformat(),
                "payload_json": dumps(fill),
            })
            return True
        except DuplicateKey:
            return False

    def fills_for_order(self, order_id: str) -> list[dict]:
        return [json.loads(r["payload_json"]) for r in
                self.db.query("SELECT payload_json FROM fills WHERE order_id=?", (order_id,))]

    # -- account -----------------------------------------------------------
    def append_account_event(self, ev, fill_dedupe: bool = True) -> bool:
        """Returns False if an event with the same fill_id already applied."""
        if fill_dedupe and ev.fill_id:
            dupes = self.db.query(
                "SELECT 1 FROM account_events WHERE account_id=? AND fill_id=?",
                (ev.account_id, ev.fill_id),
            )
            if dupes:
                return False
        self._insert("account_events", {
            "account_id": ev.account_id, "seq": ev.seq, "kind": ev.kind,
            "instrument_id": ev.instrument_id, "fill_id": ev.fill_id,
            "amount": str(ev.amount) if ev.amount is not None else None,
            "effective_at": ev.effective_at.isoformat(),
            "payload_json": dumps(ev),
        })
        return True

    def account_events(self, account_id: str) -> list[dict]:
        return [json.loads(r["payload_json"]) for r in
                self.db.query("SELECT payload_json FROM account_events WHERE account_id=? ORDER BY seq", (account_id,))]

    def save_account_state(self, snap) -> None:
        self.db.execute(
            "INSERT INTO account_state (account_id, version, cash, reserved_cash, updated_at, payload_json) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET version=excluded.version, "
            "cash=excluded.cash, reserved_cash=excluded.reserved_cash, updated_at=excluded.updated_at, "
            "payload_json=excluded.payload_json",
            (snap.account_id, snap.version, str(snap.cash), str(snap.reserved_cash),
             snap.as_of.isoformat(), dumps(snap)),
        )
        self.db.commit()

    def load_account_state(self, account_id: str):
        rows = self.db.query("SELECT payload_json FROM account_state WHERE account_id=?", (account_id,))
        return json.loads(rows[0]["payload_json"]) if rows else None

    def save_reservation(self, res) -> None:
        self._insert("reservations", {
            "reservation_id": res.reservation_id, "order_id": res.order_id,
            "account_id": res.account_id, "account_version": res.account_version,
            "amount": str(res.amount), "active": int(res.active),
            "payload_json": dumps(res),
        })

    def set_reservation_active(self, reservation_id: str, active: bool) -> None:
        self.db.execute("UPDATE reservations SET active=? WHERE reservation_id=?",
                        (int(active), reservation_id))
        self.db.commit()

    def active_reservations(self, account_id: str) -> list[dict]:
        return [json.loads(r["payload_json"]) for r in self.db.query(
            "SELECT payload_json FROM reservations WHERE account_id=? AND active=1", (account_id,))]

    # -- outcomes / memory ---------------------------------------------------
    def save_outcome(self, outcome) -> None:
        self.db.execute(
            "INSERT INTO outcomes (decision_id, is_final, available_at, payload_json) VALUES (?,?,?,?) "
            "ON CONFLICT(decision_id) DO UPDATE SET is_final=excluded.is_final, "
            "available_at=excluded.available_at, payload_json=excluded.payload_json",
            (outcome.decision_id, int(outcome.is_final), outcome.available_at.isoformat(), dumps(outcome)),
        )
        self.db.commit()

    def save_memory(self, rec) -> None:
        self._insert("memories", {
            "memory_id": rec.memory_id, "type": rec.type.value,
            "instrument_id": rec.instrument_id,
            "available_at": rec.available_at.isoformat(),
            "review_state": rec.review_state.value, "version": rec.version,
            "payload_json": dumps(rec),
        })

    def update_memory_state(self, memory_id: str, review_state: str, version: int) -> None:
        self.db.execute("UPDATE memories SET review_state=?, version=? WHERE memory_id=?",
                        (review_state, version, memory_id))
        self.db.commit()

    def memories_visible(self, as_of: datetime, instrument_id: str | None = None) -> list[dict]:
        """Memories whose available_at instant is not after as_of.

        TEXT available_at may carry any UTC offset (legacy rows included), so
        a SQL string comparison is not instant order (ISSUE-20261018-01). The
        query is only a coarse prefilter: widening the TEXT upper bound by
        2 days can never exclude a truly visible row (ISO offset skew is
        bounded far below that), and the exact visibility decision below
        compares parsed timezone-aware datetimes — the same semantics as
        MemoryRecord.visible_at. Naive as_of is rejected like the domain
        contract's require_tz.
        """
        if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
            raise ValueError("memories_visible requires a timezone-aware as_of")
        rows = self.db.query(
            "SELECT payload_json FROM memories WHERE available_at <= ? "
            "AND review_state IN ('PROVISIONAL','CONFIRMED')",
            ((as_of + timedelta(days=2)).isoformat(),),
        )
        out = []
        for r in rows:
            rec = json.loads(r["payload_json"])
            if instrument_id and rec.get("instrument_id") not in (None, instrument_id):
                continue
            if datetime.fromisoformat(rec["available_at"]) > as_of:
                continue
            vu = rec.get("valid_until")
            if vu and datetime.fromisoformat(vu) < as_of:
                continue
            out.append(rec)
        out.sort(key=lambda rec: datetime.fromisoformat(rec["available_at"]))
        return out

    def save_reflection(self, reflection) -> None:
        self._insert("reflections", {
            "reflection_id": reflection.id, "decision_id": reflection.decision_id,
            "payload_json": dumps(reflection),
        })

    # -- control -------------------------------------------------------------
    def get_kill_switch(self) -> dict:
        r = self.db.query("SELECT engaged, reason, updated_at FROM kill_switch WHERE id=1")[0]
        return {"engaged": bool(r["engaged"]), "reason": r["reason"], "updated_at": r["updated_at"]}

    def set_kill_switch(self, engaged: bool, reason: str = "") -> None:
        self.db.execute("UPDATE kill_switch SET engaged=?, reason=?, updated_at=? WHERE id=1",
                        (int(engaged), reason, utcnow().isoformat()))
        self.db.commit()

    def save_run(self, run_id: str, mode: str, config_hash: str) -> None:
        self._insert("runs", {
            "run_id": run_id, "mode": mode, "config_hash": config_hash,
            "started_at": utcnow().isoformat(), "ended_at": None, "status": "running",
        })

    def finish_run(self, run_id: str, status: str = "done") -> None:
        self.db.execute("UPDATE runs SET status=?, ended_at=? WHERE run_id=?",
                        (status, utcnow().isoformat(), run_id))
        self.db.commit()

    def save_coalesce(self, key: str, at: datetime, meta: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO coalesce_state (key, last_run_at, meta_json) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET last_run_at=excluded.last_run_at, meta_json=excluded.meta_json",
            (key, at.isoformat(), dumps(meta or {})),
        )
        self.db.commit()

    def get_coalesce(self, key: str) -> dict | None:
        rows = self.db.query("SELECT last_run_at, meta_json FROM coalesce_state WHERE key=?", (key,))
        if not rows:
            return None
        return {"last_run_at": datetime.fromisoformat(rows[0]["last_run_at"]),
                "meta": json.loads(rows[0]["meta_json"])}


class AuditSink:
    """Control-plane audit trail. `critical` must never silently fail."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, actor: str, action: str, target: str, payload: dict | None = None) -> str:
        audit_id = new_id("aud")
        self.db.execute(
            "INSERT INTO control_audit (audit_id, actor, action, target, at, payload_json) VALUES (?,?,?,?,?,?)",
            (audit_id, actor, action, target, utcnow().isoformat(), dumps(payload or {})),
        )
        self.db.commit()
        return audit_id

    def record_critical(self, actor: str, action: str, target: str, payload: dict | None = None) -> str:
        try:
            return self.record(actor, action, target, payload)
        except Exception as exc:  # noqa: BLE001
            raise CriticalAuditFailure(
                f"critical audit write failed for {action}/{target}: {exc}"
            ) from exc

    def tail(self, limit: int = 100) -> list[dict]:
        return [json.loads(r["payload_json"]) | {"audit_id": r["audit_id"], "action": r["action"],
                                                 "actor": r["actor"], "target": r["target"]}
                for r in self.db.query(
                    "SELECT * FROM control_audit ORDER BY at DESC LIMIT ?", (limit,))]
