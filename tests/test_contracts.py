"""TASK-06 contract tests: legal fixtures pass, illegal inputs rejected."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from conftest import T0, legal_evidence, legal_intent, legal_thesis

from trademind.config import _validate_mode_isolation, load_config
from trademind.errors import ConfigError
from trademind.domain.context import ContextSnapshot, FeatureVector, PortfolioView
from trademind.domain.enums import ConfidenceSemantics, TradeAction
from trademind.domain.instruments import FeeSchedule, MarketProfile
from trademind.domain.intents import PortfolioIntent
from trademind.domain.judgments import Judgment, JudgmentBatch
from trademind.domain.memory import MemoryRecord
from trademind.domain.orders import Fill, Order
from trademind.domain.outcome import Outcome
from trademind.domain.research import AnalystReport, Fact
from trademind.domain.base import new_id


def test_legal_fixtures_pass():
    ev = legal_evidence()
    th = legal_thesis()
    it = legal_intent()
    assert ev.evidence_id and th.thesis_id and it.decision_id
    assert it.schema_version == th.schema_version == ev.schema_version


def test_unknown_action_rejected():
    with pytest.raises(ValidationError):
        legal_intent(action="SHORT")


def test_nan_and_infinity_rejected():
    with pytest.raises(ValidationError):
        legal_intent(target_weight="NaN")
    with pytest.raises(ValidationError):
        legal_intent(target_weight="Infinity")
    with pytest.raises(ValidationError):
        legal_intent(target_weight=float("nan"))


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        legal_thesis(valid_until=datetime(2026, 9, 2, 0, 0, 0))  # naive
    with pytest.raises(ValidationError):
        legal_evidence(available_at=T0.replace(tzinfo=None), ingested_at=T0)


def test_missing_source_rejected():
    with pytest.raises(ValidationError):
        legal_evidence(source_id="")


def test_published_before_event_rejected():
    with pytest.raises(ValidationError):
        legal_evidence(
            event_time=T0,
            published_at=T0.replace(tzinfo=timezone.utc) - __import__("datetime").timedelta(hours=2),
        )


def test_unknown_published_requires_missing_reason():
    ev = legal_evidence(published_at=None, missing_reason="source does not disclose")
    assert ev.published_at is None
    with pytest.raises(ValidationError):
        legal_evidence(published_at=None)


def test_action_weight_contradiction_detected():
    it = legal_intent(action=TradeAction.HOLD, target_weight=Decimal("0.5"), current_weight=Decimal("0.1"))
    problems = it.check_against_account()
    assert problems, "HOLD with changed weight must be flagged"

    buy = legal_intent(action=TradeAction.BUY, target_weight=Decimal("0.05"), current_weight=Decimal("0.10"))
    assert any("exceed" in p for p in buy.check_against_account())

    sell = legal_intent(action=TradeAction.SELL, target_weight=Decimal("0.30"), current_weight=Decimal("0.10"))
    assert any("below" in p for p in sell.check_against_account())


def test_sell_negative_weight_rejected():
    with pytest.raises(ValidationError):
        legal_intent(action=TradeAction.SELL, target_weight=Decimal("-0.1"))


def test_weight_above_one_rejected():
    with pytest.raises(ValidationError):
        legal_intent(target_weight=Decimal("1.5"))


def test_probability_bounds():
    from trademind.domain.research import TradeThesis

    with pytest.raises(ValidationError):
        legal_thesis(confidence=Decimal("1.2"))
    th = legal_thesis(confidence=Decimal("0"))
    assert th.confidence == 0


def test_judgment_needs_answer_or_refusal():
    with pytest.raises(ValidationError):
        Judgment(question_id="continuation")
    j = Judgment(question_id="continuation", missing_reason="model refused")
    assert j.probability is None
    with pytest.raises(ValidationError):
        Judgment(question_id="not_a_question", missing_reason="x")


def test_judgment_batch_stub_status_shape():
    batch = JudgmentBatch(
        id=new_id("jb"), trace_id=new_id("t"), created_at=T0,
        context_id=new_id("c"), thesis_id=None, instrument_id="600519.SH",
        provider="stub", model="stub", question_version="qv-test",
        judgments=[{"question_id": "continuation", "probability": "0.6", "probability_semantics": "self_reported_answer_confidence"}],
        latency_ms=1, status="ok", is_stub=True, as_of=T0,
    )
    assert batch.is_stub and batch.judgments[0].probability == Decimal("0.6")


def test_thesis_requires_invalidation():
    with pytest.raises(ValidationError):
        legal_thesis(invalidations=[])


def test_analyst_facts_must_cite_visible_evidence():
    with pytest.raises(ValidationError):
        AnalystReport(
            id=new_id("r"), trace_id=new_id("t"), created_at=T0,
            role="news_analyst", context_id=new_id("c"), instrument_id="600519.SH",
            evidence_ids=["ev-1"],
            facts=[Fact(statement="profit up", evidence_id="ev-unknown", kind="fact")],
            analysis="positive",
        )


def test_risk_review_revise_requires_proposed_weight():
    from trademind.domain.intents import RiskReview

    with pytest.raises(ValidationError):
        RiskReview(id=new_id("rr"), trace_id=new_id("t"), created_at=T0,
                   decision_id="d", intent_id="i", verdict="REVISE", reason="too big")
    with pytest.raises(ValidationError):
        RiskReview(id=new_id("rr"), trace_id=new_id("t"), created_at=T0,
                   decision_id="d", intent_id="i", verdict="APPROVE", reason="ok",
                   proposed_weight="0.3")


def test_outcome_maturity_contract():
    with pytest.raises(ValidationError):
        Outcome(id=new_id("o"), trace_id=new_id("t"), created_at=T0, decision_id="d",
                label_definition="5d forward net return", label_version="lv-1",
                horizon_end=T0, available_at=T0)  # neither final nor censored
    o = Outcome(id=new_id("o"), trace_id=new_id("t"), created_at=T0, decision_id="d",
                label_definition="5d forward net return", label_version="lv-1",
                horizon_end=T0, available_at=T0, censored_reason="not_mature")
    assert not o.is_final


def test_memory_visibility():
    m = MemoryRecord(id=new_id("m"), trace_id=new_id("t"), created_at=T0,
                     memory_id=new_id("m"), type="TRADE", available_at=T0,
                     summary="sold too early")
    assert m.visible_at(T0)
    assert not m.visible_at(T0 - __import__("datetime").timedelta(seconds=1))
    m2 = MemoryRecord(id=new_id("m"), trace_id=new_id("t"), created_at=T0,
                      memory_id=new_id("m"), type="LESSON", available_at=T0,
                      review_state="RETRACTED", summary="wrong lesson")
    assert not m2.visible_at(T0)


def test_fill_positive_constraints():
    with pytest.raises(ValidationError):
        Fill(id=new_id("f"), trace_id=new_id("t"), created_at=T0, fill_id=new_id("f"),
             order_id="o1", provider_fill_id="pf1", quantity="0", price="100",
             fee="1", executed_at=T0, available_at=T0)


def test_market_profile_rejects_short_selling():
    fees = FeeSchedule(id=new_id("fs"), trace_id=new_id("t"), created_at=T0,
                       commission_rate="0.00025", commission_min="5",
                       stamp_duty_rate_sell="0.0005", transfer_fee_rate="0.00001")
    with pytest.raises(ValidationError):
        MarketProfile(id=new_id("mp"), trace_id=new_id("t"), created_at=T0,
                      profile_id="mp-xshg-main", market="XSHG",
                      effective_from=__import__("datetime").date(2026, 1, 1),
                      source_ref="test", price_limit_ratio="0.10", fees=fees,
                      short_selling_allowed=True)


def test_context_snapshot_expiry_and_hash_stable():
    fv = FeatureVector(id=new_id("fv"), trace_id=new_id("t"), created_at=T0,
                       feature_version="fv-1", instrument_id="600519.SH", as_of=T0,
                       values={"ma20": "1500.5"}, missing_fields={"spread": "no quotes"})
    snap = ContextSnapshot(id=new_id("cs"), trace_id=new_id("t"), created_at=T0,
                           context_id=new_id("cs"), as_of=T0,
                           expires_at=T0 + __import__("datetime").timedelta(minutes=15),
                           instrument_id="600519.SH", universe_version="u1",
                           evidence_ids=["e2", "e1"], features=fv,
                           memory_ids=["m1"])
    assert snap.compute_hash() == snap.compute_hash()
    with pytest.raises(ValidationError):
        ContextSnapshot(id=new_id("cs"), trace_id=new_id("t"), created_at=T0,
                        context_id=new_id("cs"), as_of=T0, expires_at=T0,
                        instrument_id="600519.SH", universe_version="u1",
                        evidence_ids=[], features=fv)


def test_mode_isolation_replay_rejects_credentials():
    from trademind.config import AppConfig, DataConfig, APIConfig

    cfg = AppConfig(mode="replay", data=DataConfig(tushare_token="x"), api=APIConfig())
    with pytest.raises(ConfigError):
        _validate_mode_isolation(cfg)
    cfg2 = AppConfig(mode="live")
    with pytest.raises(ConfigError):
        _validate_mode_isolation(cfg2)
    cfg3 = AppConfig(mode="bogus")
    with pytest.raises(ConfigError):
        _validate_mode_isolation(cfg3)


def test_load_example_config_defaults():
    cfg = load_config()
    assert cfg.mode == "paper"
    assert cfg.risk.max_position_weight == Decimal("0.20")
    assert cfg.universe, "example universe should not be empty"


# -- TASK-20261025-03: strategy exit contract (ISSUE-20261025-01) --

def _exit_contract_plan(allowed, exits):
    from datetime import datetime, timedelta, timezone
    from trademind.domain.strategy import StrategyPlan
    at = datetime(2026, 10, 12, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    entry = [{'field': 'close', 'operator': 'gt', 'value': '1'}] if 'BUY' in allowed else []
    return StrategyPlan(plan_id='plan-x', instrument_id='513050.SH', market='XSHG',
        rule_version='r', prompt_version='strategy-5', created_at=at, data_cutoff=at,
        available_at=at, valid_until=at + timedelta(seconds=600), evidence_ids=['e'],
        trend='t', risk='r', critic='c', reason='测试', allowed_actions=allowed,
        entry_conditions=entry, exit_conditions=exits,
        invalidation_conditions=[], holding_horizon_seconds=300)


def test_exit_contract_buy_requires_sell_and_exits():
    from trademind.domain.strategy import exit_contract_violation
    buy_only = _exit_contract_plan(['BUY', 'HOLD', 'WAIT'],
                                   [{'field': 'close', 'operator': 'lt', 'value': '2'}])
    no_exits = _exit_contract_plan(['BUY', 'SELL'], [])
    assert exit_contract_violation(buy_only) == 'BUY plan requires SELL with computable exit conditions'
    assert exit_contract_violation(no_exits) == 'BUY plan requires SELL with computable exit conditions'


def test_exit_contract_sell_requires_exits():
    from trademind.domain.strategy import exit_contract_violation
    plan = _exit_contract_plan(['SELL', 'HOLD', 'WAIT'], [])
    assert exit_contract_violation(plan) == 'SELL plan requires computable exit conditions'


def test_exit_contract_valid_plans_pass():
    from trademind.domain.strategy import exit_contract_violation
    assert exit_contract_violation(_exit_contract_plan(
        ['BUY', 'SELL', 'HOLD', 'WAIT'],
        [{'field': 'close', 'operator': 'lt', 'value': '2'}])) is None
    assert exit_contract_violation(_exit_contract_plan(['WAIT'], [])) is None
    assert exit_contract_violation(_exit_contract_plan(
        ['SELL', 'HOLD', 'WAIT'], [{'field': 'close', 'operator': 'lt', 'value': '2'}])) is None


def test_legacy_buy_only_plan_still_parses_for_archives():
    """The rule is deliberately not a pydantic validator: history loaders
    (fast_comparison, plan history) must keep reading pre-contract plans."""
    from trademind.domain.enums import TradeAction
    plan = _exit_contract_plan(['BUY'], [])
    assert plan.allowed_actions == [TradeAction.BUY]
