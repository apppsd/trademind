"""Deterministic per-round stock candidate selector (ARCH-028 §4).

Task27-05. Priority is frozen by the design record: held positions first,
then decision-layer candidates by ascending instrument id. At most one
candidate is offered per call — the pipeline's global evaluation cooldown
(``fast-eval:<account>``) independently enforces the "≤1 model candidate
per round" contract; this selector only decides *which* stock is offered.

Pure function of (positions, candidates, cooldown state, now): same inputs
→ same output, every offered/skipped stock carries a machine-readable
reason, so a round's selection is recomputable from the persisted record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StockSelection:
    """Outcome of one selection round."""

    instrument_id: str | None
    reason: str  # position_first | candidate_rank | none_cooldown | none_empty
    ranked: tuple[tuple[str, str], ...]  # (instrument_id, per-stock reason) in scan order

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "reason": self.reason,
            "ranked": [list(pair) for pair in self.ranked],
        }


class StockCandidateSelector:
    """Positions first, then affordable candidates, cooldown-aware."""

    def __init__(self, instrument_id_for: Any):
        """``instrument_id_for(code, board) -> 'code.SU'`` injected so the
        selector stays decoupled from board conventions."""
        self._instrument_id_for = instrument_id_for

    def select(
        self,
        held_instrument_ids: list[str],
        candidates: list[dict[str, Any]],  # affordable tradability records: code/board/...
        cooldown_until: dict[str, datetime],  # instrument_id → exclusive lower bound
        now: datetime,
    ) -> StockSelection:
        held = {h for h in held_instrument_ids if h}
        ranked: list[tuple[str, str]] = []
        first_free: str | None = None
        for cand in sorted(candidates, key=lambda c: (c.get("board", ""), c.get("code", ""))):
            try:
                iid = cand.get("instrument_id") or self._instrument_id_for(cand["code"], cand["board"])
            except (KeyError, ValueError) as exc:
                ranked.append((str(cand.get("code", "?")), f"skipped: {exc}"))
                continue
            if iid in held:
                ranked.append((iid, "position_first"))
                return StockSelection(iid, "position_first", tuple(ranked))
            until = cooldown_until.get(iid)
            if until is not None and now < until:
                ranked.append((iid, "cooldown"))
                continue
            ranked.append((iid, "candidate_rank"))
            if first_free is None:
                first_free = iid
        if first_free is not None:
            return StockSelection(first_free, "candidate_rank", tuple(ranked))
        if any(reason == "cooldown" for _, reason in ranked):
            return StockSelection(None, "none_cooldown", tuple(ranked))
        return StockSelection(None, "none_empty", tuple(ranked))


__all__ = ["StockSelection", "StockCandidateSelector"]
