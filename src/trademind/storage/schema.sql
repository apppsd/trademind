-- TradeMind storage schema (TASK-07). SQLite first; PostgreSQL stays a
-- possible migration behind the same repository interface (ARCH-001 note).
-- Object payloads are stored as canonical JSON next to indexed columns so
-- the audit chain can be replayed without schema churn.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    instrument_id TEXT,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    event_time TEXT,
    published_at TEXT,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    backfill INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_instr_time ON evidence (instrument_id, available_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_hash ON evidence (content_hash, revision);

CREATE TABLE IF NOT EXISTS context_snapshots (
    context_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ctx_instr ON context_snapshots (instrument_id, as_of);

CREATE TABLE IF NOT EXISTS analyst_reports (
    report_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS theses (
    thesis_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_theses_instr ON theses (instrument_id, valid_until);

CREATE TABLE IF NOT EXISTS judgment_batches (
    batch_id TEXT PRIMARY KEY,
    context_id TEXT NOT NULL,
    thesis_id TEXT,
    instrument_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    is_stub INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intents (
    decision_id TEXT NOT NULL,
    intent_version INTEGER NOT NULL,
    instrument_id TEXT NOT NULL,
    action TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (decision_id, intent_version)
);

CREATE TABLE IF NOT EXISTS risk_reviews (
    review_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guard_results (
    guard_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calls (
    call_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    is_stub INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_calls_trace ON model_calls (trace_id);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    intent_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_intent ON orders (intent_id);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    provider_fill_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fills_order ON fills (order_id);

CREATE TABLE IF NOT EXISTS account_events (
    account_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    instrument_id TEXT,
    fill_id TEXT,
    amount TEXT,
    effective_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (account_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_account_fill ON account_events (account_id, fill_id) WHERE fill_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS account_state (
    account_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    cash TEXT NOT NULL,
    reserved_cash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    decision_id TEXT PRIMARY KEY,
    is_final INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    instrument_id TEXT,
    available_at TEXT NOT NULL,
    review_state TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mem_available ON memories (available_at);

CREATE TABLE IF NOT EXISTS reflections (
    reflection_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control_audit (
    audit_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    account_version INTEGER NOT NULL,
    amount TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    engaged INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO kill_switch (id, engaged, reason, updated_at)
VALUES (1, 0, '', '1970-01-01T00:00:00+00:00');

CREATE TABLE IF NOT EXISTS coalesce_state (
    key TEXT PRIMARY KEY,
    last_run_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_plan_history (
    account_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(account_id, plan_id)
);
