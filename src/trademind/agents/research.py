"""Research pipeline: analysts → bull/bear debate → research manager Thesis.

Boundaries (TASK-12): every fact cites visible evidence; debate rounds are
fixed and bounded; the manager outputs a Thesis, never an order; disabled
capabilities (no news/macro data) are stated, not invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..context.builder import ContextBuilder
from ..domain.base import new_id, utcnow
from ..domain.context import ContextSnapshot
from ..domain.enums import ConfidenceSemantics, Direction, ProviderKind
from ..domain.research import AnalystReport, DebateTurn, Fact, TradeThesis
from ..errors import ProviderError
from ..models import LLMProvider
from ..storage import Repository
from .prompts import PROMPT_REGISTRY

ANALYST_ROLES = (
    "fundamental_analyst",
    "news_analyst",
    "technical_analyst",
    "macro_analyst",
    "flow_analyst",
)


def _context_brief(snap: ContextSnapshot) -> str:
    fv = snap.features
    lines = [
        f"instrument: {snap.instrument_id}",
        f"as_of: {snap.as_of.isoformat()}",
        "features(json): " + _compact({k: str(v) for k, v in fv.values.items()}),
    ]
    if fv.missing_fields:
        lines.append("missing_fields: " + _compact(fv.missing_fields))
    aux = snap.aux
    lines.append("recent_bars: " + _compact(aux.get("last_bars", [])))
    news = aux.get("news", [])
    if news:
        lines.append("news(visible): " + _compact(news))
    else:
        lines.append("news(visible): [] — 无可见新闻数据，如实说明缺失")
    funds = aux.get("fundamentals", [])
    if funds:
        lines.append("fundamentals(visible): " + _compact(funds))
    else:
        lines.append("fundamentals(visible): [] — 数据缺失")
    if snap.portfolio is not None:
        lines.append(f"portfolio_version: {snap.portfolio.portfolio_version}")
    lines.append("evidence_ids: " + ",".join(snap.evidence_ids[:8]))
    return "\n".join(lines)


def _compact(obj: Any, limit: int = 1500) -> str:
    import json

    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…[truncated]"


class ResearchPipeline:
    def __init__(self, llm: LLMProvider, repo: Repository,
                 max_debate_rounds: int = 1) -> None:
        self.llm = llm
        self.repo = repo
        self.max_debate_rounds = max_debate_rounds

    def run(self, snap: ContextSnapshot, trace_id: str,
            now: datetime | None = None) -> TradeThesis:
        now = now or utcnow()
        reports = self._analysts(snap, trace_id)
        debate = self._debate(reports, snap, trace_id)
        thesis = self._synthesize(reports, debate, snap, trace_id, now)
        return thesis

    # -- stages ---------------------------------------------------------------
    def _analysts(self, snap: ContextSnapshot, trace_id: str) -> list[AnalystReport]:
        brief = _context_brief(snap)
        out: list[AnalystReport] = []
        for role in ANALYST_ROLES:
            try:
                mout = self.llm.complete(role=f"analyst:{role}",
                                         system=PROMPT_REGISTRY[f"analyst:{role}"],
                                         user=brief, trace_id=trace_id)
            except ProviderError:
                continue  # role failure degrades coverage, recorded in call log
            parsed = mout.parsed or {}
            facts = [
                Fact(statement=str(f.get("statement", "")),
                     evidence_id=str(f.get("evidence_id", snap.evidence_ids[0] if snap.evidence_ids else "ev-missing")),
                     kind=f.get("kind", "interpretation"))
                for f in parsed.get("facts", []) if isinstance(f, dict)
            ]
            report = AnalystReport(
                id=new_id("rep"), trace_id=trace_id, created_at=utcnow(),
                role=role,  # type: ignore[arg-type]
                context_id=snap.context_id, instrument_id=snap.instrument_id,
                evidence_ids=list(snap.evidence_ids),
                facts=facts,
                analysis=str(parsed.get("analysis", ""))[:2000],
                unknowns=[str(x) for x in parsed.get("unknowns", [])][:10],
                risks=[str(x) for x in parsed.get("risks", [])][:10],
                is_stub=self.llm.kind == ProviderKind.STUB,
                model=self.llm.model,
                prompt_id=f"analyst:{role}",
            )
            self.repo.save_report(report)
            out.append(report)
        return out

    def _debate(self, reports: list[AnalystReport], snap: ContextSnapshot,
                trace_id: str) -> list[DebateTurn]:
        turns: list[DebateTurn] = []
        digest = _compact([{"report_id": r.id, "role": r.role,
                            "facts": [{"statement": f.statement} for f in r.facts],
                            "analysis": r.analysis} for r in reports], 2500)
        for round_no in range(1, self.max_debate_rounds + 1):
            for role, prompt_key in (("bull", "bull"), ("bear", "bear")):
                try:
                    mout = self.llm.complete(
                        role=prompt_key, system=PROMPT_REGISTRY[prompt_key],
                        user=f"round {round_no}\nreports:\n{digest}", trace_id=trace_id)
                except ProviderError:
                    continue
                parsed = mout.parsed or {}
                turns.append(DebateTurn(
                    role=role, round_no=round_no,  # type: ignore[arg-type]
                    argument=str(parsed.get("argument", ""))[:2000],
                    cites_report_ids=[str(x) for x in parsed.get("cites_report_ids", [])][:10],
                ))
        return turns

    def _synthesize(self, reports: list[AnalystReport], debate: list[DebateTurn],
                    snap: ContextSnapshot, trace_id: str,
                    now: datetime) -> TradeThesis:
        digest = _compact({
            "reports": [{"report_id": r.id, "role": r.role, "analysis": r.analysis,
                         "risks": r.risks} for r in reports],
            "debate": [{"role": t.role, "argument": t.argument} for t in debate],
        }, 3000)
        mout = self.llm.complete(role="research_manager",
                                 system=PROMPT_REGISTRY["research_manager"],
                                 user=f"as_of: {snap.as_of.isoformat()}\n{digest}",
                                 trace_id=trace_id)
        parsed = mout.parsed or {}
        horizon = int(parsed.get("horizon_seconds") or 5 * 86400)
        thesis = TradeThesis(
            id=new_id("th"), trace_id=trace_id, created_at=utcnow(),
            thesis_id=new_id("th"), instrument_id=snap.instrument_id,
            context_id=snap.context_id,
            direction=parsed.get("direction", "NEUTRAL"),  # type: ignore[arg-type]
            horizon_seconds=horizon,
            confidence=str(parsed.get("confidence", "0.5")),
            confidence_semantics=ConfidenceSemantics(
                parsed.get("confidence_semantics", "evidence_support")),
            evidence_ids=list(snap.evidence_ids),
            supporting_reasons=[str(x) for x in parsed.get("supporting_reasons", [])][:10],
            counterarguments=[str(x) for x in parsed.get("counterarguments", [])][:10],
            invalidations=[str(x) for x in parsed.get("invalidations", [])][:10],
            available_at=now, valid_until=now + __import__("datetime").timedelta(seconds=horizon),
            is_stub=self.llm.kind == ProviderKind.STUB,
            debate=debate, model=self.llm.model, prompt_id="research_manager",
        )
        self.repo.save_thesis(thesis)
        return thesis
