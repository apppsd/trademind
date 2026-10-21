from datetime import datetime, timezone
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest

from trademind import runner
from trademind.config import MarketAccessConfig
from trademind.market.selector import MultiAssetCandidateSelector

NOW = datetime.now(timezone.utc)


def sample_assets():
    return [
        {
            "instrument_id": "513050.SH",
            "kind": "etf",
            "name": "中概互联50ETF",
            "currency": "CNY",
            "price": "2.0",
            "turnover": "5000000.0",
            "source_time": NOW.isoformat(),
        },
        {
            "instrument_id": "513100.SH",
            "kind": "etf",
            "name": "纳指ETF",
            "currency": "CNY",
            "price": "1.5",
            "turnover": "1000000.0",
            "source_time": NOW.isoformat(),
        },
        {
            "instrument_id": "510050.SH",
            "kind": "etf",
            "name": "上证50ETF",
            "currency": "CNY",
            "price": "3.0",
            "turnover": "9000000.0",
            "source_time": NOW.isoformat(),
        },
        {
            "instrument_id": "110001.SH",
            "kind": "convertible_bond",
            "name": "可转债样本",
            "currency": "CNY",
            "price": "120.0",
            "turnover": "2000000.0",
            "source_time": NOW.isoformat(),
        },
        {
            "instrument_id": "128017.SZ",
            "kind": "convertible_bond",
            "name": "金禾转债(已强赎)",
            "currency": "CNY",
            "price": "150.0",
            "turnover": "3000000.0",
            "source_time": NOW.isoformat(),
        },
        {
            "instrument_id": "00700.HK",
            "kind": "hk_stock",
            "name": "腾讯控股",
            "currency": "HKD",
            "price": "400.0",
            "turnover": "10000000.0",
            "source_time": NOW.isoformat(),
        },
    ]


def test_selector_filters_unauthorized_and_ineligible_assets():
    cfg = MarketAccessConfig(
        confirmed_permissions=["cn_fund", "convertible_bond"],
        cash_cny=Decimal("5000"),
        cash_hkd=Decimal("0"),
    )
    selector = MultiAssetCandidateSelector(cfg, top_k_per_market=1, max_total_candidates=3)
    result = selector.select(sample_assets(), NOW)

    assert result.status == "success"
    # Filter reasons must capture:
    # 510050.SH: not_t0
    # 128017.SZ: convertible_bond_called_early
    # 00700.HK: account_permission_unconfirmed (and/or insufficient_cash)
    assert result.filtered_out_reasons.get("not_t0") == 1
    assert result.filtered_out_reasons.get("convertible_bond_called_early") == 1
    assert (
        result.filtered_out_reasons.get("account_permission_unconfirmed", 0)
        + result.filtered_out_reasons.get("insufficient_cash", 0)
    ) >= 1

    # Selected must have 513050.SH (higher turnover than 513100.SH) and 110001.SH
    selected_iids = [c["instrument_id"] for c in result.selected_candidates]
    assert "513050.SH" in selected_iids
    assert "513100.SH" not in selected_iids  # ranked out by top_k=1
    assert "110001.SH" in selected_iids
    assert "510050.SH" not in selected_iids
    assert "128017.SZ" not in selected_iids
    assert "00700.HK" not in selected_iids

    assert result.selected_count == 2
    assert result.budget_reserved_calls == 4  # 2 calls * 2 candidates


def test_selector_budget_bound_strict_capping():
    cfg = MarketAccessConfig(
        confirmed_permissions=["cn_fund", "convertible_bond"],
        cash_cny=Decimal("10000"),
    )
    # Even if top_k_per_market is 2, max_total_candidates=1 restricts output to exactly 1
    selector = MultiAssetCandidateSelector(cfg, top_k_per_market=2, max_total_candidates=1)
    result = selector.select(sample_assets(), NOW)
    assert result.selected_count == 1
    assert result.budget_reserved_calls == 2


def test_cli_market_select_zero_model_calls(tmp_path, monkeypatch, capsys):
    import trademind.config as config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "dotenv_values", Mock(side_effect=AssertionError("no dotenv")))
    monkeypatch.setattr(runner, "_config_path", None)

    # Prepare market observation file
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    obs_file = runtime_dir / "market-observation.json"
    obs_file.write_text(json.dumps({"status": "success", "assets": sample_assets()}), encoding="utf-8")

    code = runner.main(["market-select", "--top-k", "1", "--max-total", "2"])
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["model_calls"] == 0
    assert "selected_candidates" in data
    assert "market_breakdown" in data
