"""Versioned fast-decision contracts. Model output never grants authority."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import Field, field_validator, model_validator
from .base import ValueObject, DecimalStr, Probability, require_tz
from .enums import TradeAction
from ..market.base import BEIJING

FEATURE_FIELDS = {'close','ret_1m','ret_5m','ret_15m','vwap','atr_14','volume_ratio'}

class Condition(ValueObject):
    field: str
    operator: Literal['gt','gte','lt','lte','eq']
    value: DecimalStr

    @field_validator('field')
    @classmethod
    def known(cls, value):
        if value not in FEATURE_FIELDS:
            raise ValueError('unknown condition field')
        return value

    def matches(self, features):
        if self.field not in features:
            return False
        left = Decimal(str(features[self.field]))
        if not left.is_finite():
            return False
        return {'gt':left>self.value,'gte':left>=self.value,'lt':left<self.value,
                'lte':left<=self.value,'eq':left==self.value}[self.operator]

class StrategyPlan(ValueObject):
    schema_version: Literal[1] = 1
    plan_id: str
    instrument_id: str
    currency: Literal['CNY'] = 'CNY'
    market: str
    rule_version: str
    prompt_version: str = 'strategy-1'
    model: Literal['deepseek-flash'] = 'deepseek-flash'
    created_at: datetime
    data_cutoff: datetime
    available_at: datetime
    valid_until: datetime
    evidence_ids: list[str]
    trend: str
    risk: str
    critic: str
    reason: str
    allowed_actions: list[TradeAction]
    entry_conditions: list[Condition]
    exit_conditions: list[Condition]
    invalidation_conditions: list[Condition]
    holding_horizon_seconds: int = Field(gt=0, le=14400)

    @field_validator('created_at','data_cutoff','available_at','valid_until')
    @classmethod
    def aware(cls, v):
        return require_tz(v)

    @model_validator(mode='after')
    def temporal(self):
        if not self.data_cutoff <= self.created_at <= self.available_at < self.valid_until:
            raise ValueError('invalid plan times')
        if (self.valid_until-self.created_at).total_seconds() > 1200:
            raise ValueError('plan TTL exceeds frozen maximum')
        if not self.evidence_ids or not self.allowed_actions or len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError('plan missing evidence/actions')
        if TradeAction.BUY in self.allowed_actions and not self.entry_conditions:
            raise ValueError('BUY plan requires computable entry conditions')
        return self

    def valid_at(self, at, instrument_id, rule_version):
        return (self.instrument_id==instrument_id and self.rule_version==rule_version
                and self.available_at <= at < self.valid_until
                and self.created_at.astimezone(BEIJING).date()==at.astimezone(BEIJING).date())

class FastDecisionState(ValueObject):
    schema_version: Literal[1] = 1
    context_id: str
    instrument_id: str
    observed_at: datetime
    data_cutoff: datetime
    source_time: datetime
    fetched_at: datetime
    available_at: datetime
    market: str
    currency: Literal['CNY'] = 'CNY'
    rule_version: str
    features: dict[str, DecimalStr]
    bars: list[dict]
    account: dict
    allowed_actions: list[TradeAction]
    plan: StrategyPlan
    quality: dict
    fees: dict
    risk_limits: dict
    budget: dict

    @field_validator('observed_at','data_cutoff','source_time','fetched_at','available_at')
    @classmethod
    def aware(cls,v):
        return require_tz(v)

    @model_validator(mode='after')
    def times(self):
        if any(v>self.observed_at for v in (self.data_cutoff,self.source_time,self.fetched_at,self.available_at)):
            raise ValueError('future state fact')
        if not self.plan.valid_at(self.observed_at,self.instrument_id,self.rule_version):
            raise ValueError('invalid strategy plan')
        return self

class DecisionOutcome(ValueObject):
    schema_version: Literal[1] = 1
    decision_id: str
    raw_action: str
    action: TradeAction
    reason: str
    plan_id: str
    context_id: str
    account_version: int
    as_of: datetime
    valid_until: datetime
    answers: dict
    model: str
    prompt_version: str = 'fast-1'
    latency_ms: int = 0
    cost: str | None = None
    usage: dict = Field(default_factory=dict)
    refresh_requested: bool = False
    action_confidence: Probability = Decimal('0')

    @field_validator('as_of','valid_until')
    @classmethod
    def aware(cls,v):
        return require_tz(v)
