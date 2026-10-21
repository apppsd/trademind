"""Tests for TASK-40: T0 experiment contracts, config freezing, and action semantics."""

from decimal import Decimal
from datetime import datetime, timezone
import pytest

from trademind.config import load_config, ConfigError, T0ExperimentConfig
from trademind.domain.enums import TradeAction, ConfidenceSemantics
from trademind.domain.intents import TradeIntent, normalize_intent_action


def test_t0_config_loading_and_invariants(tmp_path):
    cfg = load_config("config/t0-shadow.example.toml")
    assert cfg.t0.enabled is True
    assert cfg.t0.profile == "jev_deepseek"
    assert cfg.t0.initial_cash == Decimal("500")
    assert cfg.paper.initial_cash == Decimal("500")
    assert cfg.t0.max_total_exposure == Decimal("300")
    assert cfg.t0.max_single_exposure == Decimal("200")
    assert cfg.t0.min_cash_reserve == Decimal("100")
    assert cfg.t0.stop_loss_ratio == Decimal("0.008")
    assert cfg.t0.max_daily_loss_ratio == Decimal("0.03")
    assert cfg.t0.consecutive_losses_limit == 3
    assert cfg.t0.daily_new_orders_limit == 10
    assert cfg.t0.daily_model_cost_limit_cny == Decimal("5.0")
    assert cfg.t0.daily_decision_call_budget == 100
    assert cfg.t0.whitelist == ["513050.SH", "513100.SH", "518880.SH"]
    assert cfg.universe == ["513050.SH", "513100.SH", "518880.SH"]
    assert cfg.account_id == "t0-paper-001"
    assert "t0_shadow.db" in cfg.storage.url


def test_t0_budget_invariant_error(tmp_path):
    bad_toml = tmp_path / "bad_t0.toml"
    bad_toml.write_text(
        """
mode = "paper"
[t0]
enabled = true
initial_cash = 250
min_cash_reserve = 100
max_single_exposure = 200
"""
    )
    with pytest.raises(ConfigError, match="T0 budget invalid"):
        load_config(bad_toml)


def test_trade_action_wait_semantics():
    assert TradeAction.WAIT == "WAIT"
    assert TradeAction("WAIT") == TradeAction.WAIT


def test_trade_intent_wait_validation():
    from trademind.domain.base import new_id, utcnow
    valid_until = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    intent = TradeIntent(
        id=new_id("ti"),
        trace_id=new_id("tr"),
        created_at=utcnow(),
        decision_id="dec-1",
        context_id="ctx-1",
        thesis_id="the-1",
        instrument_id="510050.SH",
        portfolio_version=1,
        account_id="t0-paper-001",
        action=TradeAction.WAIT,
        target_weight=None,
        current_weight=None,
        confidence=Decimal("0.8"),
        confidence_semantics=ConfidenceSemantics.EVIDENCE_SUPPORT,
        holding_horizon_seconds=0,
        evidence_ids=["ev-1"],
        reason="No setup found, wait",
        valid_until=valid_until,
    )
    assert intent.action == TradeAction.WAIT
    assert intent.check_against_account() == []

    # WAIT with positive weight must raise error
    with pytest.raises(ValueError, match="WAIT cannot have positive target_weight"):
        TradeIntent(
            id=new_id("ti"),
            trace_id=new_id("tr"),
            created_at=utcnow(),
            decision_id="dec-2",
            context_id="ctx-1",
            thesis_id="the-1",
            instrument_id="510050.SH",
            portfolio_version=1,
            account_id="t0-paper-001",
            action=TradeAction.WAIT,
            target_weight=Decimal("0.2"),
            confidence=Decimal("0.8"),
            confidence_semantics=ConfidenceSemantics.EVIDENCE_SUPPORT,
            holding_horizon_seconds=0,
            evidence_ids=["ev-1"],
            reason="Illegal wait with weight",
            valid_until=valid_until,
        )


def test_hold_to_wait_normalization():
    # When holding no position, HOLD normalizes to WAIT
    action, reason = normalize_intent_action(TradeAction.HOLD, current_weight=Decimal("0"))
    assert action == TradeAction.WAIT
    assert reason == "normalized_from_hold_zero_position"

    action, reason = normalize_intent_action(TradeAction.HOLD, current_weight=None)
    assert action == TradeAction.WAIT
    assert reason == "normalized_from_hold_zero_position"

    # When holding a position, HOLD remains HOLD
    action, reason = normalize_intent_action(TradeAction.HOLD, current_weight=Decimal("0.25"))
    assert action == TradeAction.HOLD
    assert reason is None

    # BUY remains BUY
    action, reason = normalize_intent_action(TradeAction.BUY, current_weight=Decimal("0"))
    assert action == TradeAction.BUY
    assert reason is None
