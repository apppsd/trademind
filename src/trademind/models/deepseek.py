"""DeepSeek provider: OpenAI-compatible chat completions (TASK-11).

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

from ..domain.base import new_id, utcnow
from ..domain.enums import ProviderKind
from ..errors import ProviderError, ProviderRefused, ProviderTimeout
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


class DeepSeekProvider(LLMProvider):
    kind = ProviderKind.DEEPSEEK

    def __init__(self, api_key: str, model: str = "deepseek-flash",
                 base_url: str = "https://api.deepseek.com",
                 auditor: CallAuditor | None = None,
                 temperature: float = 0.2, max_repair_attempts: int = 1,
                 budget: Budget | None = None,
                 thinking: str = "disabled") -> None:
        if not api_key:
            raise ProviderError("DeepSeek api_key missing; use the explicit stub instead")
        if thinking not in ("disabled", "enabled"):
            raise ProviderError("DeepSeek thinking mode must be 'disabled' or 'enabled'")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.auditor = auditor
        self.temperature = temperature
        self.max_repair_attempts = max_repair_attempts
        self.budget = budget or Budget(limit=10**9)
        self.thinking = thinking
        self.breaker = CircuitBreaker()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # -- capability probe ---------------------------------------------------
    def probe(self) -> dict:
        """GET /models. Success sets capability_verified; failure keeps False."""
        try:
            r = self.session.get(f"{self.base_url}/models", timeout=15)
            r.raise_for_status()
            ids = [m.get("id") for m in r.json().get("data", [])]
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
        call.model_params = {"temperature": self.temperature, "thinking": self.thinking}
        started = time.monotonic()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = 0
        reserved_attempts = 0
        call.attempts = 0
        last_err: str | None = None
        all_usage_known = True
        while attempts <= self.max_repair_attempts:
            attempts += 1
            payload = {
                "model": self.model, "messages": messages,
                "temperature": self.temperature,
                "thinking": {"type": self.thinking},
                "response_format": {"type": "json_object"}, "stream": False,
            }
            if hasattr(self.budget, "reserve_request"):
                cap = self.budget.cfg.model_max_output_tokens
                payload["max_tokens"] = cap
                # UTF-8 byte count overbounds text token count; add framing margin.
                bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 1024
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
                    f"{self.base_url}/chat/completions",
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

            if not isinstance(body, dict):
                last_err = "protocol structure error: invalid response JSON"
                call.status = "error"
                call.failure_category = "protocol_error"
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "failure_category": "protocol_error",
                }
                break

            raw_usage = body.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else {}
            tokens = (usage.get('prompt_tokens'), usage.get('completion_tokens'))
            known = isinstance(raw_usage, dict) and all(type(v) is int and v >= 0 for v in tokens)
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

            comp_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
            reasoning_tokens = comp_details.get("reasoning_tokens") if type(comp_details.get("reasoning_tokens")) is int and comp_details.get("reasoning_tokens") >= 0 else None
            cached_tokens = (
                prompt_details.get("cached_tokens")
                if type(prompt_details.get("cached_tokens")) is int and prompt_details.get("cached_tokens") >= 0
                else (usage.get("prompt_cache_hit_tokens") if type(usage.get("prompt_cache_hit_tokens")) is int and usage.get("prompt_cache_hit_tokens") >= 0 else None)
            )
            total_tokens = usage.get("total_tokens") if type(usage.get("total_tokens")) is int and usage.get("total_tokens") >= 0 else None

            choices = body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
                last_err = "protocol structure error: missing choices or message"
                call.status = "error"
                call.failure_category = "protocol_error"
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "finish_reason": None,
                    "content_type": "missing",
                    "content_length": 0,
                    "content_empty": True,
                    "explicit_refusal": False,
                    "reasoning_present": False,
                    "reasoning_tokens": reasoning_tokens,
                    "cached_tokens": cached_tokens,
                    "total_tokens": total_tokens,
                    "usage_known": known,
                    "failure_category": "protocol_error",
                }
                break

            choice = choices[0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
            call.finish_reason = finish_reason
            raw_content = message.get("content")
            content_type = "missing" if "content" not in message else ("null" if raw_content is None else type(raw_content).__name__)
            text = raw_content if isinstance(raw_content, str) else ""
            content_length = len(text)
            content_empty = not text.strip()
            refusal_val = message.get("refusal")
            explicit_refusal = bool(isinstance(refusal_val, str) and refusal_val.strip()) or finish_reason == "content_filter"
            reasoning_val = message.get("reasoning_content")
            reasoning_present = bool((isinstance(reasoning_val, str) and reasoning_val.strip()) or (reasoning_tokens is not None and reasoning_tokens > 0))

            def _record_diag(cat: str) -> None:
                call.failure_category = cat
                call.response_diagnostics = {
                    "http_status": call.http_status,
                    "finish_reason": finish_reason,
                    "content_type": content_type,
                    "content_length": content_length,
                    "content_empty": content_empty,
                    "explicit_refusal": explicit_refusal,
                    "reasoning_present": reasoning_present,
                    "reasoning_tokens": reasoning_tokens,
                    "cached_tokens": cached_tokens,
                    "total_tokens": total_tokens,
                    "usage_known": known,
                    "failure_category": cat,
                }

            if explicit_refusal:
                call.status = "refused"
                _record_diag("explicit_refusal")
                last_err = "content filter refusal" if finish_reason == "content_filter" else "explicit model refusal"
                break

            if raw_content is not None and not isinstance(raw_content, str):
                call.status = "error"
                _record_diag("protocol_error")
                last_err = "protocol structure error: non-string message content"
                break

            parsed = extract_json(text)
            if not content_empty and parsed is None and attempts <= self.max_repair_attempts:
                # one explicit format repair round-trip
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                        "Your previous reply was not valid JSON. Reply again with "
                        "only a JSON object matching the requested schema."},
                ]
                continue
            if content_empty:
                call.status = "error"
                if finish_reason == "length":
                    _record_diag("length_truncated")
                    last_err = "completion truncated at max_tokens (finish_reason=length)"
                elif reasoning_present:
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
