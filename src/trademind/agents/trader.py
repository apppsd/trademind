"""JEV judgment compilation + Trader intent (TASK-13).

The JEV question set is compiled from the latest state and a valid thesis.
JEV probabilities are advisory inputs, never order authorization. Stale
theses never enter the fast path.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot
from ..domain.enums import ConfidenceSemantics, ProviderKind, TradeAction
from ..domain.intents import ExitCondition, TradeIntent
from ..domain.judgments import KNOWN_QUESTIONS, JudgmentBatch
from ..domain.research import TradeThesis
from ..errors import ProviderError, StaleThesis
from ..models import JEVProvider, LLMProvider
from ..storage import Repository
from .prompts import PROMPT_REGISTRY

OPEN_QUESTIONS = ("thesis_supported", "entry_suitable", "continuation",
                  "breakout_quality", "liquidity_healthy", "adverse_move",
                  "evidence_sufficient")
HOLDING_QUESTIONS = ("thesis_supported", "exit_supported", "adverse_move",
                     "evidence_sufficient")
QUESTION_TEXT = {
    "thesis_supported": "Do the visible facts support the thesis and its invalidations?",
    "entry_suitable": "Do the visible facts support entering at the current price and liquidity?",
    "continuation": "Does the visible trend support continuation during the thesis horizon?",
    "breakout_quality": "Is the visible breakout supported by price and volume rather than a false breakout?",
    "liquidity_healthy": "Is visible liquidity adequate for the proposed position?",
    "adverse_move": "Do visible facts indicate an adverse move during the thesis horizon?",
    "exit_supported": "Do visible facts or thesis invalidations support exiting the holding?",
    "evidence_sufficient": "Is visible evidence sufficient to answer the requested judgments?",
}


class JEVGate:
    """Compiles questions, calls the JEV provider, stores the batch."""

    def __init__(self, jev: JEVProvider, repo: Repository,
                 question_version: str = "qv-2026-10-07") -> None:
        self.jev = jev
        self.repo = repo
        self.question_version = question_version

    def ask(self, snap: ContextSnapshot, thesis: TradeThesis | None,
            holding: bool, trace_id: str,
            now: datetime | None = None) -> JudgmentBatch:
        now = now or utcnow()
        if thesis is not None and not thesis.is_valid_at(now):
            raise StaleThesis(f"thesis {thesis.thesis_id} expired")
        qids = HOLDING_QUESTIONS if holding else OPEN_QUESTIONS
        questions = [{"question_id": q, "text": QUESTION_TEXT[q],
                      "horizon_seconds": thesis.horizon_seconds if thesis else None,
                      "answer_schema": {"value": ["yes", "no", "unsure"],
                                        "probability_semantics": "self_reported_answer_confidence"},
                      "on_missing_evidence": "return missing_reason; never invent facts"}
                     for q in qids if q in KNOWN_QUESTIONS]
        batch = self.jev.judge(
            context_id=snap.context_id,
            thesis_id=thesis.thesis_id if thesis else None,
            instrument_id=snap.instrument_id, as_of=snap.as_of,
            questions=questions, trace_id=trace_id,
            state=snap.model_dump(mode="json"), thesis=thesis.model_dump(mode="json") if thesis else None,
        )
        returned = [j.question_id for j in batch.judgments]
        if (batch.status != "ok" or len(returned) != len(set(returned)) or set(returned) != set(qids)
                or batch.context_id != snap.context_id or batch.thesis_id != (thesis.thesis_id if thesis else None)
                or batch.instrument_id != snap.instrument_id or batch.as_of != snap.as_of
                or any(j.missing_reason for j in batch.judgments)):
            raise ProviderError("JEV batch incomplete, refused or mismatched")
        self.repo.save_judgments(batch)
        return batch


def _judgment_brief(batch: JudgmentBatch) -> str:
    import json

    return json.dumps([
        {"question_id": j.question_id, "value": j.value,
         "probability": str(j.probability) if j.probability is not None else None,
         "semantics": j.probability_semantics.value}
        for j in batch.judgments
    ], ensure_ascii=False)


class Trader:
    """Produces the TradeIntent. Stale context/judgments are refused."""

    def __init__(self, llm: LLMProvider, repo: Repository,
                 context_ttl_seconds: int = 900) -> None:
        self.llm = llm
        self.repo = repo
        self.context_ttl_seconds = context_ttl_seconds

    def decide(self, snap: ContextSnapshot, thesis: TradeThesis,
               batch: JudgmentBatch, trace_id: str,
               now: datetime | None = None) -> TradeIntent:
        now = now or utcnow()
        if not thesis.is_valid_at(now):
            raise StaleThesis(thesis.thesis_id)
        if batch.context_id != snap.context_id:
            raise ProviderError("judgment batch is from a different context")

        holding = False
        current_weight = Decimal("0")
        if snap.portfolio is not None:
            pos = snap.portfolio.positions.get(snap.instrument_id)
            if pos:
                holding = True
                current_weight = Decimal(pos["weight"])
        intent_hint = "已持仓，评估 HOLD/SELL" if holding else "未持仓，评估 BUY/HOLD"
        user = (
            f"instrument: {snap.instrument_id}\nas_of: {snap.as_of.isoformat()}\n"
            f"thesis: {thesis.direction} conf={thesis.confidence} "
            f"({thesis.confidence_semantics.value}) valid_until={thesis.valid_until.isoformat()}\n"
            f"支持: {thesis.supporting_reasons}\n反对: {thesis.counterarguments}\n"
            f"失效条件: {thesis.invalidations}\n"
            f"JEV: {_judgment_brief(batch)}\n"
            f"portfolio: current_weight={current_weight}, holding={holding}\n{intent_hint}\n"
            f"features: {dict(snap.features.values)}"
        )
        mout = self.llm.complete(role="trader", system=PROMPT_REGISTRY["trader"],
                                 user=user, trace_id=trace_id)
        parsed = mout.parsed
        if parsed is None:
            raise ProviderError("trader output not parseable; no intent produced")
        action = parsed.get("action", "HOLD")
        target = parsed.get("target_weight")
        entry = "exit_review" if (holding and action == "SELL") else (
            "increase" if (holding and action == "BUY") else (
                "open" if action == "BUY" else "reduce" if action == "SELL" else "reduce"))
        intent = TradeIntent(
            id=new_id("ti"), trace_id=trace_id, created_at=utcnow(),
            decision_id=new_id("dec"), context_id=snap.context_id,
            thesis_id=thesis.thesis_id, instrument_id=snap.instrument_id,
            portfolio_version=snap.portfolio.portfolio_version if snap.portfolio else 0,
            account_id=snap.portfolio.account_id if snap.portfolio else "paper-001",
            action=action,  # type: ignore[arg-type]
            target_weight=Decimal(str(target)) if target is not None else None,
            current_weight=current_weight,
            confidence=str(parsed.get("confidence", thesis.confidence)),
            confidence_semantics=ConfidenceSemantics(
                parsed.get("confidence_semantics", "evidence_support")),
            holding_horizon_seconds=int(parsed.get("holding_horizon_seconds") or thesis.horizon_seconds),
            evidence_ids=list(snap.evidence_ids)[:12],
            reason=str(parsed.get("reason", ""))[:1500],
            exit_conditions=[
                ExitCondition(description=str(c.get("description", "")),
                              kind=c.get("kind", "other"),
                              reference=str(c["reference"]) if c.get("reference") is not None else None)
                for c in parsed.get("exit_conditions", []) if isinstance(c, dict)
            ][:8],
            valid_until=now + __import__("datetime").timedelta(seconds=self.context_ttl_seconds),
            entry_type=entry,  # type: ignore[arg-type]
            is_stub=self.llm.kind == ProviderKind.STUB,
            model=self.llm.model, prompt_id="trader",
        )
        problems = intent.check_against_account()
        if problems:
            #非法方向：降级为 HOLD 意图并记录，绝不放大风险
            intent = intent.model_copy(update={
                "action": TradeAction.HOLD, "target_weight": current_weight,
                "reason": "[downgraded] illegal direction: " + "; ".join(problems),
            })
        self.repo.save_intent(intent, status="CREATED")
        return intent
