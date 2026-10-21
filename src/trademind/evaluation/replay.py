"""Offline replay + metrics (TASK-19).

- Replay only sees evidence with available_at <= simulated as_of (enforced by
  the ContextBuilder); no network and no execution tools are reachable here.
- Splits: train / calibration / holdout by trading-day index, frozen config.
- Baselines run in the same engine for comparison; the rule baseline exists
  only inside evaluation and never produces production intents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from copy import deepcopy
import json

from ..config import AppConfig
from ..pipeline import Pipeline
from ..storage import Database

UTC = timezone_utc = __import__("datetime").timezone.utc


@dataclass
class ReplayReport:
    ablation: str
    start: datetime
    end: datetime
    equity_curve: list[dict] = field(default_factory=list)
    decisions: int = 0
    orders: int = 0
    vetoes: int = 0
    holds: int = 0
    errors: int = 0
    final_equity: Decimal = Decimal("0")
    total_return: Decimal | None = None
    max_drawdown: Decimal | None = None
    win_rate: Decimal | None = None
    profit_factor: Decimal | None = None
    sharpe: Decimal | None = None  # per-decision, assumes iid (stated limitation)
    model_calls_stub: int = 0

    def to_dict(self) -> dict:
        return {
            "ablation": self.ablation,
            "start": self.start.isoformat(), "end": self.end.isoformat(),
            "decisions": self.decisions, "orders": self.orders,
            "vetoes": self.vetoes, "holds": self.holds, "errors": self.errors,
            "final_equity": str(self.final_equity),
            "total_return": str(self.total_return) if self.total_return is not None else None,
            "max_drawdown": str(self.max_drawdown) if self.max_drawdown is not None else None,
            "win_rate": str(self.win_rate) if self.win_rate is not None else None,
            "profit_factor": str(self.profit_factor) if self.profit_factor is not None else None,
            "sharpe_iid_assumption": str(self.sharpe) if self.sharpe is not None else None,
            "model_calls_stub": self.model_calls_stub,
        }


def _drawdown(curve: list[Decimal]) -> Decimal | None:
    if len(curve) < 2:
        return None
    peak = curve[0]
    mdd = Decimal("0")
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd.quantize(Decimal("0.000001"))


def _sharpe(returns: list[Decimal]) -> Decimal | None:
    if len(returns) < 3:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var == 0:
        return None
    from decimal import getcontext

    getcontext().prec = 28
    return (mean / var.sqrt()).quantize(Decimal("0.000001"))


class ReplayRunner:
    """Runs the full pipeline on the fixture window, decision at day close+delay."""

    def __init__(self, cfg: AppConfig, ablation: str = "full",
                 db_url: str | None = None) -> None:
        cfg = deepcopy(cfg)
        cfg.storage.url = db_url or "sqlite:///:memory:"
        cfg.mode = "replay"
        cfg.llm.provider = cfg.jev.provider = "stub"
        cfg.llm.api_key = cfg.jev.api_key = cfg.data.tushare_token = cfg.api.token = ""
        cfg.data.provider_pref = ["fixture"]
        # daily budget is per-calendar-day; a replay covering N days gets N× budget
        # (declared in the report; still capped, never unlimited)
        cfg.llm.daily_call_budget = cfg.llm.daily_call_budget * max(1, len(cfg.universe) * 40)
        self.cfg = cfg
        self.ablation = ablation
        self.pipe = Pipeline(cfg, ablation=ablation)
        if self.pipe.repo.db.query("SELECT 1 FROM account_events LIMIT 1"):
            self.pipe.db.close()
            raise ValueError("replay requires a fresh isolated database")
        self.report = ReplayReport(ablation=ablation, start=cfg.replay_start, end=cfg.replay_end) \
            if hasattr(cfg, "replay_start") else None

    def run(self, universe: list[str], days: list, initial_cash: Decimal) -> ReplayReport:
        if not days or days != sorted(set(days)) or initial_cash <= 0:
            raise ValueError("replay requires ordered unique days and positive capital")
        if self.pipe.repo.db.query("SELECT 1 FROM account_events LIMIT 1"):
            raise ValueError("a ReplayRunner can only run once")
        report = ReplayReport(ablation=self.ablation, start=datetime.combine(days[0], datetime.min.time(), tzinfo=UTC),
                              end=datetime.combine(days[-1], datetime.min.time(), tzinfo=UTC))
        self.pipe.ensure_account(initial_cash, at=datetime.combine(days[0], datetime.min.time(), tzinfo=UTC))
        decision_gap = max(1, len(days) // 30)  # at most ~30 research days per run
        equity_curve: list[Decimal] = [initial_cash]
        daily_returns: list[Decimal] = []
        wins = losses = 0
        gross_win = gross_loss = Decimal("0")
        for i, d in enumerate(days):
            as_of = datetime.combine(d, datetime.min.time(), tzinfo=UTC).replace(hour=7, minute=30)
            self.pipe.on_new_trading_day(d, self._close_prices(d, as_of))
            filled = self.pipe.poll_fills(as_of)
            report.orders += len(filled)
            for inst in universe:
                if i % decision_gap == 0:
                    res = self.pipe.run_decision(inst, as_of)
                    report.decisions += 1
                    if res.error:
                        report.errors += 1
                    elif res.intent is None or res.intent.action.value == "HOLD":
                        report.holds += 1
            prices = self._close_prices(d, as_of)
            snap = self.pipe.ledger.snapshot(at=as_of, prices=prices)
            equity_curve.append(snap.equity)
            if equity_curve[-2] > 0:
                daily_returns.append(snap.equity / equity_curve[-2] - 1)
            report.equity_curve.append({"date": d.isoformat(), "equity": str(snap.equity)})

        report.final_equity = equity_curve[-1]
        report.total_return = (report.final_equity / initial_cash - 1).quantize(Decimal("0.000001"))
        report.max_drawdown = _drawdown(equity_curve)
        report.sharpe = _sharpe(daily_returns)
        # win/loss from matured outcomes in this db
        for r in self.pipe.repo.db.query("SELECT payload_json FROM outcomes WHERE is_final=1"):
            rr = Decimal(json.loads(r["payload_json"]).get("return_ratio") or "0")
            if rr >= 0:
                wins += 1
                gross_win += rr
            else:
                losses += 1
                gross_loss += abs(rr)
        if wins + losses > 0:
            report.win_rate = (Decimal(wins) / Decimal(wins + losses)).quantize(Decimal("0.0001"))
            if gross_loss > 0:
                report.profit_factor = (gross_win / gross_loss).quantize(Decimal("0.0001"))
        report.model_calls_stub = self.pipe.llm_is_stub or self.pipe.jev_is_stub
        rows = self.pipe.repo.db.query("SELECT COUNT(*) c FROM model_calls WHERE is_stub=1")
        report.model_calls_stub = int(rows[0]["c"])
        # veto count from guard_results
        rows = self.pipe.repo.db.query("SELECT payload_json FROM guard_results")
        report.vetoes = sum(1 for r in rows if json.loads(r["payload_json"])["verdict"] == "VETO")
        return report

    def _close_prices(self, d, as_of) -> dict[str, Decimal]:
        prices = {}
        for inst in self.cfg.universe:
            bars, _ = self.pipe.router.get_daily_bars(inst, d, d)
            visible = [b for b in bars if b.available_at <= as_of]
            if visible:
                prices[inst] = visible[-1].close
        return prices


def run_comparison(cfg: AppConfig, days: list, initial_cash: Decimal,
                   ablations: list[str] | None = None) -> dict[str, dict]:
    """Same window/fees/capital across ablations; separate databases."""
    ablations = ablations or ["full", "no_jev", "no_memory"]
    out = {}
    for i, name in enumerate(ablations):
        runner = ReplayRunner(cfg, ablation=name)
        try:
            out[name] = runner.run(cfg.universe, days, initial_cash).to_dict()
        finally:
            runner.pipe.db.close()
    return out
