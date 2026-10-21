"""ContextSnapshot, features, and account view contracts (ARCH-001)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import DecimalStr, MsgBase, require_tz, utcnow, new_id
from .enums import DataQuality


class FeatureVector(MsgBase):
    """Deterministic facts only. Missing fields are absent + listed, not 0."""

    feature_version: str
    instrument_id: str
    as_of: datetime
    values: dict[str, DecimalStr] = Field(default_factory=dict)
    window: dict[str, Any] = Field(default_factory=dict)
    missing_fields: dict[str, str] = Field(default_factory=dict)  # field -> reason
    quality: DataQuality = DataQuality.OK

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


class PortfolioView(MsgBase):
    """Read-only account view embedded into context; references a version."""

    portfolio_version: int
    account_id: str
    as_of: datetime
    currency: str
    cash: DecimalStr
    equity: DecimalStr
    positions: dict[str, dict[str, str]]  # instrument_id -> {qty, market_value, weight,...}
    reserved_cash: DecimalStr = Decimal("0")

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


class ContextSnapshot(MsgBase):
    """Immutable point-in-time view for one instrument. Frozen content and
    reproducible hash; model input budget enforced at build time."""

    context_id: str
    as_of: datetime
    expires_at: datetime
    instrument_id: str
    universe_version: str
    evidence_ids: list[str]
    features: FeatureVector
    portfolio: PortfolioView | None = None
    memory_ids: list[str] = []
    missing_fields: dict[str, str] = {}
    content_hash: str = ""
    input_budget_tokens: int | None = None
    truncated: bool = False
    aux: dict[str, Any] = Field(default_factory=dict)  # news/fundamentals/last bars

    @field_validator("as_of", "expires_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _window(self):
        if self.expires_at <= self.as_of:
            raise ValueError("expires_at must be after as_of")
        return self

    def compute_hash(self) -> str:
        payload = {
            "context_id": self.context_id,
            "as_of": self.as_of.isoformat(),
            "instrument_id": self.instrument_id,
            "evidence_ids": sorted(self.evidence_ids),
            "features": {k: str(v) for k, v in sorted(self.features.values.items())},
            "feature_version": self.features.feature_version,
            "memory_ids": sorted(self.memory_ids),
            "portfolio_version": self.portfolio.portfolio_version if self.portfolio else None,
            "aux_keys": sorted(self.aux.keys()),
        }
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
