"""TASK-21 API tests: status exposure, stub honesty, control auth, trace."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.api import app as fastapi_app
from trademind.api.app import init_app
from trademind.config import AppConfig


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'api.db'}"
    cfg.api.token = "test-token"
    init_app(cfg)
    return TestClient(fastapi_app)


def test_status_exposes_stub_honestly(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["is_stub_chain"] is True
    assert body["live_available"] is False
    assert "stub" in body["stub_warning"].lower()


def test_account_and_orders_empty_start(client):
    acc = client.get("/api/account").json()
    assert acc["version"] >= 1
    assert client.get("/api/orders").json() == []


def test_control_requires_token(client):
    r = client.post("/api/control/kill_switch?engaged=true")
    assert r.status_code == 401
    r2 = client.post("/api/control/kill_switch?engaged=true",
                     headers={"X-API-Token": "wrong"})
    assert r2.status_code == 401
    r3 = client.post("/api/control/kill_switch?engaged=true&reason=test",
                     headers={"X-API-Token": "test-token"})
    assert r3.status_code == 200
    assert client.get("/api/status").json()["kill_switch"]["engaged"] is True


def test_control_disabled_without_token_configured(tmp_path):
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'api2.db'}"
    cfg.api.token = ""
    init_app(cfg)
    c = TestClient(fastapi_app)
    assert c.post("/api/control/kill_switch?engaged=true").status_code == 403


def test_trace_endpoint_shape(client):
    r = client.get("/api/trace/does-not-exist")
    assert r.status_code == 200
    body = r.json()
    for key in ("model_calls", "theses", "intents", "orders"):
        assert key in body


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "TradeMind" in r.text


def test_instrument_names_serve_whitelist_chinese_names(client):
    # TASK-20261016: workbench renders Chinese fund names instead of raw codes
    r = client.get("/api/instrument-names")
    assert r.status_code == 200
    names = r.json()
    assert names["518880.SH"] == "华安黄金ETF"
    assert names["513100.SH"] == "国泰纳斯达克100ETF"
    assert names["513050.SH"] == "易方达中概互联50ETF"
    assert all(isinstance(v, str) and v for v in names.values())
