import json
from decimal import Decimal
from unittest.mock import Mock
import pytest
import requests

from test_fast_decision import make_pipe, strategy, I, NOW
from trademind.agents.strategy_planner import StrategyPlanner
from trademind.domain.modelcall import ModelCall, SAFE_DIAGNOSTIC_KEYS, sanitize_response_diagnostics
from trademind.domain.enums import ProviderKind
from trademind.evaluation.fast_archives import classify_failure, audit_archive_failures
from trademind.execution.paper_run import public_call
from trademind.errors import ProviderError, ProviderRefused, ProviderTimeout
from trademind.market.profiles import get_profile
from trademind.models.base import CallAuditor
from trademind.models.deepseek import DeepSeekProvider
from trademind.reports.locale import explanation, failure_category_text


def _mock_response(body, status_code=200):
    resp = Mock(status_code=status_code)
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    resp.raise_for_status = Mock()
    return resp


def test_deepseek_disables_thinking_by_default_and_succeeds_with_chinese_strategy(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    provider = DeepSeekProvider("TEST_KEY_ONLY", budget=pipe.model_budget, auditor=CallAuditor(pipe.repo))
    draft = strategy()
    resp = _mock_response({
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False)}}],
        "usage": {
            "prompt_tokens": 3995,
            "completion_tokens": 380,
            "total_tokens": 4375,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "prompt_tokens_details": {"cached_tokens": 2048},
        },
    })
    provider.session.post = Mock(return_value=resp)
    planner = StrategyPlanner(provider, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
    context = pipe.context_builder.build_minute(I, tick[0])
    plan = planner.get(context, get_profile(I), "trace-ok")
    sent_payload = provider.session.post.call_args.kwargs["json"]
    assert sent_payload["thinking"] == {"type": "disabled"}
    assert sent_payload["response_format"] == {"type": "json_object"}
    assert sent_payload["max_tokens"] == 1024
    history = pipe.repo.db.query("SELECT plan_id, payload_json FROM strategy_plan_history WHERE account_id=?", (pipe.cfg.account_id,))
    assert len(history) == 1 and history[0]["plan_id"] == plan.plan_id
    calls = [json.loads(r["payload_json"]) for r in pipe.repo.db.query("SELECT payload_json FROM model_calls")]
    assert len(calls) == 1
    call = calls[0]
    assert call["status"] == "ok"
    assert call["http_status"] == 200
    assert call["finish_reason"] == "stop"
    assert call["failure_category"] == "none"
    diag = call["response_diagnostics"]
    assert diag["content_empty"] is False
    assert diag["explicit_refusal"] is False
    assert diag["reasoning_present"] is False
    assert diag["reasoning_tokens"] == 0
    assert diag["cached_tokens"] == 2048
    assert diag["total_tokens"] == 4375
    assert diag["usage_known"] is True


@pytest.mark.parametrize("case,body,status_code,exc_cls,expected_status,expected_cat,expected_stage", [
    (
        "explicit_refusal_field",
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "", "refusal": "Policy refusal"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5},
        },
        200,
        ProviderRefused,
        "refused",
        "explicit_refusal",
        "model_call",
    ),
    (
        "content_filter_refusal",
        {
            "choices": [{"finish_reason": "content_filter", "message": {"content": ""}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 0},
        },
        200,
        ProviderRefused,
        "refused",
        "explicit_refusal",
        "model_call",
    ),
    (
        "empty_content",
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "   "}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 0},
        },
        200,
        ProviderError,
        "error",
        "empty_output",
        "model_call",
    ),
    (
        "finish_reason_length_empty",
        {
            "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "long chain of thought DO_NOT_STORE"}}],
            "usage": {"prompt_tokens": 3997, "completion_tokens": 1024, "completion_tokens_details": {"reasoning_tokens": 1024}},
        },
        200,
        ProviderError,
        "error",
        "length_truncated",
        "model_call",
    ),
    (
        "reasoning_only_no_content",
        {
            "choices": [{"finish_reason": "stop", "message": {"content": None, "reasoning_content": '{"trend":"fake strategy in reasoning"}'}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "completion_tokens_details": {"reasoning_tokens": 200}},
        },
        200,
        ProviderError,
        "error",
        "reasoning_only",
        "model_call",
    ),
    (
        "invalid_json",
        {
            "choices": [{"finish_reason": "stop", "message": {"content": "not valid json object"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 15},
        },
        200,
        ProviderError,
        "error",
        "invalid_json",
        "model_call",
    ),
    (
        "missing_choices",
        {
            "usage": {"prompt_tokens": 80, "completion_tokens": 0},
        },
        200,
        ProviderError,
        "error",
        "protocol_error",
        "model_call",
    ),
    (
        "http_502_failure",
        {"error": "bad gateway"},
        502,
        ProviderError,
        "error",
        "http_error",
        "model_call",
    ),
    (
        "http_429_rate_limited",
        {"error": "rate limit"},
        429,
        ProviderError,
        "error",
        "rate_limited",
        "model_call",
    ),
])
def test_deepseek_failure_classifications_and_no_plan_or_jev_leak(
    tmp_path, case, body, status_code, exc_cls, expected_status, expected_cat, expected_stage
):
    pipe, tick = make_pipe(tmp_path / case)
    provider = DeepSeekProvider(
        "sk-secretkey123456",
        budget=pipe.model_budget,
        auditor=CallAuditor(pipe.repo),
        max_repair_attempts=0,
    )
    provider.session.post = Mock(return_value=_mock_response(body, status_code=status_code))
    planner = StrategyPlanner(provider, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
    context = pipe.context_builder.build_minute(I, tick[0])
    with pytest.raises(exc_cls):
        planner.get(context, get_profile(I), f"tr-{case}")

    # Must not write valid strategy plan cache or history, nor call JEV or create orders
    assert pipe.repo.get_coalesce(planner.key(I)) is None
    assert not pipe.repo.db.query("SELECT * FROM strategy_plan_history WHERE account_id=?", (pipe.cfg.account_id,))
    pipe.jev.session.post.assert_not_called()
    assert not pipe.repo.open_orders()

    rows = [json.loads(r["payload_json"]) for r in pipe.repo.db.query("SELECT payload_json FROM model_calls")]
    assert len(rows) == 1
    call = rows[0]
    assert call["status"] == expected_status
    assert call["failure_category"] == expected_cat
    raw_dump = json.dumps(call, ensure_ascii=False)
    assert "sk-secretkey123456" not in raw_dump
    assert "DO_NOT_STORE" not in raw_dump
    assert "fake strategy in reasoning" not in raw_dump

    pub = public_call(call)
    stage, archive_cat = classify_failure(call.get("error"), None, "WAIT", calls=[pub])
    expected_archive_cat = "format_error" if expected_cat == "invalid_json" else expected_cat
    assert (stage, archive_cat) == (expected_stage, expected_archive_cat)
    assert failure_category_text(archive_cat) != "未知原因"
    assert "历史说明尚未中文化" not in explanation(call.get("error"))


def test_timeout_and_unknown_failure_classification(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    provider = DeepSeekProvider("TEST_KEY", budget=pipe.model_budget, auditor=CallAuditor(pipe.repo))
    provider.session.post = Mock(side_effect=requests.exceptions.Timeout("read timed out"))
    with pytest.raises(ProviderTimeout):
        provider.complete(role="strategy", system="json", user="{}", trace_id="tr-timeout")
    row = json.loads(pipe.repo.db.query("SELECT payload_json FROM model_calls")[0]["payload_json"])
    assert row["status"] == "timeout"
    assert row["failure_category"] == "timeout"
    assert classify_failure(row["error"], None, None, calls=[public_call(row)]) == ("model_call", "timeout")

    # Unrecognized error string without matching evidence classifies as unknown
    assert classify_failure("unclassified MysteriousInternalFault", None, None) == ("unknown", "unknown")
    assert failure_category_text("unknown") == "未知原因"


def test_historical_modelcall_compatibility_and_whitelist_redaction(tmp_path):
    # Legacy ModelCall record without new optional fields validates cleanly
    legacy_payload = {
        "id": "mc-legacy",
        "call_id": "mc-legacy",
        "trace_id": "tr-legacy",
        "role": "strategy",
        "provider": ProviderKind.DEEPSEEK,
        "model": "deepseek-flash",
        "prompt_id": "p1",
        "prompt_hash": "h1",
        "latency_ms": 500,
        "status": "refused",
        "error": "empty completion (refusal?)",
        "created_at": NOW.isoformat(),
    }
    legacy = ModelCall.model_validate(legacy_payload)
    assert legacy.http_status is None
    assert legacy.finish_reason is None
    assert legacy.failure_category is None
    assert legacy.response_diagnostics is None
    # Legacy "empty completion (refusal?)" is classified as empty_output, not model refusal
    assert classify_failure(legacy.error, None, "WAIT") == ("model_call", "empty_output")

    # Whitelist strips forbidden keys (headers, api_key, reasoning_content, raw_body)
    dirty_diag = {
        "http_status": 200,
        "finish_reason": "length",
        "content_type": "str",
        "content_length": 0,
        "content_empty": True,
        "explicit_refusal": False,
        "reasoning_present": True,
        "reasoning_tokens": 1024,
        "failure_category": "length_truncated",
        "api_key": "sk-secret123456789",
        "headers": {"Authorization": "Bearer secret"},
        "reasoning_content": "raw chain of thought",
        "raw_body": {"choices": []},
    }
    cleaned = sanitize_response_diagnostics(dirty_diag)
    assert set(cleaned).issubset(set(SAFE_DIAGNOSTIC_KEYS))
    assert "api_key" not in cleaned
    assert "headers" not in cleaned
    assert "reasoning_content" not in cleaned
    assert "raw_body" not in cleaned

    bundle = {
        "schema_version": 2,
        "run_id": "diag-bundle",
        "status": "collected",
        "contexts": [{"id": "c1"}],
        "archives": {
            "old": {},
            "new": {
                "c1": {
                    "action": "WAIT",
                    "guard": "HOLD_NO_TRADE",
                    "failure_stage": "model_call",
                    "failure_category": "length_truncated",
                    "model_suggestion": "WAIT",
                    "program_verdict": "HOLD_NO_TRADE",
                    "completion_status": "failed",
                    "sanitized_evidence": "completion truncated at max_tokens (finish_reason=length)",
                }
            },
        },
        "failure_audits": [],
        "gaps": [],
    }
    audit = audit_archive_failures(bundle)
    assert audit["unknown_field_count"] == 0
    assert audit["failure_counts"]["length_truncated"] == 1
