from unittest.mock import Mock
import requests
import pytest
from decimal import Decimal

from test_fast_decision import make_pipe, Response


@pytest.mark.parametrize('failure', [401, 429, 500, 'timeout'])
def test_failed_jev_requests_keep_unknown_cost_pending(tmp_path, failure):
    pipe, _ = make_pipe(tmp_path)
    pipe.jev.session.post = Mock(side_effect=requests.exceptions.Timeout()) if failure == 'timeout' else Mock(
        return_value=Response({}))
    if failure != 'timeout':
        pipe.jev.session.post.return_value.status_code = failure
    result = pipe.jev.probe()
    assert not result['ok'] and not pipe.jev.capability_verified
    state = pipe.model_budget.snapshot()
    assert state['used'] == state['probe'] == 1
    assert Decimal(state['cost_pending_cny']) > 0
    assert Decimal(state['cost_settled_cny']) == 0
    assert state['cost_reserved_cny'] == state['cost_pending_cny']
    pipe.jev.session.post.assert_called_once()
    assert not pipe.repo.open_orders()
