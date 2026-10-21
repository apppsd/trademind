"""TASK-11 provider tests: stub marking, resilience, redaction, refusal."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.config import AppConfig
from trademind.errors import CircuitOpen, ConfigError, ProviderError
from trademind.models import (
    Budget,
    CallAuditor,
    CircuitBreaker,
    StubJEVProvider,
    StubLLMProvider,
    build_jev,
    build_llm,
    extract_json,
)
from trademind.storage import Database

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture()
def repo(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'t.db'}")
    from trademind.storage import Repository

    yield Repository(db)
    db.close()


def test_stub_is_explicitly_marked(repo):
    stub = StubLLMProvider(auditor=CallAuditor(repo))
    out = stub.complete(role="trader", system="s", user="u", trace_id="tr1")
    assert out.call.is_stub and out.call.status == "stub"
    assert out.call.capability_verified is False
    assert out.parsed["action"] == "HOLD"
    rows = repo.db.query("SELECT * FROM model_calls")
    assert rows and rows[0]["is_stub"] == 1


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('noise before {"a": {"b": 2}} noise after') == {"a": {"b": 2}}
    assert extract_json("no json here") is None
    assert extract_json("") is None


def test_circuit_breaker_opens():
    cb = CircuitBreaker(failure_threshold=2, cooldown_s=60)
    cb.record_failure()
    cb.check()  # still closed
    cb.record_failure()
    with pytest.raises(CircuitOpen):
        cb.check()


def test_budget_exhaustion():
    b = Budget(limit=2)
    assert b.try_consume() and b.try_consume()
    assert not b.try_consume()
    assert b.exhausted


def test_auto_without_key_builds_stub(repo):
    cfg = AppConfig()
    cfg.llm.provider = "auto"
    cfg.llm.api_key = ""
    provider, is_stub = build_llm(cfg, repo)
    assert is_stub and isinstance(provider, StubLLMProvider)


def test_deepseek_requires_key(repo):
    from trademind.models import DeepSeekProvider

    with pytest.raises(ProviderError):
        DeepSeekProvider(api_key="")


def test_jev_http_requires_base_url(repo):
    cfg = AppConfig()
    cfg.jev.provider = "http"
    cfg.jev.base_url = ""
    with pytest.raises(ProviderError):
        build_jev(cfg, repo)


def test_jev_stub_answers_all_questions(repo):
    stub = StubJEVProvider()
    batch = stub.judge(
        context_id="c1", thesis_id=None, instrument_id="600519.SH", as_of=T0,
        questions=[{"question_id": "continuation"}, {"question_id": "adverse_move"}],
        trace_id="tr2",
    )
    assert batch.is_stub and len(batch.judgments) == 2
    assert batch.judgments[0].probability is not None


def test_audit_redacts_secrets(repo):
    auditor = CallAuditor(repo)
    assert "[REDACTED]" in auditor.redact("error near sk-abcdefgh12345678 token")
