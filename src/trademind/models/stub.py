"""Explicit stub providers (TASK-11).

These exist so the offline chain runs without credentials. Their outputs are
deterministic fixtures of *schema shape only* — they carry no market opinion
and must never be presented as model performance. Every record is marked
is_stub / status='stub' / capability_verified=False.
"""

from __future__ import annotations

import time
import json
from datetime import datetime

from ..domain.base import new_id, utcnow
from ..domain.enums import ConfidenceSemantics, ProviderKind
from ..domain.judgments import Judgment, JudgmentBatch
from ..errors import ProviderError
from .base import Budget, CallAuditor, LLMProvider, JEVProvider, ModelOutput, build_call


class StubLLMProvider(LLMProvider):
    """Deterministic JSON per role. `script` lets tests drive specific shapes."""

    kind = ProviderKind.STUB
    model = "stub-offline"
    capability_verified = False

    def __init__(self, auditor: CallAuditor | None = None,
                 script: dict[str, dict] | None = None,
                 budget: Budget | None = None) -> None:
        self.auditor = auditor
        self.script = script or {}
        self.budget = budget or Budget(limit=10**9)
        self.calls = 0

    def complete(self, *, role: str, system: str, user: str, trace_id: str,
                 timeout_s: float = 5.0) -> ModelOutput:
        self.calls += 1
        if not self.budget.try_consume():
            raise ProviderError("stub budget exhausted")
        call = build_call(role=role, provider=self.kind, model=self.model,
                          system=system, user=user, trace_id=trace_id)
        started = time.monotonic()
        payload = self.script.get(role)
        if payload is None:
            payload = _default_script(role)
        import json as _json

        text = _json.dumps(payload, ensure_ascii=False)
        call = call.model_copy(update={"is_stub": True, "status": "stub"})
        call.latency_ms = int((time.monotonic() - started) * 1000) + 1
        if self.auditor:
            self.auditor.save(call)
        return ModelOutput(text=text, parsed=payload, call=call)


def _default_script(role: str) -> dict:
    """Shape-faithful stub payloads keyed by agent role."""
    if role.startswith("analyst:"):
        return {
            "facts": [{"statement": "[stub] fixture data shows a stable trend",
                       "kind": "interpretation"}],
            "analysis": "[stub] no real analysis; offline stub output",
            "unknowns": ["all real-world unknowns (stub)"],
            "risks": ["stub output must not drive real decisions"],
        }
    if role in ("bull", "bear"):
        return {
            "argument": "[stub] " + ("supportive" if role == "bull" else "opposing") + " argument",
        }
    if role == "research_manager":
        return {
            "direction": "NEUTRAL",
            "confidence": "0.5",
            "confidence_semantics": "evidence_support",
            "supporting_reasons": ["[stub] fixture momentum positive"],
            "counterarguments": ["[stub] valuation risk"],
            "invalidations": ["[stub] close below recent low on high volume"],
            "horizon_seconds": 432000,
            "summary": "[stub] neutral thesis",
        }
    if role == "trader":
        return {
            "action": "HOLD",
            "target_weight": None,
            "confidence": "0.5",
            "confidence_semantics": "evidence_support",
            "holding_horizon_seconds": 432000,
            "reason": "[stub] no position and neutral thesis",
            "exit_conditions": [{"description": "[stub] thesis invalidation", "kind": "thesis_invalidation"}],
        }
    if role == "risk":
        return {
            "verdict": "APPROVE",
            "risks": ["[stub] single-position concentration"],
            "reason": "[stub] within stated limits",
        }
    if role == "portfolio":
        return {
            "action": "HOLD",
            "target_weight": None,
            "reason": "[stub] keep current allocation",
        }
    if role == "reflection":
        return {
            "summary": "[stub] outcome within expectation",
            "lesson": "[stub] no lesson from stub",
            "attributes_luck": True,
            "confidence": "low",
        }
    return {}


class StubJEVProvider(JEVProvider):
    """Answers every question with a neutral 0.5 and explicit refusal semantics."""

    kind = ProviderKind.STUB
    model = "stub-jev"
    capability_verified = False

    def __init__(self, question_version: str = "qv-stub",
                 auditor: CallAuditor | None = None,
                 answers: dict[str, tuple[str, str]] | None = None) -> None:
        self.question_version = question_version
        self.auditor = auditor
        self.answers = answers or {}

    def judge(self, *, context_id: str, thesis_id: str | None, instrument_id: str,
              as_of: datetime, questions: list[dict], trace_id: str,
              state: dict | None = None, thesis: dict | None = None) -> JudgmentBatch:
        judgments = []
        for q in questions:
            qid = q["question_id"]
            if qid in self.answers:
                value, prob = self.answers[qid]
                judgments.append(Judgment(question_id=qid, value=value,
                                          probability=prob,
                                          probability_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE))
            else:
                judgments.append(Judgment(question_id=qid, value="unsure",
                                          probability="0.5",
                                          probability_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE))
        batch = JudgmentBatch(
            id=new_id("jb"), trace_id=trace_id, created_at=utcnow(),
            context_id=context_id, thesis_id=thesis_id, instrument_id=instrument_id,
            provider=self.kind, model=self.model, question_version=self.question_version,
            judgments=judgments, latency_ms=1, status="ok", is_stub=True, as_of=as_of,
        )
        return batch


class HttpJEVProvider(StubJEVProvider):
    """HTTP JEV scaffold. The endpoint contract is NOT verified (ISSUE-02);
    until the user supplies a real base_url this class refuses to fabricate.

    Expected wire format (documented, unverified): POST {base_url}/v1/judgments
    {"context_id","thesis_id","instrument_id","as_of","questions":[...]} ->
    {"judgments":[{"question_id","value","probability"}], "status":"ok"}
    """

    kind = ProviderKind.JEV_HTTP

    def __init__(self, base_url: str, api_key: str = "", model: str = "",
                 question_version: str = "qv-http", timeout_s: float = 10.0,
                 auditor: CallAuditor | None = None) -> None:
        if not base_url:
            raise ProviderError(
                "JEV base_url missing; keep provider='stub' until a real "
                "endpoint is provided (capability cannot be verified)")
        import requests

        self._requests = requests
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "jev-http"
        self.question_version = question_version
        self.timeout_s = timeout_s
        self.auditor = auditor
        self.capability_verified = False

    def judge(self, *, context_id, thesis_id, instrument_id, as_of, questions, trace_id,
              state=None, thesis=None):
        call = build_call(role="jev", provider=self.kind, model=self.model, system=self.question_version,
                          user=json.dumps({"questions": questions, "state": state, "thesis": thesis}, sort_keys=True), trace_id=trace_id)
        started = time.monotonic()
        try:
            batch = self._judge(context_id=context_id, thesis_id=thesis_id, instrument_id=instrument_id,
                                as_of=as_of, questions=questions, trace_id=trace_id, state=state, thesis=thesis)
            call.status = "ok"
            return batch
        except Exception as exc:
            call.error = type(exc).__name__
            raise ProviderError("JEV request or response contract failed") from exc
        finally:
            call.latency_ms = int((time.monotonic() - started) * 1000)
            if self.auditor:
                self.auditor.save(call)

    def _judge(self, *, context_id, thesis_id, instrument_id, as_of, questions, trace_id, state, thesis):
        if not state or state.get("context_id") != context_id or state.get("as_of") != as_of.isoformat():
            raise ProviderError("JEV requires the actual visible state")
        if thesis_id and (not thesis or thesis.get("thesis_id") != thesis_id):
            raise ProviderError("JEV requires the actual thesis")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "context_id": context_id, "thesis_id": thesis_id,
            "instrument_id": instrument_id, "as_of": as_of.isoformat(),
            "questions": questions, "state": state, "thesis": thesis,
            "question_version": self.question_version,
        }
        if len(json.dumps(payload)) > 48000:
            raise ProviderError("JEV input exceeds bounded payload budget")
        started = time.monotonic()
        try:
            r = self._requests.post(f"{self.base_url}/v1/judgments",
                                    json=payload, headers=headers,
                                    timeout=self.timeout_s)
            r.raise_for_status()
            body = r.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("JEV HTTP request failed") from exc
        latency = int((time.monotonic() - started) * 1000)
        asked = [q["question_id"] for q in questions]
        returned = [j["question_id"] for j in body.get("judgments", [])]
        if body.get("status") != "ok" or len(asked) != len(set(asked)) or sorted(asked) != sorted(returned):
            raise ProviderError("JEV response incomplete, duplicated or refused")
        if any(j.get('missing_reason') or j.get('probability') is None for j in body['judgments']):
            raise ProviderError('JEV response lacks supported answers')
        judgments = []
        for j in body.get("judgments", []):
            judgments.append(Judgment(
                question_id=j["question_id"], value=j.get("value"),
                probability=j.get("probability"),
                probability_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
                missing_reason=j.get("missing_reason"),
            ))
        batch = JudgmentBatch(
            id=new_id("jb"), trace_id=trace_id, created_at=utcnow(),
            context_id=context_id, thesis_id=thesis_id, instrument_id=instrument_id,
            provider=self.kind, model=self.model, question_version=self.question_version,
            judgments=judgments, latency_ms=latency,
            status=body.get("status", "ok"), is_stub=False, as_of=as_of,
            raw_response_ref=None,
        )
        return batch
