"""Outcome and Reflection contracts (ARCH-001)."""

from __future__ import annotations

from datetime import datetime

from pydantic import field_validator, model_validator

from .base import DecimalStr, MsgBase, require_tz, utcnow, new_id


class Outcome(MsgBase):
    decision_id: str
    label_definition: str  # preregistered label; versioned elsewhere
    label_version: str
    horizon_end: datetime
    available_at: datetime  # when the label became computable
    realized_pnl: DecimalStr | None = None
    fees: DecimalStr | None = None
    return_ratio: DecimalStr | None = None
    benchmark_return: DecimalStr | None = None
    censored_reason: str | None = None  # e.g. "not_mature", "position_open"
    is_final: bool = False

    @field_validator("horizon_end", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _mature(self):
        if not self.is_final and not self.censored_reason:
            raise ValueError("un-matured outcome must carry censored_reason")
        if self.is_final and self.censored_reason:
            raise ValueError("final outcome cannot carry censored_reason")
        return self


class Reflection(MsgBase):
    decision_id: str
    outcome_ids: list[str]
    summary: str
    lesson: str
    sample_size: int
    counterexamples: list[str] = []
    attributes_luck: bool = False  # explicit decision-quality vs luck split
    confidence: str = "low"
    is_stub: bool = False
    model: str = ""

    @model_validator(mode="after")
    def _evidence(self):
        if self.sample_size <= 0:
            raise ValueError("reflection must cite sample size")
        return self
