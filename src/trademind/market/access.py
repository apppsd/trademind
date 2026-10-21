"""Account eligibility and instrument qualification checker for multi-asset trading.

Validates explicit account permissions, instrument-level rules, suspension/redemption status,
pricing, liquidity, currency separation, and cash/margin availability.
No permission is ever assumed from quote downloads or code formats.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..config import MarketAccessConfig
from .rules import MarketRule, get_rule, to_decimal

# Permission mapping for market kinds and categories
REQUIRED_PERMISSIONS: dict[str, str] = {
    "etf": "cn_fund",
    "lof": "cn_fund",
    "convertible_bond": "convertible_bond",
    "hk_stock": "hk_stock",
    "futures": "futures",
}


def check_permission(kind: str, rule: MarketRule | None, confirmed: list[str]) -> tuple[bool, str]:
    """Check if the account holds the required permission for the asset.
    
    Returns (has_permission, required_permission_name).
    Supports fine-grained permissions (hk_stock_direct, hk_stock_connect,
    futures_special, futures_financial) while remaining backward compatible
    with generic permissions (hk_stock, futures).
    """
    confirmed_set = set(confirmed)
    base_perm = REQUIRED_PERMISSIONS.get(kind, kind)

    if kind == "hk_stock":
        # Check fine-grained HK permissions
        is_connect = rule.hk_connect_eligible if rule else False
        if "hk_stock" in confirmed_set or "hk_stock_direct" in confirmed_set:
            return True, "hk_stock"
        if is_connect and "hk_stock_connect" in confirmed_set:
            return True, "hk_stock_connect"
        if not is_connect and "hk_stock_connect" in confirmed_set:
            # Held connect permission, but stock is not in connect list
            return False, "hk_stock_direct"
        return False, "hk_stock"

    if kind == "futures":
        cat = rule.futures_category if rule else "standard_commodity"
        if cat == "special_commodity":
            if "futures_special" in confirmed_set:
                return True, "futures_special"
            return False, "futures_special"
        if cat == "financial":
            if "futures_financial" in confirmed_set:
                return True, "futures_financial"
            return False, "futures_financial"
        # Standard commodity
        if "futures" in confirmed_set or "futures_standard" in confirmed_set:
            return True, "futures"
        return False, "futures"

    # Standard mainland fund or convertible bond
    has_perm = base_perm in confirmed_set
    return has_perm, base_perm


def evaluate_eligibility(
    quote: Any,
    cfg: MarketAccessConfig,
    rule: MarketRule | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate whether an observed quote meets all account and rule purchase conditions.
    
    Performs deterministic qualification checks with independent blocking reasons.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    # 1. Account Permission check
    has_perm, req_perm = check_permission(quote.kind, rule, cfg.confirmed_permissions)
    if not has_perm:
        if quote.kind == "hk_stock" and "hk_stock_connect" in set(cfg.confirmed_permissions) and rule and not rule.hk_connect_eligible:
            reasons.append("hk_connect_ineligible")
        else:
            reasons.append("account_permission_unconfirmed")

    # 2. Rule Presence and Status
    if rule is None:
        reasons.append("rules_unverified")
        t0 = None
        source_url = None
        lot = None
        fee = None
    else:
        source_url = rule.source_url
        t0 = rule.t0

        # T+0 check
        if rule.t0 is False:
            reasons.append("not_t0")
        elif rule.t0 is not True:
            reasons.append("t0_rule_unverified")

        # Status checks: suspension, early redemption, delisting
        if rule.is_suspended:
            reasons.append("instrument_suspended")
        if rule.is_called_early:
            reasons.append("convertible_bond_called_early")
        if rule.is_delisted:
            reasons.append("instrument_delisted")

        # Currency consistency
        if rule.currency != quote.currency:
            reasons.append("rule_currency_mismatch")

        # Rule expiration
        if rule.effective_until and str(rule.effective_until) < now.date().isoformat():
            reasons.append("rules_expired")

        # Lot size
        lot = to_decimal(rule.lot_size)
        if lot is None or lot <= 0 or lot != lot.to_integral_value():
            reasons.append("lot_size_unverified")
            lot = None

        # Fee buffer
        fee = to_decimal(rule.fee_buffer)
        if fee is None or fee < 0:
            reasons.append("fees_unverified")
            fee = None

    # 3. Price and Margin Calculation
    if quote.price is None or quote.price <= 0:
        reasons.append("quote_unavailable")
        minimum = None
    elif lot is not None:
        minimum = quote.price * lot
    else:
        minimum = None

    if quote.kind == "futures":
        multiplier = rule.contract_multiplier if rule else None
        margin = rule.margin_rate if rule else None
        if multiplier is None or multiplier <= 0 or margin is None or not (Decimal("0") < margin <= Decimal("1")):
            reasons.append("margin_contract_unverified")
            minimum = None
        elif minimum is not None:
            minimum = minimum * multiplier * margin

    # 4. Cash and Reserve sufficiency (strict currency separation)
    total = minimum + fee if minimum is not None and fee is not None else None
    if quote.currency == "HKD":
        cash = cfg.cash_hkd - cfg.reserve_hkd
    else:
        cash = cfg.cash_cny - cfg.reserve_cny

    if minimum is not None and (minimum > cash or (total is not None and total > cash)):
        reasons.append("insufficient_cash")

    # 5. Freshness
    age = (now - quote.source_time).total_seconds() if quote.source_time else None
    freshness = "unknown" if age is None else "fresh" if 0 <= age <= 180 else "future" if age < 0 else "stale"

    # Execution capability flag
    from .profiles import _get_etf_map
    executable = (
        quote.kind == "etf"
        and quote.instrument_id in _get_etf_map()
        and _get_etf_map()[quote.instrument_id]["settlement_rule"] == "T+0"
    )

    return {
        "permission_required": req_perm,
        "t0": t0,
        "rule_source": source_url,
        "lot_size": int(lot) if lot is not None else None,
        "minimum_principal_or_margin": str(minimum) if minimum is not None else None,
        "minimum_with_fee_buffer": str(total) if total is not None else None,
        "available_cash": str(max(Decimal("0"), cash)),
        "freshness": freshness,
        "purchase_conditions_met": len(reasons) == 0,
        "blocking_reasons": reasons,
        "execution_supported": executable,
        "paper_candidate": len(reasons) == 0 and freshness == "fresh" and executable,
        "execution_note": "requires_existing_T0_Guard_OMS_and_dual_model_gate"
        if executable
        else "market_execution_adapter_not_implemented",
    }
