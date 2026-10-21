"""Multi-asset candidate selector with deterministic filtration and bounded budget.

Selects qualified, liquid, rule-verified candidates across multiple asset classes:
ETFs, LOFs, Convertible Bonds, Hong Kong stocks, and Futures.
Never amplifies model call budget; daily model quota is strictly preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..config import MarketAccessConfig
from .access import evaluate_eligibility
from .multi_asset import AssetQuote
from .rules import CANONICAL_RULES, MarketRule, get_rule, to_decimal


@dataclass
class CandidateSelectionResult:
    status: str  # 'success' | 'empty' | 'insufficient_candidates'
    scanned_count: int
    eligible_count: int
    selected_count: int
    top_k_per_market: int
    max_total_candidates: int
    selected_candidates: list[dict[str, Any]]
    market_breakdown: dict[str, dict[str, int]]
    filtered_out_reasons: dict[str, int]
    budget_reserved_calls: int


class MultiAssetCandidateSelector:
    """Deterministic candidate selector for multi-asset trading pools."""

    def __init__(
        self,
        cfg: MarketAccessConfig,
        top_k_per_market: int = 1,
        max_total_candidates: int = 3,
        rules_overrides: dict[str, Any] | None = None,
    ):
        self.cfg = cfg
        self.top_k_per_market = max(1, top_k_per_market)
        self.max_total_candidates = max(1, max_total_candidates)
        self.overrides = rules_overrides or {}

    def select(self, assets: list[dict[str, Any]], now: datetime | None = None) -> CandidateSelectionResult:
        now = now or datetime.now(timezone.utc)
        breakdown: dict[str, dict[str, int]] = {}
        filtered_reasons: dict[str, int] = {}
        eligible_by_market: dict[str, list[dict[str, Any]]] = {}

        for raw in assets:
            kind = raw.get("kind", "unknown")
            stats = breakdown.setdefault(kind, {"scanned": 0, "eligible": 0, "selected": 0})
            stats["scanned"] += 1

            iid = raw.get("instrument_id", "")
            rule = get_rule(iid, self.overrides)

            # Construct AssetQuote for access evaluation
            price = to_decimal(raw.get("price"))
            turnover = to_decimal(raw.get("turnover"))
            st = None
            if raw.get("source_time"):
                try:
                    st = datetime.fromisoformat(raw["source_time"])
                except Exception:
                    pass

            quote = AssetQuote(
                instrument_id=iid,
                kind=kind,
                name=raw.get("name", ""),
                currency=raw.get("currency", "CNY"),
                price=price,
                turnover=turnover,
                observed_at=now,
                source_time=st,
                endpoint=raw.get("endpoint", ""),
            )

            eval_res = evaluate_eligibility(quote, self.cfg, rule, now)

            if not eval_res["purchase_conditions_met"]:
                for reason in eval_res["blocking_reasons"]:
                    filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
                continue

            # Additional Liquidity gate: require positive turnover if reported
            if turnover is not None and turnover <= Decimal("0"):
                filtered_reasons["zero_turnover"] = filtered_reasons.get("zero_turnover", 0) + 1
                continue

            # Freshness gate: must be fresh
            if eval_res["freshness"] != "fresh":
                reason = f"quote_{eval_res['freshness']}"
                filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
                continue

            stats["eligible"] += 1
            item = dict(raw)
            item["eligibility"] = eval_res
            item["turnover_decimal"] = turnover or Decimal("0")
            eligible_by_market.setdefault(kind, []).append(item)

        # Rank within each market by liquidity (highest turnover first)
        selected: list[dict[str, Any]] = []
        for kind in self.cfg.markets:
            items = eligible_by_market.get(kind, [])
            items.sort(key=lambda x: x["turnover_decimal"], reverse=True)
            top = items[: self.top_k_per_market]
            for candidate in top:
                if len(selected) >= self.max_total_candidates:
                    break
                # Remove transient decimal helper
                candidate.pop("turnover_decimal", None)
                selected.append(candidate)
                breakdown.get(kind, {})["selected"] = breakdown.get(kind, {}).get("selected", 0) + 1
            if len(selected) >= self.max_total_candidates:
                break

        status = "success" if selected else "empty"
        total_eligible = sum(s["eligible"] for s in breakdown.values())

        return CandidateSelectionResult(
            status=status,
            scanned_count=len(assets),
            eligible_count=total_eligible,
            selected_count=len(selected),
            top_k_per_market=self.top_k_per_market,
            max_total_candidates=self.max_total_candidates,
            selected_candidates=selected,
            market_breakdown=breakdown,
            filtered_out_reasons=filtered_reasons,
            budget_reserved_calls=len(selected) * 2,  # 2 calls per candidate (flash + JEV)
        )
