"""Dead-man watchdog (Task-20261026-24, hardened 2026-10-27): one notification
per incident, recovery clears the marker and re-arms."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest


@pytest.fixture
def wd(monkeypatch):
    scripts = Path(__file__).parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('watchdog', scripts/'watchdog_heartbeat.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state(root, age_seconds, terminal=False):
    now = datetime.now(timezone.utc)
    payload = {'pid': 1, 'account_id': 'acc', 'halted': False,
               'heartbeat': (now-timedelta(seconds=age_seconds)).isoformat()}
    if terminal:
        payload['terminal'] = True
    (Path(root)/'auto-paper-state.json').write_text(json.dumps(payload), encoding='utf-8')


def test_stale_notifies_once_per_incident(wd, tmp_path):
    sent = []
    _state(tmp_path, age_seconds=600)
    assert wd.check(tmp_path, notify=sent.append)[0] == 'stale'
    assert wd.check(tmp_path, notify=sent.append)[0] == 'stale'
    assert len(sent) == 1  # duplicate watchdog runs do not re-notify
    assert (tmp_path/'watchdog-alert.json').exists()


def test_recovery_clears_marker_and_rearms(wd, tmp_path):
    sent = []
    _state(tmp_path, age_seconds=600)
    wd.check(tmp_path, notify=sent.append)
    _state(tmp_path, age_seconds=2)  # heartbeat recovered
    assert wd.check(tmp_path, notify=sent.append)[0] == 'ok'
    assert not (tmp_path/'watchdog-alert.json').exists()
    _state(tmp_path, age_seconds=600)  # a new incident alerts again
    wd.check(tmp_path, notify=sent.append)
    assert len(sent) == 2


def test_terminal_state_never_notifies(wd, tmp_path):
    sent = []
    _state(tmp_path, age_seconds=600, terminal=True)
    assert wd.check(tmp_path, notify=sent.append)[0] == 'halted'
    assert sent == []
