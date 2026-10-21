"""Tests for TASK-41: TypeSafe JEV provider and dual-model verification."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
import requests

from trademind.config import AppConfig
from trademind.domain.base import utcnow
from trademind.domain.enums import ConfidenceSemantics, ProviderKind
from trademind.errors import ConfigError, ProviderError, ProviderTimeout
from trademind.models.deepseek import DeepSeekProvider
from trademind.models.jev import TypeSafeJEVProvider
from trademind.models.provider_factory import build_jev, build_llm
from trademind.storage import Repository


class MockResponse:
    def __init__(self, status_code: int, data: dict, headers: dict | None = None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def test_typesafe_jev_probe_success(monkeypatch):
    provider = TypeSafeJEVProvider(
        base_url="https://api.typesafe.ai",
        api_key="test-typesafe-key",
        model="jev-latest",
    )
    assert provider.capability_verified is False

    def mock_post(url, json=None, headers=None, timeout=None):
        assert url == "https://api.typesafe.ai/v1/systemone"
        assert json["model"] == "jev-latest"
        assert "questions" in json
        return MockResponse(
            200,
            {
                "model": "jev-systemone-v1",
                "answers": {
                    "thesis_supported": {
                        "type": "noul", "noul": 0.95,
                    }
                },
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        )

    monkeypatch.setattr(provider.session, "post", mock_post)
    res = provider.probe()
    assert res["ok"] is True
    assert res["status_code"] == 200
    assert res["model"] == "jev-systemone-v1"
    assert provider.capability_verified is True


def test_typesafe_jev_probe_401(monkeypatch):
    provider = TypeSafeJEVProvider(
        base_url="https://api.typesafe.ai",
        api_key="invalid-key",
        model="jev-latest",
    )

    def mock_post(url, json=None, headers=None, timeout=None):
        return MockResponse(401, {"error": "Invalid API key"})

    monkeypatch.setattr(provider.session, "post", mock_post)
    res = provider.probe()
    assert res["ok"] is False
    assert res["status_code"] == 401
    assert "401" in res["error"]
    assert provider.capability_verified is False


def test_typesafe_jev_judge_contract(monkeypatch):
    provider = TypeSafeJEVProvider(
        base_url="https://api.typesafe.ai",
        api_key="valid-key",
        model="jev-latest",
    )

    def mock_post(url, json=None, headers=None, timeout=None):
        assert url == "https://api.typesafe.ai/v1/systemone"
        assert "thesis_supported" in json["questions"]
        assert "liquidity_healthy" in json["questions"]
        return MockResponse(
            200,
            {
                "model": "jev-systemone-v1",
                "usage": {"input_tokens":50,"output_tokens":10},
                "answers": {
                    "thesis_supported": {
                        "type": "choice",
                        "choice": "yes", "probabilities": {"yes": 0.92, "no": 0.04, "unsure": 0.04},
                        "confidence": 0.88,
                    },
                    "liquidity_healthy": {
                        "type": "score",
                        "score": 0.75, "legend": {"0":"yes","1":"no","2":"unsure"},
                        "probabilities": {"0":0.25,"1":0.75,"2":0}, "confidence": 0.80,
                    },
                },
            },
        )

    monkeypatch.setattr(provider.session, "post", mock_post)
    batch = provider.judge(
        context_id="ctx-100",
        thesis_id="the-100",
        instrument_id="510050.SH",
        as_of=datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc),
        questions=[
            {"question_id": "thesis_supported", "type": "choice"},
            {"question_id": "liquidity_healthy", "type": "score"},
            {"question_id": "entry_suitable", "type": "choice"},  # missing in response
        ],
        trace_id="tr-100",
        state={"context_id": "ctx-100", "price": "2.85"},
    )
    assert batch.provider == ProviderKind.JEV_TYPESAFE
    assert batch.model == "jev-systemone-v1"
    assert len(batch.judgments) == 3

    j0 = batch.judgments[0]
    assert j0.question_id == "thesis_supported"
    assert j0.value == "yes"
    assert j0.probability == Decimal("0.88")
    assert j0.probability_semantics == ConfidenceSemantics.ANSWER_DISTRIBUTION_CONFIDENCE

    j1 = batch.judgments[1]
    assert j1.question_id == "liquidity_healthy"
    assert j1.value == "0.75"
    assert j1.probability == Decimal("0.80")

    j2 = batch.judgments[2]
    assert j2.question_id == "entry_suitable"
    assert j2.missing_reason == "question_missing_in_jev_response"


def test_provider_factory_typesafe_jev(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path}/test.db")
    cfg = AppConfig()
    cfg.jev.provider = "jev_typesafe"
    cfg.jev.api_key = "test-key"
    provider, is_stub = build_jev(cfg, repo)
    assert isinstance(provider, TypeSafeJEVProvider)
    assert is_stub is False

    cfg_no_key = AppConfig()
    cfg_no_key.jev.provider = "jev_typesafe"
    cfg_no_key.jev.api_key = ""
    with pytest.raises(ConfigError, match="api_key missing"):
        build_jev(cfg_no_key, repo)


def test_deepseek_probe_mock(monkeypatch):
    dp = DeepSeekProvider(api_key="test-ds-key", model="deepseek-flash")
    assert dp.capability_verified is False

    def mock_get(url, timeout=None):
        return MockResponse(200, {"data": [{"id": "deepseek-flash"}, {"id": "deepseek-chat"}]})

    monkeypatch.setattr(dp.session, "get", mock_get)
    res = dp.probe()
    assert res["ok"] is True
    assert "deepseek-flash" in res["models"]
    assert dp.capability_verified is True
