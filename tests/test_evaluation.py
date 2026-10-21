"""TASK-19/20 evaluation tests: replay isolation, metrics, calibration."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.config import AppConfig
from trademind.evaluation import (
    CostReport,
    ReplayRunner,
    brier_score,
    calibrate,
    log_loss,
    reliability_bins,
)
from trademind.market.calendar import TradingCalendar

UTC = timezone.utc


def test_calibration_metrics_hand_verified():
    assert brier_score([(0.8, 1), (0.4, 0)]) == 0.1  # (0.04+0.16)/2
    assert brier_score([(1.0, 0)]) == 1.0
    assert log_loss([]) is None
    pairs = [(0.9, 1), (0.1, 0), (0.6, 0)]
    bins, ece = reliability_bins(pairs, n_bins=2)
    assert bins[1]["n"] == 2 and bins[0]["n"] == 1  # 0.9,0.6 -> bin1; 0.1 -> bin0
    assert ece is not None and 0 <= ece <= 1


def test_calibration_exclusions():
    samples = [
        {"question_id": "continuation", "probability": 0.7,
         "semantics": "calibrated_event_probability", "outcome": 1},
        {"question_id": "continuation", "probability": 0.9,
         "semantics": "self_reported_answer_confidence", "outcome": 1},  # wrong semantics
        {"question_id": "continuation", "probability": 0.6,
         "semantics": "calibrated_event_probability", "outcome": None},  # unmatured
        {"question_id": "continuation", "refused": True},  # refusal
    ]
    rep = calibrate(samples, "continuation")
    assert rep.n_scored == 1
    assert rep.n_excluded_semantics == 1
    assert rep.n_excluded_unmatured == 1
    assert rep.n_excluded_refused == 1
    assert rep.brier == 0.09  # (0.7-1)^2


def test_calibration_undefined_when_no_samples():
    rep = calibrate([], "continuation")
    assert rep.undefined_reason and rep.brier is None


def test_cost_report_percentiles():
    calls = [{"role": "trader", "latency_ms": ms, "input_tokens": 100, "output_tokens": 50}
             for ms in (10, 20, 30, 100, 200)]
    rep = CostReport.from_calls(calls)
    assert rep["n_calls"] == 5
    assert rep["p50_ms"] == 30
    assert rep["p95_ms"] == 200
    assert rep["by_role"]["trader"]["tokens_in"] == 500


def test_replay_runs_offline_and_isolated(tmp_path):
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{tmp_path/'replay.db'}"
    cfg.mode = "replay"
    cal = TradingCalendar.load()
    days = cal.window(date(2026, 8, 3), date(2026, 8, 21))
    runner = ReplayRunner(cfg, ablation="full", db_url=f"sqlite:///{tmp_path/'replay.db'}")
    report = runner.run(["600036.SH"], days, Decimal("1000000"))
    d = report.to_dict()
    assert d["decisions"] >= 1
    assert Decimal(d["final_equity"]) > 0
    # stub chain must be visible in the report
    assert d["model_calls_stub"] >= 1
    # no future sentinel: equity curve dates within window
    assert all(d0["date"] <= "2026-08-21" for d0 in report.equity_curve)
