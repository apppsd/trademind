from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from test_fast_decision import make_pipe, I
from trademind.evaluation.fast_comparison import compare_archives, facts_hash

def contexts(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    values=[]
    for i in range(2):
        values.append(pipe.context_builder.build_minute(I,tick[0]))
        tick[0]+=timedelta(minutes=5)
    pipe.db.close()
    return values

def archive(ctx,action='WAIT'):
    return {'available_at':ctx.as_of.isoformat(),'facts_hash':facts_hash(ctx),
            'action':action,'target_weight':'0',
            'account':{'cash':'500','reserved_cash':'0','positions':{}},'model_calls':[]}

def test_archived_comparison_three_independent_accounts_no_http(tmp_path):
    ctx=contexts(tmp_path)
    ar={c.context_id:archive(c) for c in ctx}
    with patch('requests.Session.request',side_effect=AssertionError('replay cannot call HTTP')):
        report=compare_archives(ctx,{'old':ar,'new':ar},artifact_kind='fixture')
    assert report['status']=='archived_intents_replayed'
    assert report['current_model_calls']==0
    assert len({v['account_id'] for v in report['accounts'].values()})==3
    assert all(v['equity']=='500' for v in report['accounts'].values())
    assert all(v['open_orders']==0 for v in report['accounts'].values())
    assert report['calibration'].startswith('unavailable')

def test_missing_archives_cost_unknown(tmp_path):
    report=compare_archives(contexts(tmp_path),{})
    assert report['status']=='incomplete_archives'
    assert report['accounts']['new']['model_cost_cny'] is None
    assert report['accounts']['new']['net_after_model_cost'] is None

def test_future_and_wrong_input_archive_rejected(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c) for c in ctx}
    ar[ctx[0].context_id]['available_at']=(ctx[0].as_of+timedelta(minutes=1)).isoformat()
    report=compare_archives(ctx,{'old':ar,'new':ar})
    assert report['status']=='incomplete_archives'
    assert any('unavailable' in e for e in report['accounts']['new']['errors'])
    ar[ctx[0].context_id]=archive(ctx[0]);ar[ctx[0].context_id]['facts_hash']='wrong'
    report=compare_archives(ctx,{'old':ar,'new':ar})
    assert any('hash mismatch' in e for e in report['accounts']['new']['errors'])

def test_archive_account_cannot_be_rebased(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c) for c in ctx}
    ar[ctx[0].context_id]['account']['cash']='501'
    report=compare_archives(ctx,{'old':ar,'new':ar})
    assert any(g['reason']=='account_state_mismatch' for g in report['accounts']['new']['archive_gaps'])

def test_future_market_bars_rejected(tmp_path):
    ctx=contexts(tmp_path)
    ctx[0].aux['minute_bars'][-1]['available_at']=(ctx[0].as_of+timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError):compare_archives(ctx,{})

def test_actionable_archive_requires_model_provenance(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c,'BUY') for c in ctx}
    report=compare_archives(ctx,{'old':ar,'new':ar})
    assert any('provenance' in e for e in report['accounts']['new']['errors'])


def test_reserved_cost_is_not_reported_as_actual(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c) for c in ctx}
    ar[ctx[0].context_id]['model_calls']=[{'cost_amount':'1','cost_currency':'CNY','cost_source':'operator_price_request_upper_bound','latency_ms':10}]
    report=compare_archives(ctx,{'old':ar,'new':ar})
    assert report['accounts']['new']['model_cost_cny'] is None
    assert report['accounts']['new']['net_after_model_cost'] is None


# -- TASK-20261013-07: configurable initial cash (ISSUE-20261012-04) -------------

def runtime_with(cash):
    return {'t0': {'initial_cash': cash}, 'paper': {'initial_cash': cash}}


def test_configured_runtime_cash_replayed_not_500_literal(tmp_path):
    ctx=contexts(tmp_path)
    ar={c.context_id:archive(c) for c in ctx}
    for row in ar.values():
        row['account']={'cash':'1000','reserved_cash':'0','positions':{}}
    rt=runtime_with('1000')
    report=compare_archives(ctx,{'old':ar,'new':ar},runtime_configs={'old':rt,'new':rt})
    for label in ('old','new'):
        acc=report['accounts'][label]
        assert acc['initial_cash']=='1000' and acc['equity']=='1000' and acc['trading_net']=='0'
    assert report['accounts']['rules']['initial_cash']=='1000'  # baseline follows the pair fund


def test_ab_fund_mismatch_rejected_before_any_account(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c) for c in ctx}
    with pytest.raises(ValueError,match='equal initial cash across archives'):
        compare_archives(ctx,{'old':ar,'new':ar},
                         runtime_configs={'old':runtime_with('1000'),'new':runtime_with('500')})


def test_invalid_recorded_cash_rejected(tmp_path):
    ctx=contexts(tmp_path);ar={c.context_id:archive(c) for c in ctx}
    for bad in ('0','-5','NaN'):
        with pytest.raises(ValueError,match='positive finite'):
            compare_archives(ctx,{'old':ar,'new':ar},runtime_configs={'old':runtime_with(bad),'new':runtime_with(bad)})
    mismatch={'t0':{'initial_cash':'1000'},'paper':{'initial_cash':'500'}}
    with pytest.raises(ValueError,match='equal t0/paper'):
        compare_archives(ctx,{'old':ar,'new':ar},runtime_configs={'old':mismatch,'new':mismatch})
