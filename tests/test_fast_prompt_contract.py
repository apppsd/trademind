import json
from datetime import timedelta
from unittest.mock import Mock

import pytest

from test_fast_decision import make_pipe, strategy, I
from trademind.agents.fast_prompts import (
    STRATEGY_OUTPUT_FIELDS, STRATEGY_PROMPT_VERSION, FAST_PROMPT_VERSION,
)
from trademind.models.base import ModelOutput, prompt_hash
from trademind.market.profiles import get_profile
from trademind.errors import ProviderError


def test_chinese_prompt_version_preserves_old_fingerprint():
    from trademind.agents.fast_prompts import get_strategy_prompt_hash, get_strategy_prompt
    assert STRATEGY_PROMPT_VERSION=='strategy-4'
    assert get_strategy_prompt_hash('strategy-3')=='69e997a86e9807727a112ff435f586725bba7980bc2cffdbaee0612d7f882fd4'
    assert '简体中文' in get_strategy_prompt('strategy-4')


def test_deepseek_request_and_audit_include_chinese_instruction(tmp_path):
    from trademind.models.deepseek import DeepSeekProvider, chinese_output_system
    pipe, tick = make_pipe(tmp_path)
    provider=DeepSeekProvider('TEST_PLACEHOLDER',budget=pipe.model_budget)
    response=Mock(status_code=200)
    response.json.return_value={'choices':[{'message':{'content':'{"reason":"等待"}'}}],
                                'usage':{'prompt_tokens':10,'completion_tokens':5}}
    provider.session.post=Mock(return_value=response)
    output=provider.complete(role='strategy',system='json',user='test',trace_id='test')
    actual=provider.session.post.call_args.kwargs['json']['messages'][0]['content']
    assert actual==chinese_output_system('json') and '简体中文' in actual
    assert output.call.prompt_hash==prompt_hash(actual,'test')


def test_paired_examples_validate_and_do_not_mutate(tmp_path):
    from trademind.agents.fast_prompts import strategy_examples, STRATEGY_PROMPT
    from trademind.agents.strategy_planner import StrategyPlanner
    examples = strategy_examples()
    assert '不是当前行情' in STRATEGY_PROMPT
    for example in examples:
        pipe, tick = make_pipe(tmp_path / example['case'])
        context = pipe.context_builder.build_minute(I, tick[0])
        context.features.values = example['input']['features']
        pipe.llm.complete = Mock(return_value=ModelOutput(text='', parsed=example['output'], call=None))
        planner = StrategyPlanner(pipe.llm, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
        plan = planner.get(context, get_profile(I), 'example')
        assert pipe.repo.db.query('SELECT plan_id FROM strategy_plan_history WHERE account_id=?',
                                  (pipe.cfg.account_id,))[0]['plan_id'] == plan.plan_id
        assert set(example['output']) == set(STRATEGY_OUTPUT_FIELDS)
        if example['case'] == 'exit_without_new_entry':
            assert plan.exit_conditions[0].matches(context.features.values)
            assert all(a.value != 'BUY' for a in plan.allowed_actions)
    examples[0]['output']['allowed_actions'].append('BUY')
    assert strategy_examples()[0]['output']['allowed_actions'] == ['WAIT']


def test_strategy_payload_matches_production_condition_contract(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    result = pipe.run_decision(I, tick[0])
    assert result.order_id
    request = json.loads(pipe.llm.complete.call_args.kwargs['user'])
    schema = request['output_schema']
    assert set(schema['properties']) == set(schema['required']) == set(STRATEGY_OUTPUT_FIELDS)
    assert schema['additionalProperties'] is False
    assert request['condition_contract']['entry_match'] == 'all'
    assert request['condition_contract']['exit_match'] == 'any'
    assert request['condition_contract']['units']['ret_5m'] == 'decimal ratio'
    assert request['fees']['source'] == 'simulated ETF fee assumptions'
    assert request['risk_limits']['stop_loss_ratio'] == str(pipe.cfg.t0.stop_loss_ratio)
    assert request['trading_constraints']['shorting'] is False
    row = pipe.repo.get_coalesce(pipe._fast_pipeline.planner.key(I))
    assert row['meta']['plan']['prompt_version'] == STRATEGY_PROMPT_VERSION
    assert result.intent.prompt_id == FAST_PROMPT_VERSION
    assert pipe.jev.question_version == 'qv-fast-1'  # shared provider defaults stay unchanged
    payload = pipe.jev.session.post.call_args.kwargs['json']
    assert pipe.jev.last_call.prompt_hash == prompt_hash(
        FAST_PROMPT_VERSION, json.dumps(payload, ensure_ascii=False, allow_nan=False))


@pytest.mark.parametrize('case', ['metadata', 'missing', 'unknown_field', 'expression',
                                 'float_horizon', 'sell_without_exit', 'unavailable_field', 'english'])
def test_invalid_drafts_are_rejected_before_cache_or_jev(tmp_path, case):
    pipe, tick = make_pipe(tmp_path)
    draft = strategy()
    if case == 'metadata': draft['prompt_version'] = 'strategy-1'
    if case == 'missing': del draft['critic']
    if case == 'unknown_field': draft['entry_conditions'][0]['field'] = 'order_book_signal'
    if case == 'expression': draft['entry_conditions'][0]['value'] = 'vwap * 1.01'
    if case == 'float_horizon': draft['holding_horizon_seconds'] = 300.0
    if case == 'sell_without_exit': draft['exit_conditions'] = []
    if case == 'unavailable_field': draft['entry_conditions'][0]['field'] = 'ret_5m'
    if case == 'english': draft['reason'] = 'No supported setup'
    # Direct planner input exercises validation independently of the outer quality gate.
    from trademind.agents.strategy_planner import StrategyPlanner
    planner = StrategyPlanner(pipe.llm, pipe.repo, pipe.cfg.t0, pipe.cfg.account_id, lambda: tick[0])
    context = pipe.context_builder.build_minute(I, tick[0])
    if case == 'unavailable_field': context.features.values.pop('ret_5m')
    pipe.llm.complete = Mock(return_value=ModelOutput(text='', parsed=draft, call=None))
    with pytest.raises(ProviderError): planner.get(context, get_profile(I), 'test')
    assert not pipe.repo.get_coalesce(planner.key(I))
    pipe.jev.session.post.assert_not_called()
    assert not pipe.repo.open_orders()


def test_complete_wait_plan_creates_no_order_or_jev_request(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    original = pipe.llm.complete.side_effect
    def wait(**kwargs):
        output = original(**kwargs)
        output.parsed.update(allowed_actions=['WAIT'], entry_conditions=[], exit_conditions=[],
                             invalidation_conditions=[], reason='没有有证据支持的机会')
        return output
    pipe.llm.complete.side_effect = wait
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'WAIT' and not result.order_id
    pipe.jev.session.post.assert_not_called()
    assert pipe.repo.get_coalesce(pipe._fast_pipeline.planner.key(I))


def test_old_prompt_plan_is_not_reused_or_immediately_retried(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    pipe.run_decision(I, tick[0])
    planner = pipe._fast_pipeline.planner
    row = pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['prompt_version'] = 'strategy-1'
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])
    context = pipe.context_builder.build_minute(I, tick[0])
    assert planner.cached(context, get_profile(I).profile_id, tick[0]) is None
    with pytest.raises(ProviderError, match='cooldown'):
        planner.get(context, get_profile(I), 'test')
    assert pipe.llm.complete.call_count == 1


def test_sell_exit_does_not_require_buy_evidence_and_closes_position(tmp_path):
    pipe, tick = make_pipe(tmp_path)
    assert pipe.run_decision(I, tick[0]).order_id
    tick[0] += timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    original = pipe.jev.session.post.side_effect
    def sell(url, json, timeout):
        response = original(url, json, timeout)
        options = response.body['answers']['action']['probabilities']
        response.body['answers']['action'].update(choice='SELL', probabilities={k: int(k == 'SELL') for k in options})
        response.body['answers']['entry_suitable']['noul'] = 0.01
        return response
    pipe.jev.session.post.side_effect = sell
    tick[0] += timedelta(minutes=4)
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'SELL' and result.order_id
    payload = pipe.jev.session.post.call_args.kwargs['json']
    assert payload['state']['quality']['order_book'] is None
    assert payload['state']['plan']['critic']
    assert pipe.llm.complete.call_count == 1
    tick[0] += timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    assert not pipe.ledger.snapshot(tick[0]).positions
