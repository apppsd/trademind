"""TASK-20261021-01 admin runtime settings: auth, masking, allowlist, precedence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind import config
from trademind.api import app as fastapi_app
from trademind.api.app import init_app
from trademind.config import (AppConfig, apply_admin_overrides,
                              load_admin_overrides, mask_setting,
                              save_admin_override)

SECRET = "sk-test1234567890abcd"
TOKEN = {"X-API-Token": "admin-token"}


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "admin_settings.json"
    monkeypatch.setenv("TRADEMIND_ADMIN_SETTINGS_PATH", str(p))
    return p


def _client(tmp_path, token_value="admin-token", db_name="admin.db"):
    # Each init_app takes the exclusive writer lease, so every client in a
    # test needs its own storage file (Windows lock is per-path).
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path / db_name}"
    cfg.api.token = token_value
    init_app(cfg)
    return TestClient(fastapi_app)


@pytest.fixture()
def client(tmp_path, settings_file):
    return _client(tmp_path)


def test_admin_endpoints_require_token(tmp_path, settings_file):
    no_token = _client(tmp_path, token_value="", db_name="a.db")
    assert no_token.get("/api/admin/settings").status_code == 403
    c = _client(tmp_path, db_name="b.db")
    assert c.get("/api/admin/settings", headers={"X-API-Token": "wrong"}).status_code == 401
    assert c.put("/api/admin/settings/deepseek_api_key", json={"value": SECRET},
                 headers={"X-API-Token": "wrong"}).status_code == 401
    assert c.delete("/api/admin/settings/deepseek_api_key",
                    headers={"X-API-Token": "wrong"}).status_code == 401


def test_put_masks_and_full_secret_never_exposed(client):
    r = client.put("/api/admin/settings/deepseek_api_key", json={"value": SECRET}, headers=TOKEN)
    assert r.status_code == 200
    assert r.json()["masked"] == "****abcd"
    assert SECRET not in r.text
    listing = client.get("/api/admin/settings", headers=TOKEN)
    assert SECRET not in listing.text
    item = next(s for s in listing.json()["settings"] if s["key"] == "deepseek_api_key")
    assert item["masked"] == "****abcd"
    assert item["set"] is True and item["source"] == "admin"


def test_unknown_key_and_bad_values_rejected(client):
    assert client.put("/api/admin/settings/llm.model",
                      json={"value": "x"}, headers=TOKEN).status_code == 400
    assert client.put("/api/admin/settings/deepseek_api_key",
                      json={"value": "   "}, headers=TOKEN).status_code == 400
    assert client.put("/api/admin/settings/deepseek_api_key",
                      json={"value": "a\nb"}, headers=TOKEN).status_code == 400
    assert client.delete("/api/admin/settings/llm.model", headers=TOKEN).status_code == 400


def test_override_applies_and_env_wins(tmp_path, settings_file, monkeypatch):
    # Mirror the real load order: .env/process env first, admin override second.
    monkeypatch.setattr(config, "ENV_FILE_PATH", tmp_path / ".env")  # absent
    monkeypatch.delenv("TRADEMIND_DEEPSEEK_API_KEY", raising=False)
    save_admin_override("deepseek_api_key", SECRET)
    cfg = AppConfig()
    config._apply_env(cfg)
    apply_admin_overrides(cfg)
    assert cfg.llm.api_key == SECRET
    monkeypatch.setenv("TRADEMIND_DEEPSEEK_API_KEY", "env-wins-12345678")
    cfg2 = AppConfig()
    config._apply_env(cfg2)
    apply_admin_overrides(cfg2)
    assert cfg2.llm.api_key == "env-wins-12345678"


def test_load_config_applies_override_and_isolation_kept(tmp_path, settings_file, monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE_PATH", tmp_path / ".env")  # absent
    for name in list(os.environ):
        if name.startswith("TRADEMIND_") or name == "DEEPSEEK_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.setenv("TRADEMIND_ADMIN_SETTINGS_PATH", str(settings_file))
    toml = tmp_path / "config.toml"
    toml.write_text('mode="paper"\n', encoding="utf-8")
    save_admin_override("deepseek_api_key", SECRET)
    assert config.load_config(toml).llm.api_key == SECRET
    assert config.load_config(toml, load_credentials=False).llm.api_key == ""


def test_delete_clears_override(client, settings_file):
    client.put("/api/admin/settings/jev_api_key",
               json={"value": "jev-key-12345678"}, headers=TOKEN)
    assert load_admin_overrides()["jev_api_key"] == "jev-key-12345678"
    assert client.delete("/api/admin/settings/jev_api_key", headers=TOKEN).status_code == 200
    assert "jev_api_key" not in load_admin_overrides()


def test_mask_short_secret_and_plain_url():
    assert mask_setting("deepseek_api_key", "short") == "****"
    assert mask_setting("llm_base_url", "https://api.deepseek.com") == "https://api.deepseek.com"


def test_corrupted_file_degrades_to_empty(settings_file):
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text("{not json", encoding="utf-8")
    assert load_admin_overrides() == {}
    apply_admin_overrides(AppConfig())  # must not raise
