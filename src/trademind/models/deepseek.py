"""Multi-protocol HTTP LLM provider (TASK-20261021-02; generalizes TASK-11).

Wire formats live in protocols.py; this module keeps the shared pipeline:
circuit breaker, daily budget reservation/reconciliation, one format-repair
attempt max, refusal/truncation diagnostics, audit. The historical name
DeepSeekProvider is kept as an alias (runner / prompt_experiment isinstance
checks and older tests keep working); the audit ProviderKind stays deepseek
and the wire protocol is recorded in call.model_params.

Verified against official docs on 2026-10-07 (see docs/research):
base https://api.deepseek.com, models deepseek-flash / deepseek-v4-pro.
capability_verified stays False until a live probe succeeds; failures keep it
False. No api key -> caller must not construct this provider.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import requests

from ..domain.enums import ProviderKind
from ..errors import ProviderError, ProviderRefused, ProviderTimeout
from . import protocols
from .base import (
    CallAuditor,
    CircuitBreaker,
    Budget,
    LLMProvider,
    ModelOutput,
    build_call,
    extract_json,
)


def chinese_output_system(system):
    return system + '\n所有面向人的分析、理由和说明必须使用简体中文；保留Schema字段名、枚举、代码、数字和必要技术名词，不输出整段英文说明。'


class MultiProtocolLLMProvider(LLMProvider):
    kind = ProviderKind.DEEPSEEK

    def __init__(self, api_key: str, model: str = "deepseek-flash",
                 base_url: str = protocols.DEEPSEEK_DEFAULT_BASE_URL,
                 protocol: str = "openai_chat",
                 auditor: CallAuditor | None = None,
                 temperature: float = 0.2, max_repair_attempts: int = 1,
                 budget: Budget | None = None,
                 thinking: str = "disabled") -> None:
        if not api_key:
            raise ProviderError("LLM api_key missing; use the explicit stub instead")
        if protocol not in protocols.VALID_LLM_PROTOCOLS:
            raise ProviderError(
                f"unknown llm protocol {protocol!r}; one of {list(protocols.VALID_LLM_PROTOCOLS)}")
        if thinking not in ("disabled", "enabled"):
            raise ProviderError("LLM thinking mode must be 'disabled' or 'enabled'")
        self.api_key = api_key
        self.model = model
        self.protocol = protocol
        # An unset/default base URL resolves to the protocol's official endpoint;
        # an explicit custom (relay) URL is kept verbatim.
        self.base_url = protocols.effective_base_url(protocol, base_url)
        self.auditor = auditor
        self.temperature = temperature
        self.max_repair_attempts = max_repair_attempts
        self.budget = budget or Budget(limit=10**9)
        self.thinking = thinking
        self.breaker = CircuitBreaker()
        self.session = requests.Session()
        self.session.headers.update(protocols.session_headers(protocol, self.api_key))

    # -- capability probe ---------------------------------------------------
    def probe(self) -> dict:
        """Protocol model-list endpoint. Success sets capability_verified; failure keeps False."""
        try:
            url, list_key, id_key, strip_prefix = protocols.probe_request(
                self.protocol, self.base_url)
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            entries = r.json().get(list_key, [])
            ids = [e.get(id_key) for e in entries if isinstance(e, dict)]
            if strip_prefix:  # Gemini lists "models/<id>"
                ids = [i.split("models/", 1)[-1] if isinstance(i, str) else i for i in ids]
            self.capability_verified = self.model in ids
            return {"ok": self.capability_verified, "models": ids,
                    "error": None if self.capability_verified else 'configured model unavailable'}
        except Exception as exc:  # noqa: BLE001
            self.capability_verified = False
            return {"ok": False, "error": type(exc).__name__}

    # -- completion ----------------------------------------------------------
    def complete(self, *, role: str, system: str, user: str, trace_id: str,
                 timeout_s: float = 60.0) -> ModelOutput:
        system = chinese_output_system(system)
        self.breaker.check()
        call = build_call(role=role, provider=self.kind, model=self.model,
                          system=system, user=user, trace_id=trace_id)
        call.capability_verified = self.capability_verified
        call.model_params = {"temperature": self.temperature,
                             "thinking": self.thinking,
                             "protocol": self.protocol}
        started = time.monotonic()
        max_tokens_cap = (self.budget.cfg.model_max_output_tokens
                          if hasattr(self.budget, "reserve_request") else None)
        history: list[tuple[str, str]] = []
        attempts = 0
        reserved_attempts = 0
        call.attempts = 0
        last_err: str | None = None
        all_usage_known = True
        while attempts <= self.max_repair_attempts:
            attempts += 1
            url, payload = protocols.build_request(
                self.protocol, base_url=self.base_url, model=self.model,
                system=system, user=user, history=history,
                temperature=self.temperature, max_tokens=max_tokens_cap,
                thinking=self.thinking)
            if hasattr(self.budget, "reserve_request"):
                cap = self.budget.cfg.model_max_output_tokens
                # UTF-8 byte count overbounds text token count; add framing margin.
                bound = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 1024
                try:
                    reserved = self.budget.reserve_request(bound, cap, role=role)
                    reserved_attempts += 1
                    call.attempts = reserved_attempts
                    call.cost_amount = str(Decimal(call.cost_amount or '0') + reserved)
                    call.cost_currency = 'CNY'
                    call.cost_source = 'operator_price_request_upper_bound'
                except ProviderError as exc:
                    call.status, call.error = "error", str(exc)
                    call.failure_category = "budget_exhausted"
                    self._audit(call)
                    raise
            elif not self.budget.try_consume():
                call.status, call.error = "error", "daily model budget exhausted"
                call.failure_category = "budget_exhausted"
                self._audit(call)
                raise ProviderError(call.error)
            else:
                reserved_attempts += 1
                call.attempts = reserved_attempts
            try:
                resp = self.session.post(
                    url,
                    json=payload,
                    timeout=timeout_s,
                )
                call.http_status = getattr(resp, "status_code", None)
                if resp.status_code == 429:
                    last_err = "rate limited (429)"
                    call.status = "error"
                    call.failure_category = "rate_limited"
                    call.response_diagnostics = {
                        "http_status": 429,
                        "failure_category": "rate_limited",
                    }
                    break
                if isinstance(call.http_status, int) and call.http_status >= 400:
                    last_err = f"model request HTTP {call.http_status}"
                    call.status = "error"
                    call.failure_category = "http_error"
                    call.response_diagnostics = {
                        "http_status": call.http_status,
                        "failure_category": "http_error",
                    }
                    break
                resp.raise_for_status()
                body = resp.json()
            except requests.exceptions.Timeout:
                last_err = "model request timeout"
                call.status, call.is_stub = "timeout", False
                call.failure_category = "timeout"
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "failure_category": "timeout",
                }
                break
            except requests.exceptions.RequestException as exc:
                http_code = getattr(getattr(exc, "response", None), "status_code", call.http_status)
                call.http_status = http_code if isinstance(http_code, int) else call.http_status
                if call.http_status == 429:
                    last_err = "rate limited (429)"
                    call.failure_category = "rate_limited"
                elif isinstance(call.http_status, int) and call.http_status >= 400:
                    last_err = f"model request HTTP {call.http_status}"
                    call.failure_category = "http_error"
                else:
                    last_err = "model request HTTP failure"
                    call.failure_category = "network_error"
                call.status = "error"
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "failure_category": call.failure_category,
                }
                break
            except ValueError:
                last_err = "protocol structure error: invalid response JSON"
                call.status = "error"
                call.failure_category = "protocol_error"
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "failure_category": "protocol_error",
                }
                break

            reply = protocols.parse_response(self.protocol, body)

            tokens = (reply.input_tokens, reply.output_tokens)
            known = reply.usage_known
            if hasattr(self.budget, 'reconcile'):
                all_usage_known = all_usage_known and known
                if known:
                    actual = self.budget.flash_cost(*tokens)
                    call.cost_amount = str(Decimal(call.cost_amount) - reserved + actual)
                    call.cost_source = 'operator_price_usage' if all_usage_known else 'mixed_usage_and_reservation'
                    try:
                        self.budget.reconcile(reserved, actual)
                    except ProviderError:
                        call.status, call.error = 'error', 'billed usage exceeded reservation'
                        call.failure_category = 'budget_exceeded'
                        self._audit(call)
                        raise
                else:
                    call.cost_source = 'mixed_usage_and_reservation'
            call.input_tokens = tokens[0] if type(tokens[0]) is int and tokens[0] >= 0 else None
            call.output_tokens = tokens[1] if type(tokens[1]) is int and tokens[1] >= 0 else None

            def _record_diag(cat: str) -> None:
                call.failure_category = cat
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "finish_reason": reply.finish_reason,
                    "content_type": reply.content_type,
                    "content_length": reply.content_length,
                    "content_empty": reply.content_empty,
                    "explicit_refusal": reply.explicit_refusal,
                    "reasoning_present": reply.reasoning_present,
                    "reasoning_tokens": reply.reasoning_tokens,
                    "cached_tokens": reply.cached_tokens,
                    "total_tokens": reply.total_tokens,
                    "usage_known": known,
                    "failure_category": cat,
                }

            if reply.error_message:
                call.status = "error"
                _record_diag("protocol_error")
                last_err = reply.error_message
                break
            if reply.structure_error:
                call.status = "error"
                _record_diag("protocol_error")
                last_err = f"protocol structure error: {reply.structure_error}"
                break

            finish_reason = reply.finish_reason
            call.finish_reason = finish_reason
            text = reply.text

            if reply.explicit_refusal:
                call.status = "refused"
                _record_diag("explicit_refusal")
                last_err = "content filter refusal" if finish_reason == "content_filter" else "explicit model refusal"
                break

            if reply.non_string_content:
                call.status = "error"
                _record_diag("protocol_error")
                last_err = "protocol structure error: non-string message content"
                break

            parsed = extract_json(text)
            if not reply.content_empty and parsed is None and attempts <= self.max_repair_attempts:
                # one explicit format repair round-trip
                history = history + [
                    ("assistant", text),
                    ("user", protocols.REPAIR_INSTRUCTION),
                ]
                continue
            if reply.content_empty:
                call.status = "error"
                if finish_reason == "length":
                    _record_diag("length_truncated")
                    last_err = "completion truncated at max_tokens (finish_reason=length)"
                elif reply.reasoning_present:
                    _record_diag("reasoning_only")
                    last_err = "empty completion content with reasoning_content only"
                else:
                    _record_diag("empty_output")
                    last_err = "empty completion content"
                break
            if parsed is None:
                call.status = "error"
                if finish_reason == "length":
                    _record_diag("length_truncated")
                    last_err = "completion truncated at max_tokens (finish_reason=length)"
                else:
                    _record_diag("invalid_json")
                    last_err = "invalid JSON completion"
                break
            _record_diag("none")
            call.status = "ok" if attempts == 1 else "repaired"
            call.latency_ms = int((time.monotonic() - started) * 1000)
            self.breaker.record_success()
            self._audit(call)
            return ModelOutput(text=text, parsed=parsed, call=call)

        call.latency_ms = int((time.monotonic() - started) * 1000)
        if call.status not in ("refused", "timeout"):
            call.status = "error"
        if not call.failure_category:
            call.failure_category = "unknown"
        call.error = last_err
        self.breaker.record_failure()
        self._audit(call)
        if call.status == "timeout":
            raise ProviderTimeout(last_err or "timeout")
        if call.status == "refused":
            raise ProviderRefused(last_err or "refused")
        raise ProviderError(last_err or "provider failed")

    def _audit(self, call) -> None:
        self.last_call = call
        if self.auditor:
            self.auditor.save(call)


# Backward-compat alias: runner / prompt_experiment isinstance checks and older
# tests reference the historical name; the class is protocol-general now.
DeepSeekProvider = MultiProtocolLLMProvider
