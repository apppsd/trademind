"""Model call audit record: prompt/model/config versions, tokens, stub marking.

Stub calls are always is_stub=True and can never be reported as real model
performance. No implicit chain-of-thought is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import MsgBase, require_tz, utcnow, new_id
from .enums import ProviderKind


SAFE_DIAGNOSTIC_KEYS = (
    "http_status",
    "finish_reason",
    "content_type",
    "content_length",
    "content_empty",
    "explicit_refusal",
    "reasoning_present",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
    "usage_known",
    "failure_category",
)


def sanitize_response_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only whitelisted primitive diagnostic fields; never store raw content or headers."""
    if not isinstance(diagnostics, dict):
        return None
    cleaned: dict[str, Any] = {}
    for key in SAFE_DIAGNOSTIC_KEYS:
        if key not in diagnostics:
            continue
        val = diagnostics[key]
        if val is None or isinstance(val, (bool, int)):
            cleaned[key] = val
        elif isinstance(val, str):
            cleaned[key] = val[:64]
    return cleaned or None


class ModelCall(MsgBase):
    call_id: str
    trace_id: str
    role: str  # research/trader/risk/portfolio/reflection/jev
    provider: ProviderKind
    model: str
    prompt_id: str
    prompt_hash: str
    model_params: dict[str, Any] = {}
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_source: str = "unmeasured"  # e.g. "list_price_2026-10-07"
    cost_amount: str | None = None  # decimal string, currency in cost_currency
    cost_currency: str | None = None
    latency_ms: int
    status: Literal["ok", "repaired", "refused", "error", "timeout", "stub"]
    is_stub: bool = False
    capability_verified: bool = False
    attempts: int = 1
    error: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    failure_category: str | None = None
    response_diagnostics: dict[str, Any] | None = None
    request_payload_ref: str | None = None
    response_payload_ref: str | None = None

    @field_validator("created_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @field_validator("response_diagnostics")
    @classmethod
    def _clean_diagnostics(cls, v):
        return sanitize_response_diagnostics(v)

    @model_validator(mode="after")
    def _stub_marker(self):
        if self.is_stub and self.status != "stub":
            raise ValueError("stub calls must carry status='stub'")
        if self.status == "stub" and not self.is_stub:
            raise ValueError("status='stub' requires is_stub=True")
        return self

