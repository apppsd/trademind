"""Tests for TASK-57: Minimal Dual-Model Decision Chain (deepseek-flash + TypeSafe JEV)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest

from trademind.agents.pipeline import DualModelPipeline
from trademind.config import T0ExperimentConfig
from trademind.domain.base import new_id, utcnow
from trademind.domain.context import ContextSnapshot, FeatureVector, PortfolioView
from trademind.domain.enums import ConfidenceSemantics, ProviderKind, TradeAction
from trademind.domain.judgments import Judgment, JudgmentBatch
from trademind.models.base import LLMProvider, ModelOutput, build_call


class MockLLM(LLMProvider):
    kind = ProviderKind.DEEPSEEK
    model = "deepseek-flash"
    capability_verified = True

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.call_history: list[dict] = []

    def complete(self, *, role: str, system: str, user: str, trace_id: str, timeout_s: float = 60.0) -> ModelOutput:
        resp_data = self.responses[len(self.call_history)]
        self.call_history.append({"system": system, "user": user, "role": role, "trace_id": trace_id})
        call = build_call(role=role, provider=self.kind, model=self.model, system=system, user=user, trace_id=trace_id)
        call.status = "ok"
        return ModelOutput(
            text=json.dumps(resp_data),
            parsed=resp_data,
            call=call,
        )


class MockJEV:
    def __init__(self, status: str = "ok", answers: dict | None = None):
        self.status = status
        self.answers = answers or {
            "entry_suitable": {"value": "yes", "confidence": 0.8},
            "adverse_move": {"value": "low", "confidence": 0.85},
            "evidence_sufficient": {"value": "yes", "confidence": 0.9},
        }
        self.call_history: list[dict] = []

    def judge(self, context_id: str, thesis_id: str, instrument_id: str, as_of: datetime, questions: list[dict], trace_id: str, state: dict):
        self.call_history.append({
            "context_id": context_id,
            "thesis_id": thesis_id,
            "instrument_id": instrument_id,
            "questions": questions,
            "state": state,
        })
        if self.status != "ok":
            return JudgmentBatch(
                id=new_id("jb"),
                trace_id=trace_id,
                created_at=utcnow(),
                context_id=context_id,
                thesis_id=thesis_id,
                instrument_id=instrument_id,
                provider=ProviderKind.JEV_TYPESAFE,
                model="jev-latest",
                question_version="v1",
                judgments=[],
                latency_ms=100,
                status=self.status,
                as_of=as_of,
            )

        judgments = [
            Judgment(
                question_id=q["id"],
                value=self.answers.get(q["id"], {}).get("value", "yes"),
                probability=Decimal(str(self.answers.get(q["id"], {}).get("confidence", "0.8"))),
                probability_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
            )
            for q in questions
        ]
        return JudgmentBatch(
            id=new_id("jb"),
            trace_id=trace_id,
            created_at=utcnow(),
            context_id=context_id,
            thesis_id=thesis_id,
            instrument_id=instrument_id,
            provider=ProviderKind.JEV_TYPESAFE,
            model="jev-latest",
            question_version="v1",
            judgments=judgments,
            latency_ms=120,
            status="ok",
            as_of=as_of,
        )


def _make_context(positions: dict | None = None) -> ContextSnapshot:
    as_of = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
    tid = new_id("tr")
    cid = new_id("ctx")
    return ContextSnapshot(
        id=cid,
        trace_id=tid,
        created_at=as_of,
        context_id=cid,
        as_of=as_of,
        expires_at=as_of + timedelta(minutes=5),
        instrument_id="510300.SH",
        universe_version="v1",
        features=FeatureVector(
            id=new_id("fv"),
            trace_id=tid,
            created_at=as_of,
            feature_version="feat-v1",
            instrument_id="510300.SH",
            as_of=as_of,
            values={"close": Decimal("4.000"), "vwap": Decimal("3.995"), "ret_5m": Decimal("0.005")},
            missing_fields={},
            window={"regime": "UPTREND"},
        ),
        portfolio=PortfolioView(
            id=new_id("pv"),
            trace_id=tid,
            created_at=as_of,
            portfolio_version=1,
            account_id="paper-t0",
            as_of=as_of,
            currency="CNY",
            cash=Decimal("500.00"),
            equity=Decimal("500.00"),
            positions=positions or {},
        ),
        evidence_ids=["ev-bar-1", "ev-bar-2"],
        aux={"minute_bars": [{"close": "4.000"}]},
    )


def test_dual_model_pipeline_wait_short_circuits():
    # Candidate outputs WAIT -> JEV should NOT be called
    mock_llm = MockLLM([
        {
            "candidate_action": "WAIT",
            "trend_analysis": "Market is sideways",
            "risk_analysis": "Low volatility",
            "critic_analysis": "No clear breakout",
            "confidence": 0.5,
            "reason": "Wait for momentum",
        }
    ])
    mock_jev = MockJEV()
    pipeline = DualModelPipeline(llm_provider=mock_llm, jev_provider=mock_jev)

    context = _make_context()
    intent, meta = pipeline.run(context)

    assert intent.action == TradeAction.WAIT
    assert intent.target_weight == Decimal("0")
    assert meta["jev_called"] is False
    assert len(mock_jev.call_history) == 0
    assert len(mock_llm.call_history) == 1  # Only candidate call


def test_dual_model_pipeline_hold_zero_position_normalized_to_wait():
    # Candidate outputs HOLD with 0 current position -> normalized to WAIT, JEV not called
    mock_llm = MockLLM([
        {
            "candidate_action": "HOLD",
            "trend_analysis": "Neutral",
            "risk_analysis": "Neutral",
            "critic_analysis": "Nothing to do",
            "confidence": 0.5,
            "reason": "Hold stance",
        }
    ])
    mock_jev = MockJEV()
    pipeline = DualModelPipeline(llm_provider=mock_llm, jev_provider=mock_jev)

    context = _make_context(positions={})
    intent, meta = pipeline.run(context)

    assert intent.action == TradeAction.WAIT
    assert meta["jev_called"] is False
    assert len(mock_jev.call_history) == 0


def test_dual_model_pipeline_buy_jev_downgrade_on_failure():
    # Candidate outputs BUY, but JEV fails -> must downgrade to WAIT to protect capital
    mock_llm = MockLLM([
        {
            "candidate_action": "BUY",
            "trend_analysis": "Bullish momentum",
            "risk_analysis": "Acceptable drawdown",
            "critic_analysis": "High probability continuation",
            "confidence": 0.75,
            "reason": "Breakout above VWAP",
        }
    ])
    mock_jev = MockJEV(status="error")  # JEV error
    pipeline = DualModelPipeline(llm_provider=mock_llm, jev_provider=mock_jev)

    context = _make_context()
    intent, meta = pipeline.run(context)

    assert meta["jev_called"] is True
    assert intent.action == TradeAction.WAIT
    assert "JEV verification failed or unavailable" in intent.reason
    assert len(mock_llm.call_history) == 1  # Final LLM not called because JEV failed


def test_dual_model_pipeline_buy_full_success():
    # Candidate outputs BUY -> JEV verifies -> Final LLM confirms BUY
    candidate_resp = {
        "candidate_action": "BUY",
        "trend_analysis": "Strong uptrend VWAP retest",
        "risk_analysis": "0.5% stop loss sufficient",
        "critic_analysis": "Volume expansion confirms move",
        "confidence": 0.8,
        "reason": "Breakout confirmed",
    }
    final_resp = {
        "action": "BUY",
        "target_weight": "0.30",
        "holding_minutes": 10,
        "exit_conditions": [
            {"kind": "stop_loss", "reference": "3.968", "description": "0.8% stop loss"},
            {"kind": "take_profit", "reference": "4.050", "description": "target profit"},
        ],
        "confidence": 0.82,
        "reason": "Both candidate and JEV support long entry",
    }
    mock_llm = MockLLM([candidate_resp, final_resp])
    mock_jev = MockJEV(status="ok")
    pipeline = DualModelPipeline(llm_provider=mock_llm, jev_provider=mock_jev)

    context = _make_context()
    intent, meta = pipeline.run(context)
    print("META DEBUG:", meta)
    assert meta["jev_called"] is True
    assert meta.get("jev_batch_status") == "ok"
    assert len(mock_jev.call_history) == 1
    assert mock_jev.call_history[0]["state"]["trend_analysis"] == "Strong uptrend VWAP retest"
    assert len(mock_llm.call_history) == 2  # Candidate + Final

    assert intent.action == TradeAction.BUY
    assert intent.target_weight == Decimal("0.30")
    assert intent.holding_horizon_seconds == 600
    assert intent.model == "deepseek-flash"
    assert intent.prompt_id.startswith("t0_final:flash")


def test_dual_model_pipeline_sell_full_success():
    # Candidate outputs SELL on held position -> JEV verifies -> Final LLM confirms SELL
    candidate_resp = {
        "candidate_action": "SELL",
        "trend_analysis": "Bearish reversal below VWAP",
        "risk_analysis": "Downside risk high",
        "critic_analysis": "Momentum fading rapidly",
        "confidence": 0.85,
        "reason": "Exiting position",
    }
    final_resp = {
        "action": "SELL",
        "target_weight": "0.00",
        "holding_minutes": 0,
        "exit_conditions": [],
        "confidence": 0.90,
        "reason": "Confirmed exit by JEV review",
    }
    mock_llm = MockLLM([candidate_resp, final_resp])
    mock_jev = MockJEV(status="ok")
    pipeline = DualModelPipeline(llm_provider=mock_llm, jev_provider=mock_jev)

    context = _make_context(positions={"510300.SH": {"weight": "0.30", "quantity": "100"}})
    intent, meta = pipeline.run(context)

    assert meta["jev_called"] is True
    assert intent.action == TradeAction.SELL
    assert intent.target_weight == Decimal("0")
    assert len(mock_llm.call_history) == 2
