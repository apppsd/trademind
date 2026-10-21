"""JEV judgment contracts (ARCH-001).

Provider answer confidence and event probability are stored separately;
only probabilities whose semantics map to a defined label enter calibration.
Question ids and semantics are versioned (question_version).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import MsgBase, ValueObject, Probability, require_tz, utcnow, new_id
from .enums import ConfidenceSemantics, ProviderKind

# The ARCH-001 question set; actual batch limits decided by capability check
# (TASK-11). Unknown question ids must be rejected at the provider boundary.
KNOWN_QUESTIONS = {
    "thesis_supported",
    "entry_suitable",
    "continuation",
    "breakout_quality",
    "liquidity_healthy",
    "adverse_move",
    "exit_supported",
    "evidence_sufficient",
}


class Judgment(ValueObject):
    question_id: str
    value: str | None = None  # e.g. "yes"/"no"/"unsure" per question schema
    probability: Probability | None = None
    probability_semantics: ConfidenceSemantics = ConfidenceSemantics.UNSPECIFIED
    missing_reason: str | None = None

    @model_validator(mode="after")
    def _answered_or_refused(self):
        if self.probability is None and not self.missing_reason:
            raise ValueError("judgment needs probability or missing_reason (refusal)")
        return self


    @model_validator(mode="after")
    def _known_question(self):
        if self.question_id not in KNOWN_QUESTIONS:
            raise ValueError(f"unknown question_id: {self.question_id}")
        return self


class JudgmentBatch(MsgBase):
    context_id: str
    thesis_id: str | None
    instrument_id: str
    provider: ProviderKind
    model: str
    question_version: str
    judgments: list[Judgment]
    latency_ms: int
    status: Literal["ok", "partial", "refused", "error", "timeout"]
    is_stub: bool = False
    as_of: datetime
    raw_response_ref: str | None = None  # pointer, sensitive raw stays out

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _questions(self):
        for j in self.judgments:
            if j.question_id not in KNOWN_QUESTIONS:
                raise ValueError(f"unknown question_id: {j.question_id}")
        return self
