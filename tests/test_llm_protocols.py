"""TASK-20261021-02 multi-protocol LLM adapters: request shapes, response
parsing, failure classification, repair rounds, config/admin surface.

All tests are offline (mocked HTTP); no real vendor calls are made.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind import config
from trademind.config import VALID_LLM_PROTOCOLS, AppConfig
from trademind.errors import ConfigError, ProviderError, ProviderRefused
from trademind.models import DeepSeekProvider, MultiProtocolLLMProvider
from trademind.models import protocols
from trademind.models.base import Budget
from trademind.storage import Repository
from trademind.models.provider_factory import build_llm

GOOD_JSON = '{"action":"HOLD","confidence":"0.5"}'


def _mock_response(body, status_code=200):
    resp = Mock(status_code=status_code)
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    resp.raise_for_status = Mock()
    return resp


def _provider(protocol, **kw):
    kw.setdefault("budget", Budget(limit=10**9))
    return MultiProtocolLLMProvider("TEST_ONLY_KEY", protocol=protocol, **kw)


def _ok_body(protocol):
    if protocol == "openai_chat":
        return {"choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": GOOD_JSON}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    if protocol == "anthropic_messages":
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                "content": [{"type": "text", "text": GOOD_JSON}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    if protocol == "openai_responses":
        return {"id": "resp_1", "object": "response", "status": "completed", "model": "m",
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": GOOD_JSON}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                          "input_tokens_details": {"cached_tokens": 2},
                          "output_tokens_details": {"reasoning_tokens": 0}}}
    return {"candidates": [{"content": {"parts": [{"text": GOOD_JSON}], "role": "model"},
                            "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5,
                              "totalTokenCount": 15}}


def test_alias_keeps_isinstance_checks():
    assert DeepSeekProvider is MultiProtocolLLMProvider


@pytest.mark.parametrize("protocol", sorted(VALID_LLM_PROTOCOLS))
def test_complete_ok_per_protocol(protocol):
    p = _provider(protocol)
    p.session.post = Mock(return_value=_mock_response(_ok_body(protocol)))
    out = p.complete(role="strategy", system="json", user="{}", trace_id="tr-ok")
    assert out.parsed == {"action": "HOLD", "confidence": "0.5"}
    call = out.call
    assert call.status == "ok" and call.failure_category == "none"
    assert call.input_tokens == 10 and call.output_tokens == 5
    assert call.finish_reason == "stop"
    assert call.model_params["protocol"] == protocol
    diag = call.response_diagnostics
    assert diag["usage_known"] is True and diag["content_empty"] is False


def test_usage_details_normalized_per_protocol():
    # anthropic: total derived; responses: cached from input details; gemini: totalTokenCount
    p = _provider("anthropic_messages")
    p.session.post = Mock(return_value=_mock_response(_ok_body("anthropic_messages")))
    call = p.complete(role="s", system="j", user="u", trace_id="t").call
    assert call.response_diagnostics["total_tokens"] == 15
    p = _provider("openai_responses")
    p.session.post = Mock(return_value=_mock_response(_ok_body("openai_responses")))
    call = p.complete(role="s", system="j", user="u", trace_id="t").call
    assert call.response_diagnostics["cached_tokens"] == 2
    p = _provider("gemini_generate_content")
    p.session.post = Mock(return_value=_mock_response(_ok_body("gemini_generate_content")))
    call = p.complete(role="s", system="j", user="u", trace_id="t").call
    assert call.response_diagnostics["total_tokens"] == 15


def test_request_shapes_and_auth_headers():
    # openai_chat keeps the DeepSeek-specific extras and Bearer auth
    p = _provider("openai_chat")
    url, payload = protocols.build_request(
        "openai_chat", base_url=p.base_url, model="deepseek-flash", system="S", user="U",
        history=[], temperature=0.2, max_tokens=1024, thinking="disabled")
    assert url == "https://api.deepseek.com/chat/completions"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 1024
    assert p.session.headers["Authorization"] == "Bearer TEST_ONLY_KEY"

    # anthropic_messages: native contract (system top-level, max_tokens required)
    url, payload = protocols.build_request(
        "anthropic_messages", base_url="https://api.anthropic.com", model="m",
        system="S", user="U", history=[("assistant", "a1"), ("user", "u1")],
        temperature=0.2, max_tokens=None, thinking="disabled")
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload["max_tokens"] == 4096  # required field, budget cap absent -> default
    assert payload["system"] == "S"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    headers = protocols.session_headers("anthropic_messages", "K")
    assert headers["x-api-key"] == "K" and headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers

    # openai_responses: instructions + typed input items + max_output_tokens
    url, payload = protocols.build_request(
        "openai_responses", base_url="https://api.openai.com/v1", model="m",
        system="S", user="U", history=[], temperature=0.2, max_tokens=1024,
        thinking="disabled")
    assert url == "https://api.openai.com/v1/responses"
    assert payload["instructions"] == "S"
    assert payload["max_output_tokens"] == 1024
    assert payload["input"][0]["content"][0]["type"] == "input_text"
    assert protocols.session_headers("openai_responses", "K")["Authorization"] == "Bearer K"

    # gemini_generate_content: systemInstruction/generationConfig, x-goog-api-key
    url, payload = protocols.build_request(
        "gemini_generate_content", base_url="https://generativelanguage.googleapis.com",
        model="gemini-flash-x", system="S", user="U",
        history=[("assistant", "a1")], temperature=0.2, max_tokens=1024,
        thinking="disabled")
    assert url == ("https://generativelanguage.googleapis.com"
                   "/v1beta/models/gemini-flash-x:generateContent")
    assert payload["systemInstruction"]["parts"][0]["text"] == "S"
    assert payload["generationConfig"]["maxOutputTokens"] == 1024
    assert [c["role"] for c in payload["contents"]] == ["user", "model"]
    assert protocols.session_headers("gemini_generate_content", "K")["x-goog-api-key"] == "K"


@pytest.mark.parametrize("protocol,body,exc,cat", [
    ("openai_chat",
     {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}],
      "usage": {"prompt_tokens": 1, "completion_tokens": 0}},
     ProviderRefused, "explicit_refusal"),
    ("anthropic_messages",
     {"content": [{"type": "text", "text": ""}], "stop_reason": "refusal",
      "usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderRefused, "explicit_refusal"),
    ("openai_responses",
     {"object": "response", "status": "completed",
      "output": [{"type": "message", "role": "assistant",
                  "content": [{"type": "refusal", "refusal": "no"}]}],
      "usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderRefused, "explicit_refusal"),
    ("gemini_generate_content",
     {"candidates": [{"content": {"parts": [{"text": ""}], "role": "model"},
                      "finishReason": "SAFETY"}],
      "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0}},
     ProviderRefused, "explicit_refusal"),
    ("gemini_generate_content",
     {"promptFeedback": {"blockReason": "SAFETY"},
      "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0}},
     ProviderRefused, "explicit_refusal"),
    ("anthropic_messages",
     {"content": [{"type": "text", "text": ""}], "stop_reason": "max_tokens",
      "usage": {"input_tokens": 10, "output_tokens": 100}},
     ProviderError, "length_truncated"),
    ("gemini_generate_content",
     {"candidates": [{"content": {"parts": [{"text": ""}], "role": "model"},
                      "finishReason": "MAX_TOKENS"}],
      "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 100}},
     ProviderError, "length_truncated"),
    ("openai_responses",
     {"object": "response", "status": "incomplete",
      "incomplete_details": {"reason": "max_output_tokens"}, "output": [],
      "usage": {"input_tokens": 10, "output_tokens": 100}},
     ProviderError, "length_truncated"),
    ("openai_chat",
     {"usage": {"prompt_tokens": 1, "completion_tokens": 0}},
     ProviderError, "protocol_error"),
    ("anthropic_messages",
     {"usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderError, "protocol_error"),
    ("openai_responses",
     {"object": "response", "status": "completed",
      "usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderError, "protocol_error"),
    ("gemini_generate_content",
     {"usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0}},
     ProviderError, "protocol_error"),
    ("openai_responses",
     {"object": "response", "status": "failed", "error": {"message": "overloaded"},
      "usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderError, "protocol_error"),
    ("anthropic_messages",
     {"content": [{"type": "text", "text": 123}], "stop_reason": "end_turn",
      "usage": {"input_tokens": 1, "output_tokens": 0}},
     ProviderError, "protocol_error"),
])
def test_failure_classification_per_protocol(protocol, body, exc, cat):
    p = _provider(protocol, max_repair_attempts=0)
    p.session.post = Mock(return_value=_mock_response(body))
    with pytest.raises(exc):
        p.complete(role="strategy", system="json", user="{}", trace_id="tr-f")
    call = p.last_call
    assert call.status == ("refused" if exc is ProviderRefused else "error")
    assert call.failure_category == cat


def test_repair_round_appends_protocol_history():
    p = _provider("anthropic_messages", max_repair_attempts=1)
    first = _mock_response({"content": [{"type": "text", "text": "oops not json"}],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 5, "output_tokens": 3}})
    second = _mock_response(_ok_body("anthropic_messages"))
    p.session.post = Mock(side_effect=[first, second])
    out = p.complete(role="strategy", system="json", user="U", trace_id="tr-repair")
    assert out.call.status == "repaired" and out.call.attempts == 2
    followup = p.session.post.call_args_list[1].kwargs["json"]["messages"]
    assert [m["role"] for m in followup] == ["user", "assistant", "user"]
    assert followup[1]["content"] == "oops not json"
    assert followup[2]["content"] == protocols.REPAIR_INSTRUCTION


def test_effective_base_url_resolution():
    # unset / DeepSeek default -> protocol official endpoint
    assert protocols.effective_base_url("anthropic_messages", "https://api.deepseek.com") \
        == "https://api.anthropic.com"
    assert protocols.effective_base_url("gemini_generate_content", "") \
        == "https://generativelanguage.googleapis.com"
    assert protocols.effective_base_url("openai_responses", "https://api.deepseek.com/") \
        == "https://api.openai.com/v1"
    # explicit custom (relay) URL kept verbatim
    assert protocols.effective_base_url("anthropic_messages", "https://relay.example.com") \
        == "https://relay.example.com"
    # provider init resolves the same way
    assert _provider("anthropic_messages").base_url == "https://api.anthropic.com"
    relay = MultiProtocolLLMProvider("K", protocol="openai_responses",
                                     base_url="https://my-relay.example/v1")
    assert relay.base_url == "https://my-relay.example/v1"
    with pytest.raises(ProviderError, match="unknown llm protocol"):
        MultiProtocolLLMProvider("K", protocol="websocket")


def test_gemini_probe_strips_models_prefix_and_anthropic_headers():
    p = _provider("gemini_generate_content", model="gemini-flash-x")

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"models": [{"name": "models/gemini-flash-x"}, {"name": "models/other"}]}

    p.session.get = Mock(return_value=R())
    res = p.probe()
    assert res["ok"] is True and "gemini-flash-x" in res["models"]
    assert p.session.get.call_args.args[0].endswith("/v1beta/models")
    assert p.capability_verified is True

    a = _provider("anthropic_messages")
    assert a.session.headers["x-api-key"] == "TEST_ONLY_KEY"
    assert a.session.headers["anthropic-version"] == "2023-06-01"
    probe_url, _, _, _ = protocols.probe_request("anthropic_messages", a.base_url)
    assert probe_url == "https://api.anthropic.com/v1/models"


def test_config_rejects_unknown_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE_PATH", tmp_path / ".env")  # absent
    toml = tmp_path / "bad.toml"
    toml.write_text('[llm]\nprotocol = "websocket"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid llm.protocol"):
        config.load_config(toml)


def test_env_selects_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE_PATH", tmp_path / ".env")  # absent
    monkeypatch.setenv("TRADEMIND_LLM_PROTOCOL", "gemini_generate_content")
    toml = tmp_path / "plain.toml"
    toml.write_text('mode="paper"\n', encoding="utf-8")
    assert config.load_config(toml).llm.protocol == "gemini_generate_content"


def test_factory_passes_protocol(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'factory.db'}")
    cfg = AppConfig()
    cfg.llm.provider = "deepseek"
    cfg.llm.api_key = "k"
    cfg.llm.protocol = "gemini_generate_content"
    provider, is_stub = build_llm(cfg, repo)
    assert is_stub is False
    assert provider.protocol == "gemini_generate_content"
    assert provider.base_url == "https://generativelanguage.googleapis.com"
