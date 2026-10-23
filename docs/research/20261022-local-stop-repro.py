r"""Isolated review reproducer: no real child, account, API or live state used.

Run: .venv\Scripts\python.exe docs/research/20261022-local-stop-repro.py
The assertions confirm the defects at review time; fixes should change them.
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from trademind.execution.auto_paper import Supervisor
import subprocess


class FakeChild:
    def __init__(self):
        self.done = False

    def poll(self):
        return 0 if self.done else None

    def kill(self):
        self.done = True

    def wait(self, timeout=None):
        if not self.done:
            raise subprocess.TimeoutExpired('fake-child', timeout)
        return 0


def supervisor(root, launch):
    calendar = Mock()
    calendar.covers.return_value = True
    calendar.is_session_open.return_value = False
    return Supervisor('review-unused.toml', root, calendar, launch,
                      lambda: datetime(2026, 10, 22, 7, 10, tzinfo=timezone.utc),
                      account_id='isolated-review', db_path=root/'absent.db')


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    launches = []

    def launch(command):
        launches.append(command)
        return FakeChild()

    sup = supervisor(root, launch)
    sup.child, sup.kind = FakeChild(), 'serve'
    (root/'auto-paper.stop').write_text('stop', encoding='utf-8')
    sup.tick()
    assert sup.stopping and len(launches) == 1 and 't0-paper' in launches[0]
    print('BUG-1: stopping from serve constructed a new t0-paper command; kind=None')
    sup.stop_child()

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    state = root/'auto-paper-state.json'
    state.write_text(json.dumps({'halted': False}), encoding='utf-8')
    sup = supervisor(root, lambda command: FakeChild())
    sup.child, sup.kind, sup.stopping = FakeChild(), 'paper', True
    sup.run()  # finally -> stop_child -> forced termination of fake child
    assert sup.halted and not json.loads(state.read_text(encoding='utf-8'))['halted']
    print('BUG-2: forced paper stop halted in memory, but persisted halted remained false')
