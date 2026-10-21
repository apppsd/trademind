import os

import pytest

from trademind import config
from trademind.errors import ConfigError


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE_PATH", env_path)
    for name in list(os.environ):
        if name.startswith("TRADEMIND_") or name == "DEEPSEEK_API_KEY":
            monkeypatch.delenv(name)
    toml = tmp_path / "config.toml"
    toml.write_text('[llm]\napi_key="toml-test-value"\n', encoding="utf-8")
    return env_path, toml


def test_dotenv_keys_and_process_override(local_config, monkeypatch):
    env_path, toml = local_config
    env_path.write_text('TRADEMIND_DEEPSEEK_API_KEY="dotenv-test-value"\n'
                        'TRADEMIND_JEV_API_KEY=jev-test-value\n', encoding="utf-8")
    cfg = config.load_config(toml)
    assert cfg.llm.api_key == "dotenv-test-value"
    assert cfg.jev.api_key == "jev-test-value"
    assert "TRADEMIND_JEV_API_KEY" not in os.environ
    monkeypatch.setenv("TRADEMIND_DEEPSEEK_API_KEY", "process-test-value")
    assert config.load_config(toml).llm.api_key == "process-test-value"


def test_missing_dotenv_preserves_toml(local_config):
    _, toml = local_config
    assert config.load_config(toml).llm.api_key == "toml-test-value"


def test_dotenv_replay_credentials_are_rejected(local_config):
    env_path, toml = local_config
    toml.write_text('mode="replay"\n', encoding="utf-8")
    env_path.write_text('TRADEMIND_JEV_API_KEY=jev-test-value\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        config.load_config(toml)
