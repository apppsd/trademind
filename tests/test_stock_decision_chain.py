"""TASK-20261027-05: stock decision chain, selector, isolated account."""
from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import Mock

import pytest

from trademind.config import AppConfig
from trademind.domain.base import new_id
from trademind.domain.enums import ProviderKind
from trademind.domain.orders import Order
from trademind.market.base import BEIJING, MinuteBar
from trademind.market.profiles import get_profile
from trademind.market.stock_profiles import (
    instrument_id_for,
    split_instrument,
    stock_profile_for_instrument,
)
from trademind.market.stock_selector import StockCandidateSelector
from trademind.models.base import ModelOutput, build_call
from trademind.models.deepseek import DeepSeekProvider
from trademind.models.jev import TypeSafeJEVProvider
from trademind.pipeline import Pipeline
from trademind.portfolio.ledger import Ledger
from trademind.storage.db import Database
from scripts.init_stock_account import ACCOUNT_ID, main as init_main

I = "600000.SH"
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=BEIJING)  # Tuesday mid-morning


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def strategy():
    return dict(trend="上涨", risk="风险有界", critic="关注成本", reason="测试计划",
                allowed_actions=["BUY", "SELL", "HOLD", "WAIT"],
                entry_conditions=[dict(field="close", operator="gt", value="1")],
                exit_conditions=[dict(field="close", operator="lt", value="2")],
                invalidation_conditions=[], holding_horizon_seconds=300)


def make_stock_pipe(tmp_path, price="4.00", stock_chain=True, instrument=I):
    tick = [NOW]
    cfg = AppConfig()
    cfg.t0.enabled = True
    cfg.t0.profile = "jev_fast_decision"
    cfg.t0.stock_chain = stock_chain
    cfg.t0.whitelist = [instrument]
    cfg.t0.initial_cash = D("1000")
    cfg.t0.max_single_exposure = D("450")
    cfg.t0.max_total_exposure = D("900")
    cfg.t0.min_cash_reserve = D("100")
    for k in ("model_input_cny_per_million", "model_output_cny_per_million",
              "jev_input_cny_per_million", "jev_output_cny_per_million"):
        setattr(cfg.t0, k, D("1"))
    cfg.account_id = "stock-test"
    cfg.universe = [instrument]
    cfg.storage.url = f"sqlite:///{tmp_path}/stock-fast.db"
    cfg.data.provider_pref = ["fixture"]
    cfg.risk.quote_max_age_seconds = 180
    pipe = Pipeline(cfg, clock=lambda: tick[0])
    pipe.ensure_account(at=NOW - timedelta(minutes=30))

    def bars(*a, **kw):
        return ([MinuteBar(instrument_id=instrument, trade_date=(tick[0] - timedelta(minutes=i)).date(),
                           bar_time=tick[0] - timedelta(minutes=i),
                           open=price, high=price, low=price, close=price,
                           volume="1000", amount=str(D(price) * 1000),
                           provider="test", source_time=tick[0] - timedelta(minutes=i),
                           available_at=tick[0] - timedelta(minutes=i),
                           fetched_at=tick[0] - timedelta(minutes=i))
                for i in reversed(range(20))], None)

    pipe.router.get_minute_bars = Mock(side_effect=bars)
    pipe.llm = DeepSeekProvider(api_key="test-only", model="deepseek-flash", budget=pipe.model_budget)
    pipe.jev = TypeSafeJEVProvider(api_key="test-only", budget=pipe.model_budget)
    pipe.llm.capability_verified = pipe.jev.capability_verified = True

    def complete(**kw):
        pipe.model_budget.reserve_request(1000, 1024, role=kw["role"])
        call = build_call(role=kw["role"], provider=ProviderKind.DEEPSEEK, model="deepseek-flash",
                          system=kw["system"], user=kw["user"], trace_id=kw["trace_id"])
        return ModelOutput(text="", parsed=strategy(), call=call)

    pipe.llm.complete = Mock(side_effect=complete)

    def post(url, json, timeout):
        opts = list(json["questions"]["action"]["criteria"])
        choice = "BUY" if "BUY" in opts else "SELL" if "SELL" in opts else "HOLD"
        probs = {o: 0 for o in opts}
        probs[choice] = 1
        answers = {k: {"type": "noul", "noul": 0.95} for k in json["questions"] if k != "action"}
        answers["action"] = {"type": "choice", "choice": choice, "probabilities": probs, "confidence": 1}
        return Response({"model": "jev-1.13.0", "answers": answers,
                         "usage": {"input_tokens": 100, "output_tokens": 50}})

    pipe.jev.session.post = Mock(side_effect=post)
    return pipe, tick


# -- pipeline integration --------------------------------------------------------

def test_stock_chain_buys_one_lot_within_caps(tmp_path):
    pipe, tick = make_stock_pipe(tmp_path)
    result = pipe.run_decision(I, tick[0])
    assert result.error is None
    assert result.intent.action.value == "BUY"
    assert result.qty == 100  # one lot 400 ≤ 450; cash allows more but cap binds
    state = pipe.jev.session.post.call_args.kwargs["json"]["state"]
    assert state["account"]["cash"] == "1000"


def test_default_chain_still_rejects_t_plus_one(tmp_path):
    pipe, tick = make_stock_pipe(tmp_path, stock_chain=False)
    result = pipe.run_decision(I, tick[0])
    # V1 get_profile('600000.SH') is a T+1 main-board profile; the unmodified
    # T0 gate must still refuse it — ETF behavior unchanged.
    assert result.intent.action.value == "WAIT"
    assert "whitelist" in (result.intent.reason or "") or "whitelist" in str(
        result.diagnostics.get("reason", ""))


def test_stock_entry_unaffordable_waits(tmp_path):
    pipe, tick = make_stock_pipe(tmp_path, price="5.00")  # one lot 500 > 450
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == "WAIT"
    assert result.intent.reason == "minimum lawful lot unaffordable"


# -- profile resolution boundaries ------------------------------------------------

def test_chinext_resolvable_in_stock_chain_and_still_raises_in_v1():
    profile = stock_profile_for_instrument("300750.SZ")
    assert profile.price_limit_ratio == D("0.20") and profile.t_plus_days == 1
    with pytest.raises(ValueError, match="fixture scope"):
        get_profile("300750.SZ")  # V1 chain keeps its current guard


def test_out_of_scope_instrument_refused_explicitly():
    with pytest.raises(ValueError, match="outside the stock-chain v1 board scope"):
        stock_profile_for_instrument("688001.SH")  # STAR stays out of v1
    with pytest.raises(ValueError, match="style"):
        split_instrument("600000")  # missing suffix


def test_instrument_id_for_maps_observation_boards():
    assert instrument_id_for("000001", "sz_main") == "000001.SZ"
    assert instrument_id_for("300750", "chinext") == "300750.SZ"
    assert instrument_id_for("600000", "sh_main") == "600000.SH"


# -- selector ---------------------------------------------------------------------

def _cands(*pairs):
    return [{"code": c, "board": b} for b, c in pairs]


def test_selector_deterministic_and_position_first():
    sel = StockCandidateSelector(instrument_id_for)
    cands = _cands(("sz_main", "000001"), ("sh_main", "600000"))
    a = sel.select([], cands, {}, NOW)
    b = sel.select([], cands, {}, NOW)
    assert a.to_dict() == b.to_dict()
    assert a.instrument_id == "600000.SH" and a.reason == "candidate_rank"
    held = sel.select(["600000.SH"], cands, {}, NOW)
    assert held.instrument_id == "600000.SH" and held.reason == "position_first"


def test_selector_cooldown_skip_and_exhaustion():
    sel = StockCandidateSelector(instrument_id_for)
    cands = _cands(("sz_main", "000001"), ("sh_main", "600000"))
    skipped = sel.select([], cands, {"600000.SH": NOW + timedelta(seconds=60)}, NOW)
    assert skipped.instrument_id == "000001.SZ"  # cooled stock skipped with reason
    reasons = dict(skipped.ranked)
    assert reasons["600000.SH"] == "cooldown"
    all_cool = sel.select([], cands, {"600000.SH": NOW + timedelta(seconds=60),
                                      "000001.SZ": NOW + timedelta(seconds=60)}, NOW)
    assert all_cool.instrument_id is None and all_cool.reason == "none_cooldown"
    expired = sel.select([], cands, {"600000.SH": NOW}, NOW)  # boundary: until==now → free
    assert expired.instrument_id == "600000.SH"


# -- isolated account bootstrap & cross-db isolation -------------------------------

def test_init_stock_account_idempotent(tmp_path):
    db_path = tmp_path / "stock.db"
    assert init_main(["--db", str(db_path)]) == 0
    assert init_main(["--db", str(db_path)]) == 0  # second run must not double-deposit
    db = Database(f"sqlite:///{db_path}")
    snap = Ledger(db, ACCOUNT_ID, stock_profile_for_instrument(I).fees).snapshot(NOW)
    assert snap.cash == D("1000") and not snap.positions
    db.close()


def test_stock_and_etf_dbs_isolated(tmp_path):
    stock_db_path, etf_db_path = tmp_path / "stock.db", tmp_path / "etf.db"
    assert init_main(["--db", str(stock_db_path)]) == 0

    etf_db = Database(f"sqlite:///{etf_db_path}")
    etf_ledger = Ledger(etf_db, "paper-t0-local-1k", stock_profile_for_instrument(I).fees)
    etf_ledger.deposit(D("1000"))
    etf_version_before = etf_ledger.snapshot(NOW).version

    # Both databases open and writable concurrently; stock ops leave ETF db untouched.
    stock_db = Database(f"sqlite:///{stock_db_path}")
    stock_ledger = Ledger(stock_db, ACCOUNT_ID, stock_profile_for_instrument(I).fees)
    stock_ledger.deposit(D("500"))  # would be a second deposit on the same account/db
    assert stock_ledger.snapshot(NOW + timedelta(seconds=1)).cash == D("1500")
    assert etf_ledger.snapshot(NOW + timedelta(seconds=1)).version == etf_version_before
    assert etf_ledger.snapshot(NOW + timedelta(seconds=1)).cash == D("1000")
    assert stock_db.path != etf_db.path
    stock_db.close()
    etf_db.close()


def test_rejects_non_stock_config():
    class _FakeNS:
        stock_chain = False

    # init without config flag checks nothing; with a config path pointing to
    # a non-stock config it must refuse (load_config needs a real file, so
    # exercise the guard via a minimal toml).
    cfg_path = None
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
        handle.write("[t0]\nenabled = true\n")
        cfg_path = handle.name
    rc = init_main(["--config", cfg_path])
    assert rc == 1
