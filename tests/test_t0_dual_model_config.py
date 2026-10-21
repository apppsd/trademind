"""Tests for TASK-52: JEV + deepseek-flash dual model configuration and readiness checks."""

import pytest
from trademind.config import load_config, ConfigError
from trademind.models.provider_factory import build_llm, build_jev
from trademind.models.deepseek import DeepSeekProvider
from trademind.models.jev import TypeSafeJEVProvider
from trademind.storage import Repository


def test_t0_dual_model_config_defaults():
    cfg = load_config("config/t0-shadow.example.toml")
    assert cfg.t0.enabled is True
    assert cfg.t0.profile == "jev_deepseek"
    assert cfg.llm.model == "deepseek-flash"
    assert cfg.jev.provider == "jev_typesafe"
    assert cfg.t0.daily_model_cost_limit_cny == 5.0
    assert cfg.t0.daily_decision_call_budget == 100


def test_t0_rejects_non_flash_model(tmp_path):
    bad_toml = tmp_path / "bad_model.toml"
    bad_toml.write_text(
        """
mode = "paper"
[t0]
enabled = true
profile = "jev_deepseek"

[llm]
model = "deepseek-v4-pro"
"""
    )
    with pytest.raises(ConfigError, match="requires llm.model='deepseek-flash'"):
        load_config(bad_toml)


def test_t0_dual_provider_construction(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path}/repo.db")
    cfg = load_config("config/t0-shadow.example.toml")
    cfg.llm.api_key = "test-deepseek-key"
    cfg.jev.api_key = "test-typesafe-key"

    llm_prov, llm_is_stub = build_llm(cfg, repo)
    jev_prov, jev_is_stub = build_jev(cfg, repo)

    assert isinstance(llm_prov, DeepSeekProvider)
    assert llm_is_stub is False
    assert llm_prov.model == "deepseek-flash"

    assert isinstance(jev_prov, TypeSafeJEVProvider)
    assert jev_is_stub is False
    assert jev_prov.model == "SystemOne"
