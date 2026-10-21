"""Intent chain contracts: TradeIntent → RiskReview → PortfolioIntent → GuardResult.

Rules (ARCH-001): AI states intent as target weight of current equity; HOLD
weight equals current weight or None; BUY means target above current, SELL
below current and >= 0; no shorting in V1. Guard never mutates these objects.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import DecimalStr, MsgBase, ValueObject, Probability, require_tz, utcnow, new_id
from .enums import (
    ConfidenceSemantics,
    GuardVerdict,
    RiskVerdict,
    RunMode,
    TradeAction,
)


class ExitCondition(ValueObject):
    description: str
    kind: Literal["stop_loss", "take_profit", "thesis_invalidation", "time_stop", "other"]
    reference: DecimalStr | None = None  # e.g. price level if applicable


class TradeIntent(MsgBase):
    """Trader output. Still must pass AI risk + portfolio review."""

    decision_id: str
    context_id: str
    thesis_id: str
    instrument_id: str
    portfolio_version: int
    account_id: str
    action: TradeAction
    target_weight: DecimalStr | None
    current_weight: DecimalStr | None = None
    confidence: Probability
    confidence_semantics: ConfidenceSemantics
    holding_horizon_seconds: int
    evidence_ids: list[str]
    reason: str
    exit_conditions: list[ExitCondition] = []
    valid_until: datetime
    entry_type: Literal["open", "increase", "reduce", "exit_review"] = "open"
    limit_price: DecimalStr | None = None
    is_stub: bool = False
    model: str = ""
    prompt_id: str = ""
    intent_version: int = 1
    supersedes_decision_id: str | None = None

    @field_validator("valid_until")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _direction(self):
        if self.action == TradeAction.SELL and self.target_weight is not None:
            if self.target_weight < 0:
                raise ValueError("SELL target_weight must be >= 0 (no shorting)")
        if self.target_weight is not None and self.target_weight > 1:
            raise ValueError("target_weight is a fraction of equity, must be <= 1")
        if self.action == TradeAction.BUY and self.holding_horizon_seconds <= 0:
            raise ValueError("BUY needs a positive holding horizon")
        if self.action == TradeAction.WAIT and self.target_weight is not None and self.target_weight > 0:
            raise ValueError("WAIT cannot have positive target_weight")
        return self

    def check_against_account(self) -> list[str]:
        """Post-construction consistency vs the account snapshot weight.

        Schema cannot know the account, so agents call this before persisting;
        violations produce errors, not silent rewrites.
        """
        problems: list[str] = []
        w = self.target_weight
        cw = self.current_weight
        if self.action == TradeAction.HOLD:
            if w is not None and cw is not None and w != cw:
                problems.append("HOLD target_weight must equal current weight or be None")
        elif self.action == TradeAction.WAIT:
            if w is not None and w != 0:
                problems.append("WAIT target_weight must be None or 0")
        elif self.action == TradeAction.BUY:
            if w is None:
                problems.append("BUY requires target_weight")
            elif cw is not None and w <= cw:
                problems.append("BUY target_weight must exceed current weight")
        elif self.action == TradeAction.SELL:
            if w is None:
                problems.append("SELL requires target_weight")
            elif cw is not None and w >= cw:
                problems.append("SELL target_weight must be below current weight")
        return problems


def normalize_intent_action(
    action: TradeAction, current_weight: Decimal | None
) -> tuple[TradeAction, str | None]:
    """Normalize HOLD to WAIT when holding no position (TASK-40)."""
    if action == TradeAction.HOLD and (current_weight is None or current_weight <= 0):
        return TradeAction.WAIT, "normalized_from_hold_zero_position"
    return action, None


class RiskReview(MsgBase):
    decision_id: str
    intent_id: str
    verdict: RiskVerdict
    risks: list[str] = []
    proposed_weight: DecimalStr | None = None  # only for REVISE
    reason: str
    evidence_ids: list[str] = []
    is_stub: bool = False
    model: str = ""
    prompt_id: str = ""

    @model_validator(mode="after")
    def _revise_shape(self):
        if self.verdict == RiskVerdict.REVISE and self.proposed_weight is None:
            raise ValueError("REVISE must propose a new weight; it becomes a new intent version")
        if self.verdict != RiskVerdict.REVISE and self.proposed_weight is not None:
            raise ValueError("proposed_weight only valid on REVISE")
        return self


class PortfolioIntent(MsgBase):
    """The only intent form Guard accepts."""

    intent_id: str
    decision_id: str
    account_id: str
    portfolio_version: int
    instrument_id: str
    action: TradeAction
    target_weight: DecimalStr | None
    current_weight: DecimalStr | None = None
    valid_until: datetime
    mode: RunMode
    limit_price: DecimalStr | None = None
    risk_review_id: str | None = None
    currency: str = "CNY"

    @field_validator("valid_until")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)

    @model_validator(mode="after")
    def _shape(self):
        if self.target_weight is not None and (self.target_weight < 0 or self.target_weight > 1):
            raise ValueError("target_weight must be within [0,1]")
        return self


class GuardResult(MsgBase):
    """One-shot: never reusable across intents."""

    intent_id: str
    account_version: int
    checked_at: datetime
    verdict: GuardVerdict
    reason_codes: list[str] = []
    policy_version: str
    expires_at: datetime
    details: dict[str, str] = {}

    @field_validator("checked_at", "expires_at")
    @classmethod
    def _aware(cls, v):
        return require_tz(v)


def create_exit_portfolio_intent(
    account_id: str,
    portfolio_version: int,
    instrument_id: str,
    current_weight: Decimal,
    valid_until: datetime,
    mode: RunMode = RunMode.PAPER,
    limit_price: Decimal | None = None,
    trace_id: str | None = None,
) -> PortfolioIntent:
    """Create deterministic mult-lot position exit PortfolioIntent (target_weight = 0)."""
    return PortfolioIntent(
        id=new_id("pi"),
        trace_id=trace_id or new_id("tr"),
        created_at=utcnow(),
        intent_id=new_id("pi"),
        decision_id=new_id("dec_exit"),
        account_id=account_id,
        portfolio_version=portfolio_version,
        instrument_id=instrument_id,
        action=TradeAction.SELL,
        target_weight=Decimal("0"),
        current_weight=current_weight,
        valid_until=valid_until,
        mode=mode,
        limit_price=limit_price,
    )
