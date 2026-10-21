"""MemoryRecord contract (ARCH-001)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .base import MsgBase, require_tz, utcnow, new_id
from .enums import MemoryReviewState, MemoryType


class MemoryRecord(MsgBase):
    memory_id: str
    type: MemoryType
    instrument_id: str | None = None
    evidence_ids: list[str] = []
    decision_ids: list[str] = []
    available_at: datetime  # replay visibility boundary
    valid_until: datetime | None = None
    version: int = 1
    supersedes_memory_id: str | None = None
    summary: str
    confidence: str = "low"
    review_state: MemoryReviewState = MemoryReviewState.PROVISIONAL
    keywords: list[str] = []
    embedding: list[float] | None = None  # interface-isolated; not required

    @field_validator("available_at", "valid_until")
    @classmethod
    def _aware(cls, v):
        if v is not None:
            require_tz(v)
        return v

    def visible_at(self, as_of: datetime) -> bool:
        if self.available_at > as_of:
            return False
        if self.review_state == MemoryReviewState.RETRACTED:
            return False
        if self.valid_until is not None and as_of > self.valid_until:
            return False
        return True
