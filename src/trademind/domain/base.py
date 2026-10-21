"""Base message contract and strict scalar types.

Unit contract (ARCH-001): amounts/quantities travel as decimal strings; returns
are decimal ratios; bps fields are named *_bps; probabilities live in [0,1] and
carry an explicit semantics; missing values are None + missing_reason.
NaN / Infinity / naive datetimes / unknown actions are rejected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, BeforeValidator,field_validator

from .. import SCHEMA_VERSION

__all__ = [
    "DecimalStr",
    "RatioStr",
    "Probability",
    "MsgBase",
    "new_id",
    "utcnow",
    "FiniteDecimal",
    "require_tz",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, (int, float, str)):
        try:
            d = Decimal(str(value))
        except InvalidOperation as exc:  # noqa: PERF203
            raise ValueError(f"not a valid decimal: {value!r}") from exc
    else:
        raise ValueError(f"not a valid decimal: {value!r}")
    if not d.is_finite():
        raise ValueError(f"NaN/Infinity not allowed: {value!r}")
    return d


DecimalStr = Annotated[Decimal, BeforeValidator(_to_decimal)]
"""Amount or quantity; serialized as string."""

RatioStr = Annotated[Decimal, BeforeValidator(_to_decimal)]
"""Decimal ratio in [-1, 1] semantics per field; serialized as string."""


def _check_probability(v: Any) -> Decimal:
    d = _to_decimal(v)
    if d < 0 or d > 1:
        raise ValueError(f"probability must be in [0,1], got {d}")
    return d


Probability = Annotated[Decimal, BeforeValidator(_check_probability)]
"""Probability in [0,1]; semantics must be declared next to it."""

FiniteDecimal = Decimal


class ValueObject(BaseModel):
    """Embedded value object; identity and trace come from the parent message."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MsgBase(BaseModel):
    """Every message: schema_version, id, trace_id, created_at (tz-aware)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: str = SCHEMA_VERSION
    id: str
    trace_id: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_datetime(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("naive datetime not allowed; use tz-aware UTC")
        return v.astimezone(timezone.utc)


def require_tz(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("naive datetime not allowed; use tz-aware UTC")
    return v


def dump(obj: BaseModel) -> dict[str, Any]:
    """JSON-safe dump with Decimals as strings and datetimes as ISO-8601."""
    return obj.model_dump(mode="json")
