from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from trademind.config import MarketAccessConfig
from trademind.market import access, multi_asset as m, rules
from trademind.market.rules import CANONICAL_RULES, MarketRule, get_rule

NOW = datetime.now(timezone.utc)


def make_quote(kind="etf", iid="513050.SH", price="1.0", currency=None, source_time=None):
    curr = currency or ("HKD" if kind == "hk_stock" else "CNY")
    st = source_time or NOW
    p = Decimal(str(price)) if price is not None else None
    return m.AssetQuote(iid, kind, "test_asset", curr, p, Decimal("1000000"), NOW, st, "endpoint")


# --- 1. ETF Qualification Tests ---

def test_etf_t0_approved_with_cn_fund_permission():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("etf", "513050.SH", price="2.5")
    rule = get_rule("513050.SH")
    assert rule is not None
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    assert res["blocking_reasons"] == []
    assert res["t0"] is True
    assert res["minimum_principal_or_margin"] == "250.0"
    assert res["minimum_with_fee_buffer"] == "251.0"


def test_etf_t1_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("etf", "510050.SH", price="3.0")
    rule = get_rule("510050.SH")
    assert rule is not None
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "not_t0" in res["blocking_reasons"]


def test_etf_rejected_without_account_permission():
    cfg = MarketAccessConfig(confirmed_permissions=[], cash_cny=Decimal("1000"))
    q = make_quote("etf", "513050.SH", price="2.5")
    rule = get_rule("513050.SH")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "account_permission_unconfirmed" in res["blocking_reasons"]


# --- 2. LOF Qualification Tests ---

def test_lof_t0_approved():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("lof", "162411.SZ", price="0.8")
    rule = get_rule("162411.SZ")
    assert rule is not None and rule.t0 is True
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    assert res["blocking_reasons"] == []
    assert res["minimum_with_fee_buffer"] == "81.0"


def test_lof_t1_domestic_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("lof", "161725.SZ", price="1.2")
    rule = get_rule("161725.SZ")
    assert rule is not None and rule.t0 is False
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "not_t0" in res["blocking_reasons"]


def test_lof_unverified_rule_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("lof", "169999.SZ", price="1.0")
    res = access.evaluate_eligibility(q, cfg, None, NOW)
    assert res["purchase_conditions_met"] is False
    assert "rules_unverified" in res["blocking_reasons"]


# --- 3. Convertible Bond Qualification Tests ---

def test_convertible_bond_approved():
    cfg = MarketAccessConfig(confirmed_permissions=["convertible_bond"], cash_cny=Decimal("2000"))
    q = make_quote("convertible_bond", "110001.SH", price="120.0")
    rule = get_rule("110001.SH")
    assert rule is not None
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    assert res["lot_size"] == 10
    # 120 * 10 = 1200, fee_buffer = 1.0 -> 1201.0
    assert res["minimum_principal_or_margin"] == "1200.0"
    assert res["minimum_with_fee_buffer"] == "1201.0"


def test_convertible_bond_early_redemption_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["convertible_bond"], cash_cny=Decimal("2000"))
    q = make_quote("convertible_bond", "128017.SZ", price="150.0")
    rule = get_rule("128017.SZ")
    assert rule is not None and rule.is_called_early is True
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "convertible_bond_called_early" in res["blocking_reasons"]


def test_convertible_bond_suspended_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["convertible_bond"], cash_cny=Decimal("2000"))
    q = make_quote("convertible_bond", "113016.SH", price="110.0")
    overrides = {"113016.SH": {"is_suspended": True, "source_url": "https://www.sse.com.cn/"}}
    rule = get_rule("113016.SH", overrides)
    assert rule is not None and rule.is_suspended is True
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "instrument_suspended" in res["blocking_reasons"]


# --- 4. Hong Kong Stock Direct vs Stock Connect Tests ---

def test_hk_stock_currency_isolation():
    # Only CNY cash, 0 HKD cash
    cfg = MarketAccessConfig(confirmed_permissions=["hk_stock_direct"], cash_cny=Decimal("100000"), cash_hkd=Decimal("0"))
    q = make_quote("hk_stock", "00700.HK", price="400.0")
    rule = get_rule("00700.HK")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "insufficient_cash" in res["blocking_reasons"]
    assert res["available_cash"] == "0"


def test_hk_stock_direct_approved():
    cfg = MarketAccessConfig(confirmed_permissions=["hk_stock_direct"], cash_hkd=Decimal("50000"), reserve_hkd=Decimal("1000"))
    q = make_quote("hk_stock", "00700.HK", price="400.0")
    rule = get_rule("00700.HK")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    assert res["blocking_reasons"] == []
    # 400 * 100 + 15 = 40015.0
    assert res["minimum_with_fee_buffer"] == "40015.0"


def test_hk_stock_connect_eligible_stock():
    cfg = MarketAccessConfig(confirmed_permissions=["hk_stock_connect"], cash_hkd=Decimal("50000"))
    q = make_quote("hk_stock", "00700.HK", price="400.0")
    rule = get_rule("00700.HK")
    assert rule is not None and rule.hk_connect_eligible is True
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True


def test_hk_stock_connect_ineligible_stock_rejected():
    cfg = MarketAccessConfig(confirmed_permissions=["hk_stock_connect"], cash_hkd=Decimal("50000"))
    q = make_quote("hk_stock", "00001.HK", price="50.0")
    rule = get_rule("00001.HK")
    assert rule is not None and rule.hk_connect_eligible is False
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "hk_connect_ineligible" in res["blocking_reasons"]


# --- 5. Futures Commodity vs Special vs Financial Tests ---

def test_futures_standard_approved():
    cfg = MarketAccessConfig(confirmed_permissions=["futures_standard"], cash_cny=Decimal("10000"))
    q = make_quote("futures", "RB2610.SHFE", price="3500.0")
    rule = get_rule("RB2610.SHFE")
    assert rule is not None
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    # 3500 * 1 lot * 10 multiplier * 0.10 margin = 3500.0 margin + 10 fee = 3510.0
    assert Decimal(res["minimum_principal_or_margin"]) == Decimal("3500")
    assert Decimal(res["minimum_with_fee_buffer"]) == Decimal("3510")


def test_futures_special_rejected_with_only_standard_permission():
    cfg = MarketAccessConfig(confirmed_permissions=["futures_standard"], cash_cny=Decimal("100000"))
    q = make_quote("futures", "SC2610.INE", price="600.0")
    rule = get_rule("SC2610.INE")
    assert rule is not None and rule.futures_category == "special_commodity"
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "account_permission_unconfirmed" in res["blocking_reasons"]
    assert res["permission_required"] == "futures_special"


def test_futures_special_approved_with_special_permission():
    cfg = MarketAccessConfig(confirmed_permissions=["futures_special"], cash_cny=Decimal("100000"))
    q = make_quote("futures", "SC2610.INE", price="600.0")
    rule = get_rule("SC2610.INE")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    # 600 * 1 * 1000 * 0.12 = 72000.0 margin + 20 fee = 72020.0
    assert Decimal(res["minimum_principal_or_margin"]) == Decimal("72000")
    assert Decimal(res["minimum_with_fee_buffer"]) == Decimal("72020")


def test_futures_financial_rejected_without_financial_permission():
    cfg = MarketAccessConfig(confirmed_permissions=["futures_standard"], cash_cny=Decimal("200000"))
    q = make_quote("futures", "IF2612.CFFEX", price="4000.0")
    rule = get_rule("IF2612.CFFEX")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "account_permission_unconfirmed" in res["blocking_reasons"]
    assert res["permission_required"] == "futures_financial"


def test_futures_financial_approved():
    cfg = MarketAccessConfig(confirmed_permissions=["futures_financial"], cash_cny=Decimal("200000"))
    q = make_quote("futures", "IF2612.CFFEX", price="4000.0")
    rule = get_rule("IF2612.CFFEX")
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is True
    # 4000 * 1 * 300 * 0.12 = 144000.0 + 30 = 144030.0
    assert Decimal(res["minimum_principal_or_margin"]) == Decimal("144000")
    assert Decimal(res["minimum_with_fee_buffer"]) == Decimal("144030")


# --- 6. Expiry and Timing Tests ---

def test_rules_expiry_date():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    q = make_quote("etf", "513050.SH", price="2.0")
    overrides = {"513050.SH": {"effective_until": "2020-01-01", "source_url": "https://www.sse.com.cn/"}}
    rule = get_rule("513050.SH", overrides)
    res = access.evaluate_eligibility(q, cfg, rule, NOW)
    assert res["purchase_conditions_met"] is False
    assert "rules_expired" in res["blocking_reasons"]


def test_quote_stale_and_future_freshness():
    cfg = MarketAccessConfig(confirmed_permissions=["cn_fund"], cash_cny=Decimal("1000"))
    rule = get_rule("513050.SH")

    # Stale quote (5 minutes old)
    q_stale = make_quote("etf", "513050.SH", price="2.0", source_time=NOW - timedelta(minutes=5))
    res = access.evaluate_eligibility(q_stale, cfg, rule, NOW)
    assert res["freshness"] == "stale"

    # Future quote
    q_future = make_quote("etf", "513050.SH", price="2.0", source_time=NOW + timedelta(minutes=1))
    res = access.evaluate_eligibility(q_future, cfg, rule, NOW)
    assert res["freshness"] == "future"
