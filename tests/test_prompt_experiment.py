"""Tests for TASK-59: Prompt experiment comparison runner."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest

from trademind.domain.base import new_id, utcnow
from trademind.domain.context import ContextSnapshot, FeatureVector, PortfolioView
from trademind.domain.enums import ConfidenceSemantics, ProviderKind, TradeAction
from trademind.domain.judgments import Judgment, JudgmentBatch
from trademind.evaluation.prompt_experiment import (
    PromptComparisonItem,
    PromptExperimentReport,
    compare_prompt_versions,
)
from trademind.models.base import LLMProvider, ModelOutput, build_call


class MockLLM(LLMProvider):
    kind = ProviderKind.DEEPSEEK
    model = "deepseek-flash"
    capability_verified = True

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.call_history: list[dict] = []

    def complete(self, *, role: str, system: str, user: str, trace_id: str, timeout_s: float = 60.0) -> ModelOutput:
        idx = len(self.call_history) % len(self.responses)
        resp_data = self.responses[idx]
        self.call_history.append({"system": system, "user": user, "role": role, "trace_id": trace_id})
        call = build_call(role=role, provider=self.kind, model=self.model, system=system, user=user, trace_id=trace_id)
        call.status = "ok"
        return ModelOutput(
            text=json.dumps(resp_data),
            parsed=resp_data,
            call=call,
        )


class MockJEV:
    def __init__(self, status: str = "ok"):
        self.status = status
        self.call_history: list[dict] = []

    def judge(self, context_id: str, thesis_id: str, instrument_id: str, as_of: datetime, questions: list[dict], trace_id: str, state: dict):
        self.call_history.append({"context_id": context_id})
        judgments = [
            Judgment(
                question_id=q["id"],
                value="yes",
                probability=Decimal("0.85"),
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
            latency_ms=100,
            status=self.status,
            as_of=as_of,
        )


def _make_context(cid: str = "ctx-1"):
    now = datetime(2026, 10, 8, 9, 35, tzinfo=timezone.utc)
    tid = new_id("tr")
    return ContextSnapshot(
        id=cid,
        trace_id=tid,
        created_at=now,
        context_id=cid,
        as_of=now,
        expires_at=now + timedelta(minutes=5),
        instrument_id="513050.SH",
        universe_version="v1",
        features=FeatureVector(
            id=new_id("fv"),
            trace_id=tid,
            created_at=now,
            feature_version="feat-v1",
            instrument_id="513050.SH",
            as_of=now,
            window={"regime": "UPTREND"},
            values={"ret_1m": Decimal("0.002"), "volume_ratio": Decimal("1.8"), "vwap_dist": Decimal("0.001"), "atr_14": Decimal("0.008")},
        ),
        portfolio=PortfolioView(
            id=new_id("pv"),
            trace_id=tid,
            created_at=now,
            portfolio_version=1,
            account_id="paper-t0",
            as_of=now,
            currency="CNY",
            cash=Decimal("500"),
            equity=Decimal("500"),
            positions={},
        ),
        evidence_ids=["ev-1"],
        aux={"minute_bars": [{"open": "1.0", "close": "1.01", "volume": 1000}]},
    )


def test_prompt_experiment_matching_and_divergence():
    # 1st context: v1 and v2 both BUY
    # 2nd context: v1 BUY, v2 WAIT (divergence)
    cand_buy = {
        "candidate_action": "BUY",
        "trend_analysis": "Uptrend",
        "risk_analysis": "Safe",
        "critic_analysis": "Good volume",
        "confidence": 0.8,
        "reason": "Breakout",
    }
    final_buy = {
        "action": "BUY",
        "target_weight": "0.20",
        "holding_minutes": 15,
        "exit_conditions": [{"kind": "stop_loss", "reference": "0.99"}],
        "confidence": 0.85,
        "reason": "Confirmed long",
    }
    cand_wait = {
        "candidate_action": "WAIT",
        "trend_analysis": "Consolidation",
        "risk_analysis": "High volatility",
        "critic_analysis": "No volume",
        "confidence": 0.6,
        "reason": "Wait for clear signal",
    }

    # For ctx1: pipe_v1 calls cand_buy + final_buy, pipe_v2 calls cand_buy + final_buy (match)
    mock_llm_match = MockLLM([cand_buy, final_buy, cand_buy, final_buy])
    mock_jev = MockJEV()
    ctx1 = _make_context("ctx-match")

    report1 = compare_prompt_versions([ctx1], llm=mock_llm_match, jev=mock_jev)
    assert report1.total_snapshots == 1
    assert report1.action_match_count == 1
    assert report1.action_divergence_count == 0
    assert report1.items[0].v1_action == "BUY"
    assert report1.items[0].v2_action == "BUY"
    assert report1.items[0].v1_holding_minutes == 15
    assert report1.items[0].jev_called_v1 is True
    assert report1.items[0].jev_called_v2 is True


def test_prompt_experiment_report_serialization():
    mock_llm = MockLLM([
        # v1: candidate WAIT
        {"candidate_action": "WAIT", "reason": "v1 wait"},
        # v2: candidate WAIT
        {"candidate_action": "WAIT", "reason": "v2 wait"},
    ])
    ctx = _make_context("ctx-wait")
    report = compare_prompt_versions([ctx], llm=mock_llm, jev=None, run_id="run-test-01")

    assert report.total_snapshots == 1
    assert report.action_match_count == 1
    assert report.action_divergence_count == 0

    d = report.to_dict()
    assert d["run_id"] == "run-test-01"
    assert d["baseline_version"] == "v1"
    assert d["experiment_version"] == "v2"
    assert len(d["items"]) == 1
    item = d["items"][0]
    assert item["context_id"] == "ctx-wait"
    assert item["v1_action"] == "WAIT"
    assert item["v2_action"] == "WAIT"
    assert item["jev_called_v1"] is False
    assert item["jev_called_v2"] is False
