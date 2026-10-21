"""Research contracts: AnalystReport, debate, TradeThesis (ARCH-001)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import DecimalStr, MsgBase, ValueObject, Probability, require_tz, utcnow, new_id
from .enums import ConfidenceSemantics, Direction


class Fact(ValueObject):
    statement: str
    evidence_id: str  # every fact must trace to visible evidence
    kind: Literal["fact", "interpretation", "uncertainty"]


class AnalystReport(MsgBase):
    role: Literal[
        "fundamental_analyst",
        "news_analyst",
        "technical_analyst",
        "macro_analyst",
        "flow_analyst",
    ]
    context_id: str
    instrument_id: str
    evidence_ids: list[str]
    facts: list[Fact]
    analysis: str
    unknowns: list[str] = []
    risks: list[str] = []
    is_stub: bool = False
    model: str = ""
    prompt_id: str = ""

    @field_validator("facts")
    @classmethod
    def _traceable(cls, v):
        for f in v:
            if not f.evidence_id:
                raise ValueError("every fact needs an evidence_id")
        return v

    @model_validator(mode="after")
    def _evidence(self):
        fact_ids = {f.evidence_id for f in self.facts}
        missing = fact_ids - set(self.evidence_ids)
        if missing:
            raise ValueError(f"facts cite evidence outside visible set: {missing}")
        return self


class DebateTurn(ValueObject):
    role: Literal["bull", "bear", "research_manager"]
    round_no: int
    argument: str
    cites_report_ids: list[str] = []


class TradeThesis(MsgBase):
    """Research output: a debatable, expiring claim. Never an order."""

    thesis_id: str
    instrument_id: str
    context_id: str
    direction: Direction
    horizon_seconds: int
    confidence: Probability
    confidence_semantics: ConfidenceSemantics
    evidence_ids: list[str]
    supporting_reasons: list[str]
    counterarguments: list[str]
    invalidations: list[str]
    valid_until: datetime
    available_at: datetime | None = None
    is_stub: bool = False
    debate: list[DebateTurn] = []
    model: str = ""
    prompt_id: str = ""

    @field_validator("valid_until", "available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v) if v is not None else None

    @model_validator(mode="after")
    def _shape(self):
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if not self.invalidations:
            raise ValueError("thesis must state invalidation conditions")
        if self.direction == Direction.LONG and not self.supporting_reasons:
            raise ValueError("LONG thesis needs supporting reasons")
        return self

    def is_valid_at(self, now: datetime) -> bool:
        return (self.available_at is None or self.available_at <= now) and now <= self.valid_until
