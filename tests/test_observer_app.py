"""TASK-20261026-07: resident read-only observer app boundaries.

The observer owns port 8321 while the paper writer runs separately. GET
requests must never create/migrate the account DB, and the heartbeat state
file must distinguish "web readable" from "trading healthy".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.api import observer_app, workbench_routes
from trademind.api.observer_app import create_observer_app
from trademind.config import AppConfig


def _cfg(tmp_path):
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'trademind.db'}"
    return cfg


@pytest.fixture()
def observer(tmp_path, monkeypatch):
    # Pin the module config to the temp root: the workbench read layer must
    # never fall through to the developer's real data/runtime in tests.
    cfg = _cfg(tmp_path)
    with TestClient(create_observer_app(cfg)) as client:
        yield client, tmp_path


def test_state_file_running_false_after_shutdown(tmp_path):
    with TestClient(create_observer_app(_cfg(tmp_path))):
        state = json.loads((tmp_path / 'observer-state.json').read_text(encoding='utf-8'))
        assert state['running'] is True
        assert state['pid'] == os.getpid()  # TestClient runs the app in-process
    state = json.loads((tmp_path / 'observer-state.json').read_text(encoding='utf-8'))
    assert state['running'] is False


def test_observer_status_reports_controls_disabled(observer):
    client, tmp = observer
    status = client.get('/api/observer-status').json()
    assert status['controls_available'] is False
    assert status['observer']['running'] is True
    assert status['observer']['heartbeat']


def test_read_endpoints_never_create_db_or_account(observer):
    client, tmp = observer
    for path in ('/api/workbench/overview', '/api/workbench/positions',
                 '/api/workbench/runtime', '/api/workbench/alerts'):
        r = client.get(path)
        assert r.status_code == 200
        env = r.json()
        assert env['schema_version']
        assert env['data_status'] in ('ok', 'partial', 'stale', 'unavailable', 'error')
        assert env['account_alias']
    assert not (tmp / 'trademind.db').exists()  # GET must not migrate or create


def test_instruments_served_from_whitelist_without_db(observer):
    client, tmp = observer
    r = client.get('/api/workbench/instruments', params={'q': '中概'})
    assert r.status_code == 200
    body = r.json()
    found = [i for i in body['instruments'] if i['instrument_id'] == '513050.SH']
    assert found and found[0]['display_name'] == '中概互联ETF'
    assert found[0]['name_source'] == 'whitelist'
    assert not (tmp / 'trademind.db').exists()


def test_workbench_page_falls_back_when_bundle_exists(observer):
    client, tmp = observer
    # The repo bundle is built during this batch, so the page must serve it.
    r = client.get('/workbench/')
    assert r.status_code == 200
    assert '<div id="app">' in r.text


def test_observer_module_never_imports_trading_pipeline():
    src = Path(observer_app.__file__).read_text(encoding='utf-8')
    assert 'get_pipeline' not in src
    assert 'from ..pipeline' not in src
    assert 'ensure_account' not in src
