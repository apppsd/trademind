"""Cross-process single-writer lease per (database file, account).

Every entry point that constructs a writing Pipeline (direct CLI, API serve,
auto-paper child) takes this lease before the database is opened, before any
recovery, migration or paid model reservation. The second writer fails fast
with a clear error, zero calls and zero account writes.

Mechanics (ISSUE-20261013-06):
- The lease is an advisory byte-range lock on a dedicated ``.writer.lock``
  file next to the database file, keyed by the account id.
- The kernel releases the lock when the holder process dies, so a crash can
  never leave a stale lock and nothing ever breaks a lock it cannot verify.
- The lock file itself is never deleted or truncated by third parties:
  unlinking a lock file races with a new holder and can produce two writers
  on one inode. Holder metadata inside is diagnostics only, never used for
  staleness decisions.
- The lease key is the resolved database path plus the account id, so two
  accounts sharing one database file can still write concurrently; SQLite
  transactions remain the cross-account guard.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from ..errors import ConfigError


class WriterLockHeld(RuntimeError):
    """Another live process holds the writer lease for this database/account."""

    def __init__(self, lock_path, holder: dict | None = None):
        self.lock_path = str(lock_path)
        self.holder = holder or {}
        known = {k: self.holder.get(k) for k in ('pid', 'host', 'account_id', 'acquired_at')}
        if any(known.values()):
            detail = f"writer lease held elsewhere: {json.dumps(known, ensure_ascii=False)}; lock file {self.lock_path}"
        else:
            detail = f"writer lease held elsewhere (holder metadata unreadable); lock file {self.lock_path}"
        super().__init__(detail)


def database_file(storage_url: str) -> Path:
    """The database file a storage URL names, with exactly the path semantics
    ``Database`` opens: ``sqlite:///rel/path`` is cwd-relative, absolute forms
    stay absolute. Relative spellings are deliberately kept relative (not
    resolved against cwd here) so that relative and absolute URLs naming one
    database produce the same lock file in the same working directory
    (ISSUE-20261013-06); the lease file is created next to the database the
    URL actually opens."""
    from ..storage.sqlite_url import sqlite_url_to_path

    try:
        return sqlite_url_to_path(storage_url)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def lock_path_for(storage_url: str, account_id: str) -> Path:
    db = database_file(storage_url)
    digest = hashlib.sha1(account_id.encode('utf-8')).hexdigest()[:16]
    # Lower-cased name so Windows path-spelling differences share one lease.
    return db.parent / f".{db.name.lower()}.{digest}.writer.lock"


class WriterLease:
    """A held writer lease; release() unlocks, the process exit also does."""

    def __init__(self, path: Path, handle):
        self.path = path
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass  # closing below still drops our possession of the lock
        finally:
            handle.close()

    def __enter__(self) -> 'WriterLease':
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _read_holder(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def acquire_writer_lease(storage_url: str, account_id: str) -> WriterLease | None:
    """Take the exclusive writer lease or raise WriterLockHeld immediately.

    In-memory databases (``:memory:``) are private to their own connection,
    so no cross-process lease exists and ``None`` is returned.
    """
    if storage_url.rstrip('/') == 'sqlite:///:memory:' or storage_url.endswith(':memory:'):
        return None
    path = lock_path_for(storage_url, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 'a+b' creates without truncating any previous holder's metadata.
    handle = open(path, 'a+b')
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\x00')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EDEADLOCK, errno.EWOULDBLOCK, 36):
                    raise WriterLockHeld(path, _read_holder(path)) from exc
                raise
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise WriterLockHeld(path, _read_holder(path)) from exc
                raise
    except WriterLockHeld:
        handle.close()
        raise
    except Exception:
        handle.close()
        raise
    meta = {'pid': os.getpid(), 'host': platform.node(), 'account_id': account_id,
            'db': str(database_file(storage_url)), 'acquired_at': datetime.now(timezone.utc).isoformat()}
    try:
        # We hold the exclusive lock, so rewriting the metadata is safe.
        handle.seek(0)
        handle.truncate(0)
        handle.write(json.dumps(meta, ensure_ascii=False).encode('utf-8'))
        handle.flush()
    except OSError:
        pass  # diagnostics only; the byte-range lock is the actual lease
    return WriterLease(path, handle)
