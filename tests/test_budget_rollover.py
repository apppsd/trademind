from datetime import datetime, timedelta, timezone
from decimal import Decimal

from test_fast_decision import make_pipe


def test_rollover_keeps_unknown_reservations_and_settles_original_day(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    budget = pipe.model_budget
    now = [datetime(2026, 10, 9, 15, 59, tzinfo=timezone.utc)]
    budget.clock = lambda: now[0]
    old = budget.reserve_jev(1000)
    original = budget.snapshot()
    now[0] += timedelta(minutes=2)
    assert budget.snapshot()['used'] == 0
    assert pipe.repo.get_coalesce(budget.key)['meta'] == original
    fresh = budget.reserve_jev(2000)
    current = budget.snapshot()
    history = budget.key + ':history:2026-10-09'
    assert pipe.repo.get_coalesce(history)['meta'] == original
    budget.reconcile(old, Decimal('0.0001'))
    assert budget.snapshot() == current
    prior = pipe.repo.get_coalesce(history)['meta']
    assert Decimal(prior['cost_pending_cny']) == 0
    assert Decimal(prior['cost_settled_cny']) == Decimal('0.0001')
    now[0] += timedelta(days=1)
    budget.reserve_jev(1000)
    assert pipe.repo.get_coalesce(history)['meta'] == prior
    assert Decimal(pipe.repo.get_coalesce(budget.key + ':history:2026-10-10')['meta']['cost_pending_cny']) == fresh


def test_late_settlement_before_new_day_first_request(tmp_path):
    pipe, _ = make_pipe(tmp_path)
    budget = pipe.model_budget
    now = [datetime(2026, 10, 9, 15, 59, tzinfo=timezone.utc)]
    budget.clock = lambda: now[0]
    reserved = budget.reserve_jev(1000)
    assert reserved + Decimal('1') == Decimal(reserved) + 1
    now[0] += timedelta(minutes=2)
    budget.reconcile(reserved, Decimal('0.0001'))
    budget.reserve_jev(1000)
    prior = pipe.repo.get_coalesce(budget.key + ':history:2026-10-09')['meta']
    assert prior['used'] == 1
    assert Decimal(prior['cost_settled_cny']) == Decimal('0.0001')
    assert Decimal(prior['cost_pending_cny']) == 0
