"""Prompt experiment comparison runner (TASK-59).

Compares baseline vs experimental versioned prompts on identical point-in-time
market context snapshots without mutating production accounts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..agents.pipeline import DualModelPipeline
from ..domain.context import ContextSnapshot
from ..models.base import LLMProvider
from ..models.jev import TypeSafeJEVProvider


@dataclass
class PromptComparisonItem:
    context_id: str
    instrument_id: str
    as_of: str
    v1_action: str
    v2_action: str
    v1_confidence: str
    v2_confidence: str
    v1_target_weight: str | None
    v2_target_weight: str | None
    v1_holding_minutes: int
    v2_holding_minutes: int
    v1_reason: str
    v2_reason: str
    jev_called_v1: bool
    jev_called_v2: bool


@dataclass
class PromptExperimentReport:
    run_id: str
    created_at: str
    baseline_version: str
    experiment_version: str
    total_snapshots: int
    action_match_count: int
    action_divergence_count: int
    items: list[PromptComparisonItem] = field(default_factory=list)
    simulation_status: str = 'intent_only_missing_recorded_minutes'
    paper_accounts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "baseline_version": self.baseline_version,
            "experiment_version": self.experiment_version,
            "total_snapshots": self.total_snapshots,
            "action_match_count": self.action_match_count,
            "action_divergence_count": self.action_divergence_count,
            "items": [
                {
                    "context_id": item.context_id,
                    "instrument_id": item.instrument_id,
                    "as_of": item.as_of,
                    "v1_action": item.v1_action,
                    "v2_action": item.v2_action,
                    "v1_confidence": item.v1_confidence,
                    "v2_confidence": item.v2_confidence,
                    "v1_target_weight": item.v1_target_weight,
                    "v2_target_weight": item.v2_target_weight,
                    "v1_holding_minutes": item.v1_holding_minutes,
                    "v2_holding_minutes": item.v2_holding_minutes,
                    "v1_reason": item.v1_reason,
                    "v2_reason": item.v2_reason,
                    "jev_called_v1": item.jev_called_v1,
                    "jev_called_v2": item.jev_called_v2,
                }
                for item in self.items
            ],
            'simulation_status': self.simulation_status,
            'paper_accounts': self.paper_accounts,
        }


def compare_prompt_versions(
    contexts: list[ContextSnapshot],
    llm: LLMProvider,
    jev: TypeSafeJEVProvider | None = None,
    run_id: str = "run-exp-v1-v2",
) -> PromptExperimentReport:
    """Run baseline v1 and experiment v2 on identical context snapshots and compare results."""
    from ..models.deepseek import DeepSeekProvider
    from ..models.daily_budget import DailyModelBudget
    if isinstance(llm, DeepSeekProvider) and not isinstance(llm.budget, DailyModelBudget):
        raise ValueError('Paid T0 prompt experiments require a persistent DailyModelBudget')
    pipe_v1 = DualModelPipeline(llm_provider=llm, jev_provider=jev, prompt_version="v1")
    pipe_v2 = DualModelPipeline(llm_provider=llm, jev_provider=jev, prompt_version="v2")
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from .prompt_paper import recorded_bars, PromptPaperAccount
    if contexts and any(a.as_of >= b.as_of for a, b in zip(contexts, contexts[1:])):
        raise ValueError('contexts must advance strictly in time')
    bars = recorded_bars(contexts)
    temp = TemporaryDirectory()
    paper = {}
    if contexts and bars:
        try:
            for version in ('v1', 'v2'):
                paper[version] = PromptPaperAccount(Path(temp.name)/(version+'.db'),
                                                    f'{run_id}-{version}', contexts, bars, llm, jev, version)
        except Exception:
            for account in paper.values():
                account.close()
            temp.cleanup()
            raise

    items: list[PromptComparisonItem] = []
    matches = 0
    divergences = 0

    try:
        for ctx in contexts:
            if paper:
                result_v1 = paper['v1'].step(ctx)
                result_v2 = paper['v2'].step(ctx)
                # Risk exits/cooldown/errors have no model intent; retain explicit
                # execution outcomes in the account report instead of fabricating one.
                if result_v1.intent is None or result_v2.intent is None:
                    continue
                intent_v1, intent_v2 = result_v1.intent, result_v2.intent
                meta_v1 = {'jev_called': intent_v1.thesis_id == 't0-dual-model'}
                meta_v2 = {'jev_called': intent_v2.thesis_id == 't0-dual-model'}
            else:
                intent_v1, meta_v1 = pipe_v1.run(ctx)
                intent_v2, meta_v2 = pipe_v2.run(ctx)

            if intent_v1.action == intent_v2.action:
                matches += 1
            else:
                divergences += 1

            items.append(
                PromptComparisonItem(
                    context_id=ctx.context_id,
                    instrument_id=ctx.instrument_id,
                    as_of=ctx.as_of.isoformat(),
                    v1_action=intent_v1.action.value,
                    v2_action=intent_v2.action.value,
                    v1_confidence=str(intent_v1.confidence),
                    v2_confidence=str(intent_v2.confidence),
                    v1_target_weight=str(intent_v1.target_weight) if intent_v1.target_weight is not None else None,
                    v2_target_weight=str(intent_v2.target_weight) if intent_v2.target_weight is not None else None,
                    v1_holding_minutes=intent_v1.holding_horizon_seconds // 60,
                    v2_holding_minutes=intent_v2.holding_horizon_seconds // 60,
                    v1_reason=intent_v1.reason,
                    v2_reason=intent_v2.reason,
                    jev_called_v1=meta_v1.get("jev_called", False),
                    jev_called_v2=meta_v2.get("jev_called", False),
                )
            )

        summaries = {v: account.report() for v, account in paper.items()}
    finally:
        for account in paper.values():
            account.close()
        temp.cleanup()
    return PromptExperimentReport(
        run_id=run_id,
        created_at=datetime.utcnow().isoformat(),
        baseline_version="v1",
        experiment_version="v2",
        total_snapshots=len(contexts),
        action_match_count=matches,
        action_divergence_count=divergences,
        items=items,
        simulation_status='historical_paper' if paper else 'intent_only_missing_recorded_minutes',
        paper_accounts=summaries,
    )
