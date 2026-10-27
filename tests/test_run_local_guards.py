"""Guards for scripts/run_local.py: proxy liveness preflight and the daily
idempotent ledger backup (Task-20261026-22)."""
import importlib.util
import sqlite3
from datetime import date
from pathlib import Path
import pytest


@pytest.fixture
def rl(monkeypatch):
    scripts = Path(__file__).parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('run_local', scripts/'run_local.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_proxy_alive_parses_host_port_and_succeeds(rl):
    calls = []

    def connect(addr, timeout):
        calls.append((addr, timeout))
        return _Conn()

    assert rl._proxy_alive('http://127.0.0.1:7897', connect=connect) is True
    assert calls[-1][0] == ('127.0.0.1', 7897)
    assert calls[-1][1] == 3.0


def test_proxy_alive_false_on_refused(rl):
    def refused(addr, timeout):
        raise OSError('connection refused')

    assert rl._proxy_alive('http://127.0.0.1:7897', connect=refused) is False


def test_proxy_alive_rejects_unparsable_url(rl):
    with pytest.raises(ValueError):
        rl._proxy_alive('::::', connect=lambda addr, timeout: _Conn())


def _make_db(tmp_path):
    db = tmp_path/'runtime'/'acc1'/ 'trademind.db'
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE t(x)')
    conn.commit()
    conn.close()
    return db


def test_backup_if_due_creates_daily_copy_once(rl, tmp_path):
    db = _make_db(tmp_path)
    day = date(2026, 10, 26)
    calls = []

    def backup(source, target):
        calls.append((source, target))
        rl._sqlite_backup(source, target)

    target = rl._backup_if_due(db, 'acc1', today=day, backup=backup)
    assert target is not None and target.exists() and len(calls) == 1
    # the copy is a real readable database
    conn = sqlite3.connect(str(target))
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] >= 1
    conn.close()
    # idempotent: same day skips
    assert rl._backup_if_due(db, 'acc1', today=day, backup=backup) is None
    assert len(calls) == 1


def test_backup_if_due_skips_missing_ledger(rl, tmp_path):
    db = tmp_path/'runtime'/'acc1'/ 'trademind.db'  # never created
    assert rl._backup_if_due(db, 'acc1', today=date(2026, 10, 26)) is None


def test_backup_failure_does_not_block_startup(rl, tmp_path, capsys):
    db = _make_db(tmp_path)

    def boom(source, target):
        raise OSError('disk full')

    assert rl._backup_if_due(db, 'acc1', today=date(2026, 10, 26), backup=boom) is None
    assert '备份失败' in capsys.readouterr().out


def test_backup_retries_after_zero_byte_leftover(rl, tmp_path):
    """2026-10-27 review: a failed attempt used to leave a 0-byte target and
    every later start skipped the backup for that day. The leftover must be
    cleaned up and the backup retried."""
    db = _make_db(tmp_path)
    day = date(2026, 10, 26)
    target = tmp_path/'backups'/'acc1'/day.isoformat()/db.name
    target.parent.mkdir(parents=True)
    target.write_bytes(b'')  # leftover of a failed attempt
    calls = []

    def backup(source, tgt):
        calls.append(tgt)
        rl._sqlite_backup(source, tgt)

    out = rl._backup_if_due(db, 'acc1', today=day, backup=backup)
    assert out == target and len(calls) == 1
    assert target.stat().st_size > 0


def test_sqlite_backup_failure_leaves_no_partial_file(rl, tmp_path):
    source = tmp_path/'runtime'/'a'/ 'trademind.db'  # missing → connect fails
    source.parent.mkdir(parents=True)
    target = tmp_path/'backups'/'a'/ '2026-10-27'/ 'trademind.db'
    with pytest.raises(sqlite3.OperationalError):
        rl._sqlite_backup(source, target)
    assert not target.exists()
    assert list(target.parent.glob('*.tmp')) == []


def test_redact_url_hides_credentials(rl):
    out = rl._redact_url('http://alice:s3cret@127.0.0.1:7897')
    assert 's3cret' not in out and 'alice' not in out
    assert out == 'http://***@127.0.0.1:7897'


def test_redact_url_plain_host_unchanged(rl):
    assert rl._redact_url('http://127.0.0.1:7897') == 'http://127.0.0.1:7897'
