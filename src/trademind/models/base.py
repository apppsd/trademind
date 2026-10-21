"""Provider interfaces and call auditing (TASK-11).

Rules from ARCH-001 / TASK-11:
- stub results are always explicitly marked and never count as real capability;
- capability_verified flips only after a successful live probe;
- invalid JSON, refusals, timeouts never become valid trading suggestions;
- one format-repair attempt max, inside budget and deadline.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.base import new_id, utcnow
from ..domain.enums import ProviderKind
from ..domain.modelcall import ModelCall
from ..errors import CircuitOpen, ProviderError, ProviderRefused, ProviderTimeout
from ..storage import Repository


@dataclass
class ModelOutput:
    text: str
    parsed: dict | None
    call: ModelCall


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\x00" + user).encode("utf-8")).hexdigest()[:32]


def extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction: fenced or bare object."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_s: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._consecutive = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self._opened_at is not None:
                if time.monotonic() - self._opened_at < self.cooldown_s:
                    raise CircuitOpen("provider circuit open")
                self._opened_at = None
                self._consecutive = 0

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self.failure_threshold:
                self._opened_at = time.monotonic()


class Budget:
    """Daily call budget with clock-free test injection."""

    def __init__(self, limit: int, used: int = 0) -> None:
        self.limit = limit
        self.used = used
        self._lock = threading.Lock()

    def try_consume(self, n: int = 1) -> bool:
        with self._lock:
            if self.used + n > self.limit:
                return False
            self.used += n
            return True

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


class LLMProvider(ABC):
    kind: ProviderKind
    model: str = ""
    capability_verified: bool = False

    @abstractmethod
    def complete(self, *, role: str, system: str, user: str, trace_id: str,
                 timeout_s: float = 60.0) -> ModelOutput: ...


class JEVProvider(ABC):
    kind: ProviderKind
    model: str = ""
    capability_verified: bool = False

    @abstractmethod
    def judge(self, *, context_id: str, thesis_id: str | None, instrument_id: str,
              as_of: datetime, questions: list[dict], trace_id: str,
              state: dict | None = None, thesis: dict | None = None) -> "object": ...


class CallAuditor:
    """Persists ModelCall records; strips any credential-looking content."""

    _SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|Bearer\s+[A-Za-z0-9._-]{6,})")

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def redact(self, s: str | None) -> str:
        return self._SECRET_RE.sub("[REDACTED]", s or "")

    def save(self, call: ModelCall) -> None:
        from ..domain.modelcall import sanitize_response_diagnostics
        call.error = self.redact(call.error) if call.error is not None else None
        if call.response_diagnostics is not None:
            cleaned = sanitize_response_diagnostics(call.response_diagnostics)
            if cleaned is not None:
                for k, v in list(cleaned.items()):
                    if isinstance(v, str):
                        cleaned[k] = self.redact(v)
            call.response_diagnostics = cleaned
        self.repo.save_model_call(call)



def build_call(*, role: str, provider: ProviderKind, model: str, system: str,
               user: str, trace_id: str) -> ModelCall:
    cid = new_id("mc")
    return ModelCall(
        id=cid, call_id=cid, trace_id=trace_id, role=role, provider=provider,
        model=model, prompt_id=prompt_hash(system, user)[:16],
        prompt_hash=prompt_hash(system, user), latency_ms=0,
        status="error", created_at=utcnow(),
    )
