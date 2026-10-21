"""Cross-process single-writer lease (TASK-20261013-14 / ISSUE-20261013-06).

Real OS subprocesses contend for one database/account; in-process mocks alone
never prove the kernel-level exclusion.
"""
import os
import subprocess
import sys
import textwrap
import time
from decimal import Decimal
from pathlib import Path

import pytest

from trademind.config import AppConfig
from trademind.execution.writer_lock import (
    WriterLockHeld,
    acquire_writer_lease,
    database_file,
    lock_path_for,
)
from trademind.pipeline import Pipeline

I = '513050.SH'
# Subprocesses do not inherit pytest's sys.path setup (src layout).
SRC = str(Path(__file__).resolve().parents[1] / 'src')
SUBPROCESS_ENV = {**os.environ,
                  'PYTHONPATH': SRC + os.pathsep + os.environ.get('PYTHONPATH', '')}


def make_cfg(tmp_path, account='writer-a', url=None):
    cfg = AppConfig()
    cfg.t0.enabled = True
    cfg.account_id = account
    cfg.universe = [I]
    cfg.storage.url = url or f'sqlite:///{tmp_path}/w.db'
    cfg.data.provider_pref = ['fixture']
    return cfg


def test_lock_key_uses_db_name_and_account(tmp_path):
    base = tmp_path / 'data'
    base.mkdir()
    a = lock_path_for(f'sqlite:///{base.as_posix()}/w.db', 'acc')
    # A longer spelling that lands on the same file shares the lease file.
    b = lock_path_for(f'sqlite:///{base.as_posix()}/sub/../w.db', 'acc')
    assert a.resolve() == b.resolve()
    assert a.name.startswith('.w.db.') and a.name.endswith('.writer.lock')
    assert lock_path_for('sqlite:///{}/W.DB'.format(base.as_posix()), 'acc') == \
        lock_path_for('sqlite:///{}/w.db'.format(base.as_posix()), 'acc')
    assert lock_path_for('sqlite:///{}/w.db'.format(base.as_posix()), 'acc') != \
        lock_path_for('sqlite:///{}/w.db'.format(base.as_posix()), 'other')


# -- ISSUE-20261013-06 reopen: relative vs absolute URL of one database --------

def test_relative_url_stays_relative_like_database(tmp_path, monkeypatch):
    from trademind.storage import Database
    from trademind.storage.sqlite_url import sqlite_url_to_path

    monkeypatch.chdir(tmp_path)
    rel = 'sqlite:///nested/w.db'
    absolute = f'sqlite:///{(tmp_path / "nested" / "w.db").as_posix()}'
    # Both modules must derive the same file from the relative spelling —
    # keeping it relative is what makes the two spellings share one lease.
    assert sqlite_url_to_path(rel) == Path('nested') / 'w.db'
    assert database_file(rel) == Path('nested') / 'w.db'
    assert database_file(rel).resolve() == database_file(absolute).resolve()
    assert lock_path_for(rel, 'acc').resolve() == lock_path_for(absolute, 'acc').resolve()
    db = Database(rel)
    try:
        assert db.path == Path('nested') / 'w.db'
        assert db.path.resolve() == (tmp_path / 'nested' / 'w.db').resolve()
    finally:
        db.close()


_REL_HOLDER = textwrap.dedent("""
    import sys, time
    from trademind.execution.writer_lock import acquire_writer_lease, WriterLockHeld
    try:
        lease = acquire_writer_lease({url!r}, 'cross-proc')
    except WriterLockHeld:
        print('DENIED', flush=True)
        sys.exit(3)
    print('ACQUIRED', flush=True)
    time.sleep(20)
""")


def test_relative_url_holder_excludes_absolute_url_rival(tmp_path):
    """The Issue's counterexample as a real OS race: a process holding the
    lease through the cwd-relative URL denies a rival that uses the absolute
    spelling of the same database. A relative URL from a different cwd names
    a different database and must still be allowed."""
    nested = tmp_path / 'nested'
    nested.mkdir()
    rel = 'sqlite:///nested/w.db'
    absolute = f'sqlite:///{(nested / "w.db").as_posix()}'
    holder = subprocess.Popen([sys.executable, '-c', _REL_HOLDER.format(url=rel)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=SUBPROCESS_ENV, cwd=str(tmp_path))
    other_dir = tmp_path / 'other-cwd'
    other_dir.mkdir()
    stranger = subprocess.Popen([sys.executable, '-c', _REL_HOLDER.format(url=rel)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=SUBPROCESS_ENV, cwd=str(other_dir))
    try:
        assert holder.stdout.readline().strip() == 'ACQUIRED'
        assert stranger.stdout.readline().strip() == 'ACQUIRED'
        # Two lease files exist — one per actual database directory.
        assert lock_path_for(absolute, 'cross-proc').exists()
        assert (other_dir / 'nested' / lock_path_for(absolute, 'cross-proc').name).exists()
        # The absolute spelling of the holder's database is denied (same file,
        # same lease) — this is exactly the mixed-spelling counterexample.
        with pytest.raises(WriterLockHeld):
            acquire_writer_lease(absolute, 'cross-proc')
    finally:
        holder.terminate()
        stranger.terminate()
        holder.wait(timeout=30)
        stranger.wait(timeout=30)
    deadline = time.monotonic() + 10
    while True:
        try:
            lease = acquire_writer_lease(absolute, 'cross-proc')
            break
        except WriterLockHeld:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    lease.release()


def test_non_local_storage_rejected():
    with pytest.raises(Exception):
        database_file('postgresql://localhost/tm')
    with pytest.raises(Exception):
        database_file('sqlite://remotehost/tm.db')


def test_second_pipeline_same_account_zero_writes(tmp_path):
    pipe = Pipeline(make_cfg(tmp_path))
    pipe.ensure_account()
    events_before = len(pipe.repo.account_events('writer-a'))
    with pytest.raises(WriterLockHeld):
        Pipeline(make_cfg(tmp_path))
    # The losing writer opened no database and wrote nothing.
    assert len(pipe.repo.account_events('writer-a')) == events_before
    assert not pipe.db.query('SELECT * FROM orders')
    assert not pipe.db.query('SELECT * FROM model_calls')


def test_release_then_reacquire(tmp_path):
    pipe = Pipeline(make_cfg(tmp_path))
    pipe.release_writer_lease()
    second = Pipeline(make_cfg(tmp_path))
    assert second._writer_lease is not None
    second.release_writer_lease()


def test_different_accounts_same_database_both_write(tmp_path):
    a = Pipeline(make_cfg(tmp_path, account='writer-a'))
    b = Pipeline(make_cfg(tmp_path, account='writer-b'))
    a.ensure_account()
    b.ensure_account()
    a.release_writer_lease()
    b.release_writer_lease()


_HOLDER = textwrap.dedent("""
    import sys, time
    from trademind.execution.writer_lock import acquire_writer_lease, WriterLockHeld
    try:
        lease = acquire_writer_lease({url!r}, 'cross-proc')
    except WriterLockHeld:
        print('DENIED', flush=True)
        sys.exit(3)
    print('ACQUIRED', flush=True)
    time.sleep(20)
""")


def test_cross_process_second_writer_denied_then_release_on_death(tmp_path):
    url = f'sqlite:///{(tmp_path / "cross.db").as_posix()}'
    holder = subprocess.Popen([sys.executable, '-c', _HOLDER.format(url=url)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=SUBPROCESS_ENV)
    try:
        assert holder.stdout.readline().strip() == 'ACQUIRED'
        rival = subprocess.run([sys.executable, '-c', _HOLDER.format(url=url)],
                               capture_output=True, text=True, timeout=60, env=SUBPROCESS_ENV)
        assert rival.returncode == 3 and 'DENIED' in rival.stdout
        # Same-process attempt conflicts too (separate file handles).
        with pytest.raises(WriterLockHeld):
            acquire_writer_lease(url, 'cross-proc')
    finally:
        holder.terminate()
        holder.wait(timeout=30)
    # The kernel released the lock when the holder process died: no stale
    # PID file, nothing to break manually. On Windows the byte-range lock
    # can be torn down a moment after the process object is signaled, so
    # poll briefly instead of racing kernel cleanup; a genuine leak still
    # fails at the deadline.
    deadline = time.monotonic() + 10
    while True:
        try:
            lease = acquire_writer_lease(url, 'cross-proc')
            break
        except WriterLockHeld:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    lease.release()


def test_cli_t0_paper_second_writer_nonzero_zero_side_effects(tmp_path):
    db = tmp_path / 'cli.db'
    toml = tmp_path / 'cli.toml'
    toml.write_text(textwrap.dedent(f"""
        mode = "paper"
        account_id = "writer-cli"
        [storage]
        url = "sqlite:///{db.as_posix()}"
        [data]
        provider_pref = ["fixture"]
        [t0]
        enabled = true
        profile = "jev_fast_decision"
    """), encoding='utf-8')
    lease = acquire_writer_lease(f'sqlite:///{db.as_posix()}', 'writer-cli')
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'trademind.runner', '--config', str(toml),
             't0-paper', '--steps', '1'],
            capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
            env=SUBPROCESS_ENV)
        assert result.returncode == 1
        assert 'WriterLockHeld' in result.stdout
        # Losing writer never opened the database file at all.
        assert not db.exists()
    finally:
        lease.release()


def test_read_only_daily_report_needs_no_writer_lease(tmp_path):
    from trademind.reports.daily import generate_for_config
    pipe = Pipeline(make_cfg(tmp_path))
    pipe.ensure_account()
    # The writer lease is held by `pipe` right now; the read-only report path
    # opens its own connection and must still work.
    report, output = generate_for_config(make_cfg(tmp_path), '2026-10-12')
    assert report['date'] == '2026-10-12'
    pipe.release_writer_lease()
