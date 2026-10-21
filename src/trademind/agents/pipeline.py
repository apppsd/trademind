"""Minimal Dual-Model Decision Chain (TASK-57).

Pipeline flow:
ContextSnapshot
  ↓
deepseek-flash (Candidate analysis: Trend / Risk / Critic sections)
  ↓
[if WAIT/HOLD: short-circuit without calling JEV]
  ↓
TypeSafe JEV (POST /v1/systemone: entry_suitable / adverse_move / evidence_sufficient)
  ↓
[if JEV refuses/fails/down: downgrade to WAIT, no new risk allowed]
  ↓
deepseek-flash (Final intent synthesis: combines candidate + JEV audits)
  ↓
TradeIntent (normalized WAIT/HOLD, validated weights, exit conditions)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..config import T0ExperimentConfig
from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot
from ..domain.enums import ConfidenceSemantics, TradeAction
from ..domain.intents import ExitCondition, TradeIntent, normalize_intent_action
from ..domain.judgments import JudgmentBatch
from ..models.base import LLMProvider, ModelOutput
from ..models.jev import TypeSafeJEVProvider
from .prompts import PROMPT_REGISTRY


class DualModelPipeline:
    def __init__(
        self,
        llm_provider: LLMProvider,
        jev_provider: TypeSafeJEVProvider | None = None,
        t0_config: T0ExperimentConfig | None = None,
        account_id: str = "paper-t0",
        prompt_version: str = "v1",
    ) -> None:
        self.llm = llm_provider
        self.jev = jev_provider
        self.t0_config = t0_config
        self.account_id = account_id
        self.prompt_version = prompt_version

    def _call_llm(self, *, role: str, system: str, user: str, trace_id: str) -> ModelOutput:
        if hasattr(self.llm, "complete"):
            return self.llm.complete(role=role, system=system, user=user, trace_id=trace_id)
        if hasattr(self.llm, "chat"):
            return self.llm.chat(system=system, user=user, trace_id=trace_id, role=role)
        raise AttributeError("llm_provider has neither complete nor chat method")

    def run(
        self,
        context: ContextSnapshot,
        trace_id: str | None = None,
    ) -> tuple[TradeIntent, dict[str, Any]]:
        trace_id = trace_id or new_id("tr")
        decision_id = new_id("dec")
        current_weight = None
        if context.portfolio and context.portfolio.positions:
            pos = context.portfolio.positions.get(context.instrument_id)
            if pos:
                current_weight = Decimal(pos.get("weight", "0"))
            else:
                current_weight = Decimal("0")
        elif context.portfolio:
            current_weight = Decimal("0")

        # Step 1: deepseek-flash Candidate Analysis
        cand_key = f"t0_candidate:flash:{self.prompt_version}"
        if cand_key not in PROMPT_REGISTRY:
            cand_key = "t0_candidate:flash"
        candidate_sys = PROMPT_REGISTRY.get(cand_key, "")
        cand_user = json.dumps(
            {
                "instrument_id": context.instrument_id,
                "as_of": context.as_of.isoformat(),
                "features": {k: str(v) for k, v in context.features.values.items()},
                "regime": context.features.window.get("regime", "UNKNOWN"),
                "recent_bars": context.aux.get("minute_bars", [])[:5],
                "portfolio": {
                    "cash": str(context.portfolio.cash) if context.portfolio else "0",
                    "equity": str(context.portfolio.equity) if context.portfolio else "0",
                    "current_weight": str(current_weight or 0),
                },
            },
            ensure_ascii=False,
        )

        cand_output = self._call_llm(role="t0_candidate", system=candidate_sys, user=cand_user, trace_id=trace_id)
        cand_parsed = cand_output.parsed or {}
        cand_action_str = str(cand_parsed.get("candidate_action", "WAIT")).upper()
        try:
            cand_action = TradeAction(cand_action_str)
        except ValueError:
            cand_action = TradeAction.WAIT

        # Normalize HOLD with zero position to WAIT
        cand_action, norm_reason = normalize_intent_action(cand_action, current_weight)

        trace_meta: dict[str, Any] = {
            "candidate_output": cand_parsed,
            "normalized_candidate_action": cand_action.value,
            "jev_called": False,
        }

        # Step 2: Short-circuit for WAIT / HOLD
        if cand_action in (TradeAction.WAIT, TradeAction.HOLD):
            target_w = Decimal("0") if cand_action == TradeAction.WAIT else current_weight
            intent = TradeIntent(
                id=new_id("ti"),
                trace_id=trace_id,
                created_at=utcnow(),
                decision_id=decision_id,
                context_id=context.context_id,
                thesis_id="t0-flash-candidate",
                instrument_id=context.instrument_id,
                portfolio_version=context.portfolio.portfolio_version if context.portfolio else 1,
                account_id=self.account_id,
                action=cand_action,
                target_weight=target_w,
                current_weight=current_weight,
                confidence=Decimal(str(cand_parsed.get("confidence", "0.5"))),
                confidence_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
                holding_horizon_seconds=0 if cand_action == TradeAction.WAIT else 300,
                evidence_ids=context.evidence_ids[:3],
                reason=cand_parsed.get("reason", "no trade signal in candidate phase"),
                exit_conditions=[],
                valid_until=context.as_of + timedelta(minutes=5),
                model="deepseek-flash",
                prompt_id="t0_candidate:flash",
            )
            return intent, trace_meta

        # Step 3: TypeSafe JEV narrow question review (Mandatory for BUY / SELL)
        jev_batch: JudgmentBatch | None = None
        if self.jev is not None:
            trace_meta["jev_called"] = True
            questions = [
                {"id": "entry_suitable", "prompt": "Is entry suitable based on current indicators and trend?"},
                {"id": "adverse_move", "prompt": "Is adverse move risk acceptable within position limits?"},
                {"id": "evidence_sufficient", "prompt": "Is available evidence sufficient for the proposed action?"},
            ]
            try:
                jev_res = self.jev.judge(
                    context_id=context.context_id,
                    thesis_id="t0-flash-candidate",
                    instrument_id=context.instrument_id,
                    as_of=context.as_of,
                    questions=questions,
                    trace_id=trace_id,
                    state={
                        "candidate_action": cand_action.value,
                        "trend_analysis": cand_parsed.get("trend_analysis", ""),
                        "risk_analysis": cand_parsed.get("risk_analysis", ""),
                        "critic_analysis": cand_parsed.get("critic_analysis", ""),
                    },
                )
                if isinstance(jev_res, JudgmentBatch):
                    jev_batch = jev_res
                    trace_meta["jev_batch_status"] = jev_batch.status
            except Exception as exc:
                trace_meta["jev_error"] = str(exc)

        # If JEV is missing, refused, or failed: MUST downgrade to WAIT (No unverified risk allowed)
        if jev_batch is None or jev_batch.status != "ok":
            intent = TradeIntent(
                id=new_id("ti"),
                trace_id=trace_id,
                created_at=utcnow(),
                decision_id=decision_id,
                context_id=context.context_id,
                thesis_id="t0-flash-candidate",
                instrument_id=context.instrument_id,
                portfolio_version=context.portfolio.portfolio_version if context.portfolio else 1,
                account_id=self.account_id,
                action=TradeAction.WAIT,
                target_weight=Decimal("0"),
                current_weight=current_weight,
                confidence=Decimal("0"),
                confidence_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
                holding_horizon_seconds=0,
                evidence_ids=context.evidence_ids[:3],
                reason=f"JEV verification failed or unavailable (status: {getattr(jev_batch, 'status', 'none')}); new risk blocked.",
                exit_conditions=[],
                valid_until=context.as_of + timedelta(minutes=5),
                model="deepseek-flash",
                prompt_id="t0_candidate:flash",
            )
            return intent, trace_meta

        # Step 4: deepseek-flash Final Intent Synthesis
        final_key = f"t0_final:flash:{self.prompt_version}"
        if final_key not in PROMPT_REGISTRY:
            final_key = "t0_final:flash"
        final_sys = PROMPT_REGISTRY.get(final_key, "")
        jev_summary = [
            {
                "question_id": j.question_id,
                "value": j.value,
                "probability": str(j.probability) if j.probability is not None else None,
                "semantics": str(j.probability_semantics.value),
            }
            for j in jev_batch.judgments
        ]
        final_user = json.dumps(
            {
                "candidate": cand_parsed,
                "jev_judgments": jev_summary,
                "features": {k: str(v) for k, v in context.features.values.items()},
                "current_weight": str(current_weight or 0),
            },
            ensure_ascii=False,
        )

        final_output = self._call_llm(role="t0_final", system=final_sys, user=final_user, trace_id=trace_id)
        final_parsed = final_output.parsed or {}
        trace_meta["final_output"] = final_parsed

        final_action_str = str(final_parsed.get("action", "WAIT")).upper()
        try:
            final_action = TradeAction(final_action_str)
        except ValueError:
            final_action = TradeAction.WAIT

        final_action, _ = normalize_intent_action(final_action, current_weight)

        tgt_w = None
        if final_action == TradeAction.BUY:
            raw_w = final_parsed.get("target_weight") or "0.20"
            tgt_w = Decimal(str(raw_w))
            if current_weight is not None and tgt_w <= current_weight:
                tgt_w = current_weight + Decimal("0.10")
        elif final_action == TradeAction.SELL:
            tgt_w = Decimal("0")
        elif final_action in (TradeAction.HOLD, TradeAction.WAIT):
            tgt_w = Decimal("0") if final_action == TradeAction.WAIT else current_weight

        exit_conds = []
        for ec in final_parsed.get("exit_conditions", []):
            exit_conds.append(
                ExitCondition(
                    description=ec.get("description", "standard exit"),
                    kind=ec.get("kind", "stop_loss"),
                    reference=Decimal(str(ec["reference"])) if ec.get("reference") is not None else None,
                )
            )

        holding_sec = int(final_parsed.get("holding_minutes", 15)) * 60
        if final_action == TradeAction.WAIT:
            holding_sec = 0

        intent = TradeIntent(
            id=new_id("ti"),
            trace_id=trace_id,
            created_at=utcnow(),
            decision_id=decision_id,
            context_id=context.context_id,
            thesis_id="t0-dual-model",
            instrument_id=context.instrument_id,
            portfolio_version=context.portfolio.portfolio_version if context.portfolio else 1,
            account_id=self.account_id,
            action=final_action,
            target_weight=tgt_w,
            current_weight=current_weight,
            confidence=Decimal(str(final_parsed.get("confidence", "0.7"))),
            confidence_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
            holding_horizon_seconds=max(0, holding_sec) if final_action != TradeAction.BUY else max(60, holding_sec),
            evidence_ids=context.evidence_ids[:3],
            reason=final_parsed.get("reason", "Dual-model JEV + deepseek-flash approved intent"),
            exit_conditions=exit_conds,
            valid_until=context.as_of + timedelta(minutes=5),
            model="deepseek-flash",
            prompt_id=final_key,
        )
        return intent, trace_meta
