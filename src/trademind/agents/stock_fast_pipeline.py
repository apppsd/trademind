"""Stock-chain fast decision pipeline (ARCH-028 / Task27-05).

Thin specialization of :class:`FastDecisionPipeline` — every gate, the
sizer, Guard/OMS semantics, the budget and the strategy-5 exit contract
are inherited unchanged. The stock chain differs only in:

- profiles resolve per A-share board through ``stock_profiles`` (T+1,
  lot 100, ChiNext ±20% included) instead of the ETF ``get_profile``;
- the T+0-only whitelist gate is lifted (``allow_t_plus_one=True``) —
  T+1 SELL availability itself is enforced downstream by the ledger's
  available-quantity semantics and the fast-state action gates, which
  already reject selling more than is sellable;
- caps come from the stock account config (ARCH-028 proposal
  1000/450/900/100, frozen by Task27-05) — the ETF account's frozen
  200/300/100 caps are untouched.

Entry cutoff (14:50), evaluation cooldown, per-round ≤1 model candidate
(global ``fast-eval`` state key), persistent budget reservation and
WAIT-on-anything-unclear semantics are all inherited as-is. Exit-side
program exits (deterministic stop-loss / day-loss / max-holding-period
with the T+1 next-day reachability) remain the runner's risk_state /
guard responsibilities and are wired at deployment (Task06).
"""
from __future__ import annotations

from .fast_pipeline import FastDecisionPipeline
from ..market.stock_profiles import stock_profile_for_instrument


class StockFastPipeline(FastDecisionPipeline):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("profile_resolver", stock_profile_for_instrument)
        kwargs.setdefault("allow_t_plus_one", True)
        super().__init__(*args, **kwargs)


__all__ = ["StockFastPipeline"]
