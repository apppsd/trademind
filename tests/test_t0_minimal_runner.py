"""Tests for TASK-58: Minimal runnable T0 paper simulation runner and workbench integration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest

from trademind.config import AppConfig, T0ExperimentConfig
from trademind.domain.base import new_id, utcnow
from trademind.domain.enums import TradeAction
from trademind.market.base import BEIJING, MinuteBar
from trademind.pipeline import Pipeline
from trademind.runner import cmd_t0_paper
from trademind.storage import Database


def test_cmd_t0_paper_runs_and_prints_json(capsys, monkeypatch, tmp_path):
    # Route storage to temporary SQLite database
    db_path = tmp_path / "t0_runner.db"
    monkeypatch.setenv("TRADEMIND_STORAGE_URL", f"sqlite:///{db_path}")

    cfg = AppConfig()
    cfg.t0.enabled = True
    cfg.storage.url = f"sqlite:///{db_path}"
    cfg.universe = ['513050.SH']
    cfg.account_id = 'paper-t0-500'
    monkeypatch.setattr('trademind.runner._load', lambda: cfg)
    from trademind.pipeline import DecisionResult
    monkeypatch.setattr(Pipeline, 'run_decision', lambda self, iid, at: DecisionResult('test', iid, at, '', guard_verdict='WAIT_NO_TRADE'))
    exit_code = cmd_t0_paper(instrument="513050.SH", steps=1)
    assert exit_code == 2  # Local stub success is explicitly unverified.

    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["status"] == "unverified"
    assert output["account_id"] == "paper-t0-500"
    assert output["mode"] == "paper"
    assert Decimal(output["cash"]) == Decimal("500")  # Initial 500 virtual cash
    assert Decimal(output["equity"]) == Decimal("500")
    assert len(output["results"]) == 1
    assert output["results"][0]["step"] == 1


def test_t0_pipeline_dual_model_wait_zero_freeze(tmp_path):
    db_path = tmp_path / "t0_pipe.db"
    cfg = AppConfig()
    cfg.storage.url = f"sqlite:///{db_path}"
    cfg.t0.enabled = True
    cfg.account_id = "paper-t0-500"
    cfg.mode = "paper"
    cfg.universe = ["513050.SH"]

    now = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
    pipe = Pipeline(cfg, clock=lambda: now)
    pipe.ensure_account(initial_cash=Decimal("500.00"), at=now)
    res = pipe.run_decision("513050.SH", now)

    # No fresh minute data must fail closed, not succeed on a daily fixture.
    snap = pipe.ledger.snapshot(now)
    assert snap.reserved_cash == Decimal("0")  # Zero cash frozen on no trade
    assert 'minute' in res.error
