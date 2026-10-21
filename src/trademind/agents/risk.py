"""AI risk review + portfolio AI (TASK-14).

REVISE produces a NEW intent version (never mutates the old object); REJECT
never reaches Guard. Neither role holds broker tools or account write access.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot
from ..domain.enums import ProviderKind, RiskVerdict, TradeAction
from ..domain.intents import PortfolioIntent, RiskReview, TradeIntent
from ..errors import ProviderError
from ..models import LLMProvider
from ..storage import Repository
from .prompts import PROMPT_REGISTRY


class AIRiskReviewer:
    def __init__(self, llm: LLMProvider, repo: Repository,
                 max_position_weight: Decimal = Decimal("0.20")) -> None:
        self.llm = llm
        self.repo = repo
        self.max_position_weight = max_position_weight

    def review(self, intent: TradeIntent, snap: ContextSnapshot,
               trace_id: str) -> RiskReview:
        positions = snap.portfolio.positions if snap.portfolio else {}
        user = (
            f"instrument: {intent.instrument_id}\n"
            f"action: {intent.action.value} target_weight={intent.target_weight} "
            f"current_weight={intent.current_weight}\n"
            f"policy max_position_weight={self.max_position_weight}\n"
            f"portfolio positions: {positions}\n"
            f"reason: {intent.reason}\nexit: {[c.description for c in intent.exit_conditions]}\n"
        )
        mout = self.llm.complete(role="risk", system=PROMPT_REGISTRY["risk"],
                                 user=user, trace_id=trace_id)
        parsed = mout.parsed
        if parsed is None:
            raise ProviderError("risk output not parseable")
        review = RiskReview(
            id=new_id("rr"), trace_id=trace_id, created_at=utcnow(),
            decision_id=intent.decision_id, intent_id=intent.id,
            verdict=parsed.get("verdict", "REJECT"),  # type: ignore[arg-type]
            risks=[str(x) for x in parsed.get("risks", [])][:10],
            proposed_weight=(Decimal(str(parsed["proposed_weight"]))
                             if parsed.get("proposed_weight") is not None else None),
            reason=str(parsed.get("reason", ""))[:1500],
            evidence_ids=list(snap.evidence_ids)[:8],
            is_stub=self.llm.kind == ProviderKind.STUB,
            model=self.llm.model, prompt_id="risk",
        )
        self.repo.save_risk_review(review)
        return review


class PortfolioAI:
    """Turns (intent, risk review) into the single Guard input intent."""

    def __init__(self, llm: LLMProvider, repo: Repository, mode: str = "paper") -> None:
        self.llm = llm
        self.repo = repo
        self.mode = mode

    def finalize(self, intent: TradeIntent, review: RiskReview,
                 snap: ContextSnapshot, trace_id: str) -> tuple[PortfolioIntent | None, TradeIntent | None]:
        """Returns (portfolio_intent, revised_intent_or_None).

        APPROVE → PortfolioIntent as-is; REVISE → new TradeIntent version +
        PortfolioIntent; REJECT → (None, None).
        """
        if review.verdict == RiskVerdict.REJECT:
            self.repo.set_intent_status(intent.decision_id, intent.intent_version, "REJECTED")
            return None, None

        final_intent = intent
        if review.verdict == RiskVerdict.REVISE:
            final_intent = intent.model_copy(update={
                "id": new_id("ti"),
                "intent_version": intent.intent_version + 1,
                "target_weight": review.proposed_weight,
                "reason": f"[revised by risk] {review.reason}"[:1500],
                "created_at": utcnow(),
            })
            problems = final_intent.check_against_account()
            if problems:
                self.repo.set_intent_status(intent.decision_id, intent.intent_version, "REJECTED")
                return None, None
            self.repo.save_intent(final_intent, status="REVIEWED")

        if final_intent.action == TradeAction.HOLD:
            self.repo.set_intent_status(final_intent.decision_id,
                                        final_intent.intent_version, "REVIEWED")
            return None, None  # nothing for Guard to check

        user = (f"action {final_intent.action.value} target {final_intent.target_weight} "
                f"current {final_intent.current_weight}; risk verdict {review.verdict.value}")
        mout = self.llm.complete(role="portfolio", system=PROMPT_REGISTRY["portfolio"],
                                 user=user, trace_id=trace_id)
        parsed = mout.parsed or {}
        action = str(parsed.get("action", final_intent.action.value))
        if action != final_intent.action.value:
            # portfolio AI disagreement → reject further progress this version
            self.repo.set_intent_status(final_intent.decision_id,
                                        final_intent.intent_version, "REJECTED")
            return None, None
        target = parsed.get("target_weight")
        pi = PortfolioIntent(
            id=new_id("pi"), trace_id=trace_id, created_at=utcnow(),
            intent_id=new_id("pi"), decision_id=final_intent.decision_id,
            account_id=final_intent.account_id,
            portfolio_version=final_intent.portfolio_version,
            instrument_id=final_intent.instrument_id,
            action=final_intent.action,  # type: ignore[arg-type]
            target_weight=final_intent.target_weight,
            current_weight=final_intent.current_weight,
            valid_until=final_intent.valid_until,
            mode=self.mode,  # type: ignore[arg-type]
            limit_price=final_intent.limit_price,
            risk_review_id=review.id,
        )
        self.repo.set_intent_status(final_intent.decision_id,
                                    final_intent.intent_version, "REVIEWED")
        return pi, (final_intent if review.verdict == RiskVerdict.REVISE else None)
