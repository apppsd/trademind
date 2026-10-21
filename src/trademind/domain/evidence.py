"""Evidence and MarketEvent contracts (ARCH-001).

Time fields: event_time (when it happened), published_at (when public),
available_at (when first obtainable by this system), ingested_at (when stored).
Replay may only use rows with available_at <= as_of.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import MsgBase, require_tz, utcnow, new_id
from .enums import DataQuality, Dataset


def content_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Evidence(MsgBase):
    """Immutable, hash-addressed observation. Missing published_at must stay
    None with missing_reason set; never inferred."""

    evidence_id: str
    source_id: str
    source_uri: str
    dataset: Dataset
    instrument_ids: list[str] = Field(default_factory=list)
    event_time: datetime | None = None
    published_at: datetime | None = None
    available_at: datetime
    ingested_at: datetime
    content_hash: str
    revision: int = 1
    supersedes_evidence_id: str | None = None
    payload_ref: str  # storage pointer; big raw content stays out of messages
    summary: str = ""
    quality: DataQuality = DataQuality.OK
    missing_reason: str | None = None
    license_ref: str = "internal-fixture"
    availability_basis: str = "available_at"  # how available_at was established
    provider: str = "fixture"
    backfill: bool = False

    @field_validator("event_time", "published_at", "available_at", "ingested_at")
    @classmethod
    def _aware(cls, v):
        if v is not None:
            require_tz(v)
        return v

    @model_validator(mode="after")
    def _provenance(self):
        if not self.source_id or not self.source_uri:
            raise ValueError("evidence requires a source (source_id/source_uri)")
        if self.published_at is None and not self.missing_reason:
            raise ValueError(
                "published_at unknown must carry missing_reason, not be inferred"
            )
        if self.event_time and self.published_at and self.published_at < self.event_time:
            raise ValueError("published_at earlier than event_time")
        if self.available_at > self.ingested_at:
            raise ValueError("available_at cannot be after ingested_at")
        return self


class MarketEvent(MsgBase):
    """Router-level event derived from evidence; references, never copies."""

    event_id: str
    kind: Dataset
    instrument_id: str | None
    evidence_id: str
    available_at: datetime
    payload_ref: str

    @field_validator("available_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)
