"""Tests for TASK-42: ETF whitelist and market profiles."""

from decimal import Decimal
import pytest

from trademind.market.profiles import get_profile, _get_etf_map


def test_etf_whitelist_loading():
    etf_map = _get_etf_map()
    assert "513050.SH" in etf_map
    assert "518880.SH" in etf_map
    assert "510050.SH" in etf_map


def test_t0_etf_profile():
    p = get_profile("513050.SH")
    assert p.security_type == "etf"
    assert p.settlement_rule == "T+0"
    assert p.lot_size == 100
    assert p.price_tick == Decimal("0.001")
    assert p.fees.stamp_duty_rate_sell == Decimal("0.0")
    assert p.fees.commission_rate == Decimal("0.0001")


def test_t1_etf_profile():
    p = get_profile("510050.SH")
    assert p.security_type == "etf"
    assert p.settlement_rule == "T+1"
    assert p.fees.stamp_duty_rate_sell == Decimal("0.0")


def test_unverified_etf_rejected():
    with pytest.raises(ValueError, match="not in verified whitelist"):
        get_profile("519999.SH")


def test_stock_profile_retains_t1_and_stamp_duty():
    p = get_profile("600519.SH")
    assert p.security_type == "stock"
    assert p.settlement_rule == "T+1"
    assert p.lot_size == 100
    assert p.price_tick == Decimal("0.01")
    assert p.fees.stamp_duty_rate_sell == Decimal("0.0005")
