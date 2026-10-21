"""Tests for TASK-20261011-08: Prompt version promotion and rollback verification."""
import pytest

from trademind.agents.fast_prompts import (
    STRATEGY_PROMPT_VERSION,
    get_strategy_prompt,
    get_strategy_prompt_hash,
)
from trademind.evaluation.prompt_eval import (
    evaluate_promotion_eligibility,
    verify_version_rollback,
)


def _make_dummy_report(
    prompt_version="strategy-3",
    status="pass",
    contract_fail=0.0,
    forbidden_rec=0.0,
    buy_cov=1.0,
    exit_cov=1.0,
):
    return {
        "schema_version": 1,
        "status": status,
        "dataset_hash": "ds-hash-123",
        "config_version": "cfg-1",
        "model_versions": {"strategy": "deepseek-flash", "fast": "jev-1.13.0"},
        "prompt_versions": {"strategy": prompt_version, "fast": "fast-2"},
        "metrics": {
            "contract_failure_rate": {"value": contract_fail},
            "forbidden_recommendation_rate": {"value": forbidden_rec},
            "buy_coverage": {"value": buy_cov},
            "exit_coverage": {"value": exit_cov},
        },
    }


def test_strategy_prompt_version_registry_and_hash():
    p2 = get_strategy_prompt("strategy-2")
    p3 = get_strategy_prompt("strategy-3")
    assert "Synthetic input/output examples" not in p2
    assert "Synthetic input/output examples" in p3

    h2 = get_strategy_prompt_hash("strategy-2")
    h3 = get_strategy_prompt_hash("strategy-3")
    assert h2 != h3
    assert len(h2) == 64
    assert len(h3) == 64

    with pytest.raises(ValueError, match="unknown strategy prompt version"):
        get_strategy_prompt("nonexistent-v99")


def test_evaluate_promotion_eligibility_pass():
    baseline = _make_dummy_report(prompt_version="strategy-2")
    candidate = _make_dummy_report(prompt_version="strategy-3")
    res = evaluate_promotion_eligibility(baseline, candidate)
    assert res["decision"] == "PROMOTE"
    assert res["eligible"] is True
    assert res["checks"]["zero_contract_failures"] is True
    assert res["checks"]["zero_forbidden_recommendations"] is True
    assert res["checks"]["sufficient_buy_coverage"] is True
    assert res["checks"]["sufficient_exit_coverage"] is True


def test_evaluate_promotion_eligibility_rejects_forbidden_actions():
    baseline = _make_dummy_report(prompt_version="strategy-2")
    candidate = _make_dummy_report(prompt_version="strategy-3", forbidden_rec=0.05)
    res = evaluate_promotion_eligibility(baseline, candidate)
    assert res["decision"] == "REJECT"
    assert res["eligible"] is False
    assert res["checks"]["zero_forbidden_recommendations"] is False


def test_evaluate_promotion_eligibility_rejects_all_wait_cheating():
    baseline = _make_dummy_report(prompt_version="strategy-2")
    # All-WAIT prompt achieves zero buy coverage
    candidate = _make_dummy_report(prompt_version="strategy-3", buy_cov=0.0)
    res = evaluate_promotion_eligibility(baseline, candidate)
    assert res["decision"] == "REJECT"
    assert res["eligible"] is False
    assert res["checks"]["sufficient_buy_coverage"] is False


def test_evaluate_promotion_eligibility_incomplete_on_empty_real_archive():
    baseline = _make_dummy_report(prompt_version="strategy-2")
    candidate = _make_dummy_report(prompt_version="strategy-3")
    empty_archive = {
        "status": "incomplete_archives",
        "contexts": [],
    }
    res = evaluate_promotion_eligibility(
        baseline, candidate, real_archive_bundle=empty_archive
    )
    assert res["decision"] == "INCOMPLETE"
    assert res["eligible"] is False
    assert "incomplete" in res["reason"]


def test_verify_version_rollback():
    class DummyRepo:
        def query(self, q):
            return [{"key": "strategy-plan:paper-t0:513050.SH:attempt"}]

    class DummyPlanner:
        repo = DummyRepo()

    res = verify_version_rollback(DummyPlanner(), "strategy-3", "strategy-2")
    assert res["cache_invalidated_by_version_partition"] is True
    assert res["preserved_cooldown_attempts"] == 1
    assert res["ledger_untouched"] is True
    assert res["orders_untouched"] is True
