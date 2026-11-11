from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from trademind.config import AppConfig, load_config
from trademind.pipeline import Pipeline
from trademind.market.base import BEIJING, MinuteBar
from trademind.models.jev import TypeSafeJEVProvider
from trademind.models.deepseek import DeepSeekProvider
from trademind.models.base import ModelOutput, build_call
from trademind.models.daily_budget import DailyModelBudget
from trademind.domain.enums import OrderSide, OrderStatus, ProviderKind, RunMode
from trademind.domain.orders import Order, Fill
from trademind.domain.base import new_id
from trademind.domain.strategy import StrategyPlan, Condition
from trademind.market.profiles import get_profile
from trademind.errors import ProviderError

I='513050.SH'
NOW=datetime(2026,10,12,10,0,tzinfo=BEIJING)

class Response:
    status_code=200
    def __init__(self,body): self.body=body
    def json(self): return self.body

def strategy():
    return dict(trend='上涨',risk='风险有界',critic='关注成本',reason='测试计划',
        allowed_actions=['BUY','SELL','HOLD','WAIT'],entry_conditions=[dict(field='close',operator='gt',value='1')],
        exit_conditions=[dict(field='close',operator='lt',value='2')],invalidation_conditions=[],holding_horizon_seconds=300)

def minute(at,price='1.50'):
    return MinuteBar(instrument_id=I,trade_date=at.date(),bar_time=at,
        open=price,high=price,low=price,close=price,volume='1000',amount='1500',
        provider='test',source_time=at,available_at=at,fetched_at=at)

def make_pipe(tmp_path,mode='paper',account_id='fast-test'):
    tick=[NOW]
    cfg=AppConfig()
    cfg.t0.enabled=True
    cfg.t0.profile='jev_fast_decision'
    cfg.account_id=account_id
    cfg.universe=[I]
    cfg.mode=mode
    cfg.storage.url=f'sqlite:///{tmp_path}/fast.db'
    cfg.data.provider_pref=['fixture']
    cfg.risk.quote_max_age_seconds=180
    for k in ('model_input_cny_per_million','model_output_cny_per_million','jev_input_cny_per_million','jev_output_cny_per_million'):
        setattr(cfg.t0,k,D('1'))
    pipe=Pipeline(cfg,clock=lambda:tick[0])
    pipe.ensure_account(at=NOW-timedelta(minutes=30))
    def bars(*a,**kw):
        return ([minute(tick[0]-timedelta(minutes=i)) for i in reversed(range(20))],None)
    pipe.router.get_minute_bars=Mock(side_effect=bars)
    pipe.llm=DeepSeekProvider(api_key='test-only',model='deepseek-flash',budget=pipe.model_budget)
    pipe.jev=TypeSafeJEVProvider(api_key='test-only',budget=pipe.model_budget)
    pipe.llm.capability_verified=pipe.jev.capability_verified=True
    pipe.llm_is_stub=pipe.jev_is_stub=False
    def complete(**kw):
        reserve=lambda:pipe.model_budget.reserve_request(1000,1024,role=kw['role'])
        if kw.get('admit_request'):
            kw['admit_request'](kw['role'],reserve)
        else:
            reserve()
        call=build_call(role=kw['role'],provider=ProviderKind.DEEPSEEK,model='deepseek-flash',system=kw['system'],user=kw['user'],trace_id=kw['trace_id'])
        return ModelOutput(text='',parsed=strategy(),call=call)
    pipe.llm.complete=Mock(side_effect=complete)
    def post(url,json,timeout):
        opts=list(json['questions']['action']['criteria'])
        choice='BUY' if 'BUY' in opts else 'SELL' if 'SELL' in opts else 'HOLD'
        probs={o:0 for o in opts};probs[choice]=1
        answers={k:{'type':'noul','noul':0.95} for k in json['questions'] if k!='action'}
        answers['action']={'type':'choice','choice':choice,'probabilities':probs,'confidence':1}
        return Response({'model':'jev-1.13.0','answers':answers,'usage':{'input_tokens':100,'output_tokens':50}})
    pipe.jev.session.post=Mock(side_effect=post)
    return pipe,tick

def test_new_chain_buy_fill_sell_and_plan_reuse(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    result=pipe.run_decision(I,tick[0])
    assert result.error is None
    assert result.intent.action.value=='BUY'
    assert result.order_id and result.qty==100
    state=pipe.jev.session.post.call_args.kwargs['json']['state']
    assert len(state['bars'])==10 and state['account']['cash']=='500'
    assert state['bars'][0]['fetched_at'] and state['plan']['plan_id']
    assert state['quality']['order_book'] is None
    tick[0]+=timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    tick[0]+=timedelta(minutes=4)
    # Disallow further BUY in the cached plan so JEV requests a legal sell.
    planner=pipe._fast_pipeline.planner
    row=pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['allowed_actions']=['SELL','HOLD','WAIT']
    pipe.repo.save_coalesce(planner.key(I),tick[0],row['meta'])
    sell=pipe.run_decision(I,tick[0])
    assert sell.error is None and sell.intent.action.value=='SELL' and sell.order_id
    assert pipe.llm.complete.call_count==1
    tick[0]+=timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    assert not pipe.ledger.snapshot(tick[0]).positions
    assert pipe.model_budget.used==3

def test_shadow_never_freezes_or_submits(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='BUY'
    assert result.guard_verdict=='VETO' and not result.order_id
    assert pipe.ledger.snapshot(tick[0]).reserved_cash==0
    assert not pipe.repo.open_orders()

def test_missing_jev_answer_becomes_wait(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.jev.session.post=Mock(return_value=Response({'model':'jev-1.13.0','answers':{},'usage':{'input_tokens':1,'output_tokens':1}}))
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT' and not result.order_id
    assert pipe.ledger.snapshot(tick[0]).reserved_cash==0

def test_unverified_real_providers_do_not_spend(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.jev.capability_verified=False
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()

@pytest.mark.parametrize('change',['expiry','config','day','rule'])
def test_plan_restart_and_invalidation(tmp_path,change):
    pipe,tick=make_pipe(tmp_path,'shadow')
    pipe.run_decision(I,tick[0])
    planner=pipe._fast_pipeline.planner
    context=pipe.context_builder.build_minute(I,tick[0])
    profile=get_profile(I)
    assert planner.cached(context,profile.profile_id,tick[0])
    from trademind.agents.strategy_planner import StrategyPlanner
    restarted=StrategyPlanner(pipe.llm,pipe.repo,pipe.cfg.t0,pipe.cfg.account_id,lambda:tick[0])
    assert restarted.cached(context,profile.profile_id,tick[0])
    at=tick[0]
    rule=profile.profile_id
    if change=='expiry': at+=timedelta(minutes=21)
    if change=='day': at+=timedelta(days=1)
    if change=='rule': rule='changed'
    if change=='config': pipe.cfg.t0.action_confidence_floor=D('0.9')
    assert restarted.cached(context,rule,at) is None

def test_global_cadence_persists(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    pipe.run_decision(I,tick[0])
    tick[0]+=timedelta(minutes=1)
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    assert pipe.llm.complete.call_count==1 and pipe.jev.session.post.call_count==1
    del pipe._fast_pipeline
    tick[0]+=timedelta(minutes=1)
    pipe.run_decision(I,tick[0])
    assert pipe.jev.session.post.call_count==1


def _cached_fast_context(pipe, tick, iid=I, actionable=True):
    """Seed a valid cached plan through the real planner, outside scheduling."""
    from trademind.agents.strategy_planner import StrategyPlanner
    context=pipe.context_builder.build_minute(I,tick[0]).model_copy(
        update={'instrument_id':iid})
    planner=StrategyPlanner(pipe.llm,pipe.repo,pipe.cfg.t0,pipe.cfg.account_id,lambda:tick[0])
    planner.get(context,get_profile(iid),'seed-'+iid)
    if not actionable:
        row=pipe.repo.get_coalesce(planner.key(iid))
        row['meta']['plan']['entry_conditions']=[dict(field='close',operator='gt',value='9')]
        pipe.repo.save_coalesce(planner.key(iid),tick[0],row['meta'])
    return context


def _fast_runner(pipe, tick, paper_new_entry_filter=False):
    from trademind.agents.fast_pipeline import FastDecisionPipeline
    return FastDecisionPipeline(pipe.llm,pipe.jev,pipe.repo,pipe.cfg.t0,
        pipe.cfg.account_id,pipe.ledger,pipe.calendar,pipe.model_budget,
        lambda:tick[0],lambda:{},paper_new_entry_filter=paper_new_entry_filter)


def test_wait_only_ticks_leave_window_for_short_lived_other_instrument(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    a=_cached_fast_context(pipe,tick,actionable=False)
    b=_cached_fast_context(pipe,tick,'513100.SH')
    fast=_fast_runner(pipe,tick)
    before=pipe.llm.complete.call_count
    for seconds in (0,1,2):
        tick[0]=NOW+timedelta(seconds=seconds)
        intent,meta=fast.run(a,'wait-'+str(seconds))
        assert intent.reason=='no actionable strategy conditions'
        assert not meta['model_path_claimed'] and not meta['jev_called']
        assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id) is None
    tick[0]+=timedelta(seconds=1)
    intent,meta=fast.run(b,'short-window')
    assert intent.action.value=='BUY' and meta['jev_called']
    assert pipe.llm.complete.call_count==before
    assert pipe.jev.session.post.call_count==1
    charged_at=pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']
    # New instance reads persisted cadence; refused ticks do not slide it.
    tick[0]+=timedelta(seconds=1)
    intent,_=_fast_runner(pipe,tick).run(b,'restart')
    assert intent.reason=='global model evaluation cooldown'
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']==charged_at


def test_flash_only_wait_claims_and_old_timestamp_still_blocks(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    original=pipe.llm.complete.side_effect
    def wait_plan(**kw):
        output=original(**kw)
        output.parsed['entry_conditions']=[dict(field='close',operator='gt',value='9')]
        return output
    pipe.llm.complete.side_effect=wait_plan
    result=pipe.run_decision(I,tick[0])
    assert result.intent.reason=='no actionable strategy conditions'
    key='fast-eval:'+pipe.cfg.account_id
    assert pipe.repo.get_coalesce(key)['last_run_at']==NOW
    pipe.jev.session.post.assert_not_called()
    # Legacy rows have only instrument metadata, and must still be honored.
    pipe.repo.save_coalesce(key,NOW,{'instrument':I})
    tick[0]+=timedelta(seconds=299)
    assert pipe.run_decision(I,tick[0]).intent.reason=='global model evaluation cooldown'
    tick[0]+=timedelta(seconds=1)
    assert pipe.run_decision(I,tick[0]).intent.reason=='no actionable strategy conditions'
    assert pipe.repo.get_coalesce(key)['last_run_at']==NOW


def test_refresh_rejected_before_provider_does_not_claim(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    context=_cached_fast_context(pipe,tick,actionable=False)
    fast=_fast_runner(pipe,tick)
    pipe.repo.save_coalesce('fast-refresh:'+pipe.cfg.account_id+':'+I,tick[0],{'requested':True})
    intent,_=fast.run(context,'too-soon-refresh')
    assert intent.reason=='strategy refresh cooldown'
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id) is None
    assert pipe.llm.complete.call_count==1


def test_charge_claim_rechecks_after_concurrent_cached_preflight(tmp_path):
    """Two separate DB connections pass preflight; only one reaches JEV."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from trademind.storage import Database, Repository
    pipe,tick=make_pipe(tmp_path,'shadow')
    context=_cached_fast_context(pipe,tick)
    first,second=_fast_runner(pipe,tick),_fast_runner(pipe,tick)
    other_db=Database(pipe.cfg.storage.url)
    second.repo=Repository(other_db)
    second.planner.repo=second.repo
    second.budget=DailyModelBudget(second.repo,pipe.cfg.t0,pipe.cfg.account_id,lambda:tick[0])
    second.jev=TypeSafeJEVProvider(api_key='test-only',budget=second.budget)
    second.jev.capability_verified=True
    second.jev.session.post=pipe.jev.session.post
    second.decider.jev=second.jev
    barrier=Barrier(2)
    for fast in (first,second):
        original=fast.planner.get
        def get(*args,_get=original,**kwargs):
            plan=_get(*args,**kwargs)
            barrier.wait(timeout=5)
            return plan
        fast.planner.get=get
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda fast:fast.run(context,'race'),(first,second)))
        assert sorted(intent.reason for intent,_ in results)==[
            'global model evaluation cooldown','typed JEV action with valid strategy and evidence']
        assert pipe.jev.session.post.call_count==1
        assert pipe.model_budget.snapshot()['jev']==1
    finally:
        other_db.close()


def test_exhausted_budget_does_not_claim_cached_action(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    context=_cached_fast_context(pipe,tick)
    pipe.model_budget.limit=pipe.model_budget.used
    intent,meta=_fast_runner(pipe,tick).run(context,'exhausted')
    assert intent.reason=='shared model budget exhausted'
    assert not meta['model_path_claimed']
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id) is None
    pipe.jev.session.post.assert_not_called()


@pytest.mark.parametrize('reject',['role_quota','unknown_price','fee_limit'])
@pytest.mark.parametrize('previous',[False,True])
def test_jev_pre_send_reject_preserves_window_and_other_flash(tmp_path,reject,previous):
    pipe,tick=make_pipe(tmp_path,'shadow')
    context=_cached_fast_context(pipe,tick)
    fast=_fast_runner(pipe,tick)
    key='fast-eval:'+pipe.cfg.account_id
    if previous:
        pipe.repo.save_coalesce(key,NOW-timedelta(seconds=301),{'old':'metadata'})
    old=pipe.repo.get_coalesce(key)
    budget=pipe.model_budget
    if reject=='role_quota':
        # Exhaust the role while shared and flash quota remain available.
        budget.reserve_jev(100)
        pipe.cfg.t0.jev_request_limit=budget.snapshot()['jev']
    elif reject=='unknown_price':
        # Pass the cheap global preflight; block at the provider's reservation.
        original_price=budget.jev_cost
        def price(inputs,outputs):
            if inputs>1:
                raise ProviderError('JEV pricing unconfigured; paid request blocked')
            return original_price(inputs,outputs)
        budget.jev_cost=price
    else:
        pipe.cfg.t0.daily_model_cost_limit_cny=D(budget.snapshot()['cost_reserved_cny'])+D('0.01')
        # Real JEV upper bound is too expensive; a small flash request still fits.
        pipe.cfg.t0.jev_input_cny_per_million=D('1000')
    before=budget.snapshot()
    intent,meta=fast.run(context,'pre-send-reject')
    assert intent.action.value=='WAIT'
    assert not meta['model_path_claimed'] and not meta['jev_called']
    assert pipe.repo.get_coalesce(key)==old
    assert budget.snapshot()==before
    pipe.jev.session.post.assert_not_called()

    tick[0]+=timedelta(seconds=1)
    other=pipe.context_builder.build_minute(I,tick[0]).model_copy(update={'instrument_id':'513100.SH'})
    original=pipe.llm.complete.side_effect
    def wait_plan(**kw):
        output=original(**kw)
        output.parsed['entry_conditions']=[dict(field='close',operator='gt',value='9')]
        return output
    pipe.llm.complete.side_effect=wait_plan
    intent,meta=_fast_runner(pipe,tick).run(other,'other-flash')
    assert intent.reason=='no actionable strategy conditions'
    assert meta['model_path_claimed'] and not meta['jev_called']
    assert pipe.repo.get_coalesce(key)['last_run_at']==tick[0]
    assert budget.used==before['used']+1


@pytest.mark.parametrize('reject',['role_quota','unknown_price','fee_limit'])
def test_real_flash_pre_send_reject_does_not_claim(tmp_path,reject):
    pipe,tick=make_pipe(tmp_path,'shadow')
    pipe.llm.complete=DeepSeekProvider.complete.__get__(pipe.llm)
    pipe.llm.session.post=Mock(side_effect=AssertionError('HTTP forbidden'))
    if reject=='role_quota':
        pipe.model_budget.reserve_request(100,100)
        pipe.cfg.t0.strategy_request_limit=pipe.model_budget.snapshot()['strategy']
    elif reject=='unknown_price':
        pipe.cfg.t0.model_input_cny_per_million=D('0')
    else:
        pipe.cfg.t0.daily_model_cost_limit_cny=D('0.000001')
    before=pipe.model_budget.snapshot()
    intent,meta=_fast_runner(pipe,tick).run(pipe.context_builder.build_minute(I,tick[0]),'flash-rejected')
    assert intent.action.value=='WAIT' and not meta['model_path_claimed']
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id) is None
    assert pipe.model_budget.snapshot()==before
    pipe.llm.session.post.assert_not_called()
    pipe.jev.session.post.assert_not_called()


@pytest.mark.parametrize('actionable',[False,True])
def test_real_flash_admission_claims_even_if_later_jev_rejected(tmp_path,actionable):
    import json
    pipe,tick=make_pipe(tmp_path,'shadow')
    pipe.llm.complete=DeepSeekProvider.complete.__get__(pipe.llm)
    draft=strategy()
    if not actionable:
        draft['entry_conditions']=[dict(field='close',operator='gt',value='9')]
    # Exhaust JEV only; an admitted flash must still retain its window.
    pipe.model_budget.reserve_jev(100)
    pipe.cfg.t0.jev_request_limit=pipe.model_budget.snapshot()['jev']
    before=pipe.model_budget.used
    def post(*args,**kwargs):
        assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']==NOW
        return Mock(status_code=200,json=Mock(return_value={
            'choices':[{'message':{'content':json.dumps(draft,ensure_ascii=False)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':100}}))
    pipe.llm.session.post=Mock(side_effect=post)
    context=pipe.context_builder.build_minute(I,tick[0])
    intent,meta=_fast_runner(pipe,tick).run(context,'admitted-flash')
    assert intent.action.value=='WAIT' and meta['model_path_claimed']
    assert not meta['jev_called']
    assert pipe.model_budget.used==before+1
    assert pipe.llm.session.post.call_count==1
    pipe.jev.session.post.assert_not_called()
    tick[0]+=timedelta(seconds=1)
    intent,_=_fast_runner(pipe,tick).run(context,'restart-cooldown')
    assert intent.reason=='global model evaluation cooldown'
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']==NOW


def test_failed_flash_invocation_keeps_claim_and_backoff(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    # Exercise real admission followed by a transport failure, not a mock
    # that throws before the provider can reserve a chargeable request.
    pipe.llm.complete=DeepSeekProvider.complete.__get__(pipe.llm)
    pipe.llm.session.post=Mock(side_effect=__import__('requests').exceptions.ConnectionError())
    context=pipe.context_builder.build_minute(I,tick[0])
    fast=_fast_runner(pipe,tick)
    intent,_=fast.run(context,'failed-flash')
    assert intent.action.value=='WAIT'
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']==NOW
    tick[0]+=timedelta(seconds=1)
    fast.run(context,'backoff')
    assert pipe.llm.session.post.call_count==1
    assert fast.planner.in_failure_backoff(tick[0])


def test_t_plus_one_keeps_legacy_wait_window(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    context=_cached_fast_context(pipe,tick,actionable=False)
    fast=_fast_runner(pipe,tick)
    fast._allow_t_plus_one=True
    intent,_=fast.run(context,'legacy-stock-window')
    assert intent.reason=='no actionable strategy conditions'
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id)['last_run_at']==NOW
    pipe.jev.session.post.assert_not_called()


def test_paper_unreachable_is_observed_and_price_drop_restores_candidate(tmp_path):
    pipe,tick=make_pipe(tmp_path,account_id='paper-t0-local-1k')
    price=['2.01']
    pipe.router.get_minute_bars.side_effect=lambda *a,**kw:(
        [minute(tick[0]-timedelta(minutes=i),price[0]) for i in reversed(range(20))],None)
    result=pipe.run_decision(I,tick[0])
    assert result.intent.reason=='paper new-entry observation only'
    report=result.diagnostics['tradability']['instruments'][0]
    assert report['reason_codes']==['single_exposure_headroom']
    assert report['cash_budget']=='400' and report['max_affordable_lots']==0
    assert pipe.cfg.universe==[I]
    assert pipe.repo.get_coalesce('fast-eval:'+pipe.cfg.account_id) is None
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()
    price[0]='1.50'
    tick[0]+=timedelta(seconds=1)
    restored=pipe.run_decision(I,tick[0])
    assert restored.intent.action.value=='BUY' and restored.guard_verdict=='ALLOW'
    assert restored.order_id and restored.qty==100
    assert restored.diagnostics['tradability']['instruments'][0]['new_entry_candidate']


def test_paper_existing_position_exit_bypasses_new_entry_affordability(tmp_path):
    pipe,tick=make_pipe(tmp_path,account_id='paper-t0-local-1k')
    _apply_fill(pipe,tick[0]-timedelta(minutes=15))
    pipe.ledger.reserve('other-order',D('240'),tick[0])
    original=pipe.llm.complete.side_effect
    def exit_plan(**kw):
        output=original(**kw)
        output.parsed['allowed_actions']=['SELL','HOLD','WAIT']
        return output
    pipe.llm.complete.side_effect=exit_plan
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='SELL'
    assert result.guard_verdict=='ALLOW' and result.order_id
    assert 'tradability' not in result.diagnostics


@pytest.mark.parametrize('mode,account',[('shadow','paper-t0-local-1k'),('paper','fast-test')])
def test_paper_filter_scope_does_not_change_other_accounts_or_modes(tmp_path,mode,account):
    pipe,tick=make_pipe(tmp_path,mode,account_id=account)
    pipe.router.get_minute_bars.side_effect=lambda *a,**kw:(
        [minute(tick[0]-timedelta(minutes=i),'2.01') for i in reversed(range(20))],None)
    result=pipe.run_decision(I,tick[0])
    assert result.intent.reason=='minimum lawful lot unaffordable'
    assert 'tradability' not in result.diagnostics


def test_paper_filter_stale_price_is_unknown_and_not_charged(tmp_path):
    pipe,tick=make_pipe(tmp_path,account_id='paper-t0-local-1k')
    context=pipe.context_builder.build_minute(I,tick[0])
    context.aux['minute_bars'][-1]['time']=(tick[0]-timedelta(seconds=181)).isoformat()
    intent,meta=_fast_runner(pipe,tick,True).run(context,'stale-entry')
    assert intent.reason=='paper new-entry observation only'
    item=meta['diagnostics']['tradability']['instruments'][0]
    assert item['status']=='数据不足' and item['reason_codes']==['price_data_unknown']
    pipe.llm.complete.assert_not_called()


def test_paper_filter_accounts_for_other_holdings_with_original_mark_time(tmp_path):
    from test_fast_architecture_repairs import _apply_buy, _bar_for
    pipe,tick=make_pipe(tmp_path,account_id='paper-t0-local-1k')
    _apply_buy(pipe,'513100.SH',tick[0]-timedelta(minutes=15),price='2')
    def bars(iid,*args,**kwargs):
        at=tick[0]-timedelta(seconds=10 if iid=='513100.SH' else 0)
        return ([_bar_for(iid,at-timedelta(minutes=i),'2' if iid=='513100.SH' else '1.5')
                 for i in reversed(range(20))],None)
    pipe.router.get_minute_bars.side_effect=bars
    result=pipe.run_decision(I,tick[0])
    item=result.diagnostics['tradability']['instruments'][0]
    assert item['reason_codes']==['total_exposure_headroom']
    assert item['total_exposure_room']=='100' and item['total_market_value']=='200'
    other=result.diagnostics['tradability']['instruments'][1]
    assert other['price_time']==(tick[0]-timedelta(seconds=10)).isoformat()
    pipe.llm.complete.assert_not_called()


@pytest.mark.parametrize('case,expected',[
    ('fee_near_miss',['fee_inclusive_cash_shortfall']),
    ('reserved',['fee_inclusive_cash_shortfall','reserved_cash_binding']),
    ('other_buy',['total_exposure_headroom','open_buy_exposure_binding']),
    ('own_sell',['pending_order']),
    ('unknown',['incomplete_valuation_or_order_commitments']),
    ('equality',[]),
])
def test_paper_candidate_filter_constraint_counterexamples(tmp_path,case,expected):
    pipe,tick=make_pipe(tmp_path,account_id='paper-t0-local-1k')
    if case in ('fee_near_miss','equality'):
        pipe.ledger.withdraw(D('250') if case=='fee_near_miss' else D('249.90'),tick[0])
    if case=='reserved':
        pipe.ledger.reserve('cash-freeze',D('250'),tick[0])
    if case in ('other_buy','own_sell','unknown'):
        order={'account_id':pipe.cfg.account_id,'instrument_id':'513100.SH',
               'side':'BUY','quantity':'100','filled_quantity':'0',
               'limit_price':'2.1','status':'ACKNOWLEDGED'}
        if case=='own_sell':
            order.update(instrument_id=I,side='SELL')
        if case=='unknown':
            order['status']='UNKNOWN'
        pipe.repo.open_orders=Mock(return_value=[order])
    result=pipe.run_decision(I,tick[0])
    item=result.diagnostics['tradability']['instruments'][0]
    assert item['reason_codes']==expected
    if expected:
        assert result.intent.reason=='paper new-entry observation only'
        pipe.llm.complete.assert_not_called()
        pipe.jev.session.post.assert_not_called()
    else:
        assert result.intent.action.value=='BUY' and result.guard_verdict=='ALLOW'
        assert result.order_id and result.qty==100

def test_plan_account_version_change_no_order(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    original=pipe.llm.complete.side_effect
    def changed(**kw):
        output=original(**kw)
        pipe.ledger.deposit(D('1'),tick[0])
        return output
    pipe.llm.complete.side_effect=changed
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    pipe.jev.session.post.assert_not_called()

def test_unknown_jev_price_blocks_transport_and_persists_count(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.cfg.t0.jev_input_cny_per_million=None
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    pipe.jev.session.post.assert_not_called()
    assert pipe.model_budget.used==0

def test_budget_role_limits_restart_and_cost(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    budget=pipe.model_budget
    budget.cfg.strategy_request_limit=1
    budget.reserve_request(100,100)
    with pytest.raises(ProviderError):budget.reserve_request(100,100)
    budget.reserve_jev(100)
    assert budget.snapshot()['strategy']==1 and budget.snapshot()['jev']==1
    restored=DailyModelBudget(pipe.repo,pipe.cfg.t0,pipe.cfg.account_id,clock=lambda:tick[0])
    assert restored.used==2
    budget.cfg.daily_model_cost_limit_cny=D('0.0001')
    with pytest.raises(ProviderError):budget.reserve_jev(100)
    assert restored.used==2

@pytest.mark.parametrize('field,value',[('close','NaN'),('unknown','1')])
def test_condition_rejects_nonfinite_and_unknown(field,value):
    with pytest.raises(ValidationError): Condition(field=field,operator='gt',value=value)

def test_official_probability_validation(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.jev.session.post=Mock(return_value=Response({'model':'jev','answers':{'action':{'type':'choice','choice':'BUY','probabilities':{'BUY':0.8,'WAIT':0.8},'confidence':1}},'usage':{'input_tokens':1,'output_tokens':1}}))
    with pytest.raises(ProviderError):pipe.jev.evaluate(state={'fact':1},questions=[{'id':'action','type':'choice','instructions':'Choose','criteria':{'BUY':'buy','WAIT':'wait'}}],trace_id='test')

def test_fast_config_is_explicit_and_no_credentials_loaded():
    cfg=load_config('config/t0-fast.example.toml',load_credentials=False)
    assert cfg.t0.profile=='jev_fast_decision' and cfg.account_id=='paper-t0-fast'
    assert not cfg.llm.api_key and not cfg.jev.api_key
    assert cfg.t0.jev_input_cny_per_million is None


def test_expiry_during_jev_request_blocks_order(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    original=pipe.jev.session.post.side_effect
    def slow(*args,**kwargs):
        response=original(*args,**kwargs)
        tick[0]+=timedelta(seconds=181)
        return response
    pipe.jev.session.post.side_effect=slow
    result=pipe.run_decision(I,NOW)
    assert result.intent.action.value=='WAIT' and not result.order_id
    assert pipe.ledger.snapshot(tick[0]).reserved_cash==0

def test_account_change_during_jev_request_blocks_order(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    original=pipe.jev.session.post.side_effect
    def changed(*args,**kwargs):
        response=original(*args,**kwargs)
        pipe.ledger.deposit(D('1'),tick[0])
        return response
    pipe.jev.session.post.side_effect=changed
    result=pipe.run_decision(I,NOW)
    assert result.intent.action.value=='WAIT' and not result.order_id

def test_low_confidence_requests_bounded_refresh(tmp_path):
    pipe,tick=make_pipe(tmp_path,'shadow')
    original=pipe.jev.session.post.side_effect
    def uncertain(*args,**kwargs):
        response=original(*args,**kwargs)
        response.body['answers']['evidence_sufficient']['noul']=0.5
        return response
    pipe.jev.session.post.side_effect=uncertain
    result=pipe.run_decision(I,NOW)
    assert result.intent.action.value=='WAIT'
    assert pipe.repo.get_coalesce('fast-refresh:'+pipe.cfg.account_id+':'+I)['meta']['requested']
    tick[0]+=timedelta(minutes=1)
    pipe.run_decision(I,tick[0])
    assert pipe.llm.complete.call_count==1
    tick[0]+=timedelta(minutes=4)
    pipe.run_decision(I,tick[0])
    assert pipe.llm.complete.call_count==2 and pipe.jev.session.post.call_count==2

def test_stop_loss_bypasses_unavailable_models(tmp_path):
    from test_t0_runtime_repairs import buy_position
    pipe,tick=make_pipe(tmp_path)
    buy_position(pipe,at=tick[0]-timedelta(minutes=5))
    pipe.jev.capability_verified=False
    pipe.router.get_minute_bars.side_effect=lambda *a,**kw:([minute(tick[0],'1.48')],None)
    result=pipe.run_decision(I,tick[0])
    assert result.portfolio_intent.action.value=='SELL' and result.order_id
    pipe.llm.complete.assert_not_called()
    pipe.jev.session.post.assert_not_called()

def test_concurrent_budget_reservations_cannot_exceed_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    pipe,tick=make_pipe(tmp_path)
    pipe.model_budget.limit=5
    def reserve(_):
        try:pipe.model_budget.reserve_jev(100);return True
        except ProviderError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve,range(20)))==5
    assert pipe.model_budget.used==5

def test_day_rollover_resets_model_budget(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.model_budget.reserve_jev(100)
    tick[0]+=timedelta(days=1)
    assert pipe.model_budget.used==0
    pipe.model_budget.reserve_jev(100)
    assert pipe.model_budget.used==1

@pytest.mark.parametrize('mutation',['type','nan','missing_usage','missing_probs','bad_choice','duplicate_id'])
def test_jev_negative_protocol(mutation):
    provider=TypeSafeJEVProvider(api_key='test-only')
    specs=[{'id':'action','type':'choice','instructions':'Choose','criteria':{'BUY':'buy','WAIT':'wait'}}]
    body={'model':'jev','answers':{'action':{'type':'choice','choice':'BUY','probabilities':{'BUY':1,'WAIT':0},'confidence':1}},'usage':{'input_tokens':1,'output_tokens':1}}
    if mutation=='type':body['answers']['action']['type']='noul'
    if mutation=='nan':body['answers']['action']['probabilities']['BUY']=float('nan')
    if mutation=='missing_usage':body.pop('usage')
    if mutation=='missing_probs':body['answers']['action'].pop('probabilities')
    if mutation=='bad_choice':body['answers']['action']['choice']='SELL'
    if mutation=='duplicate_id':specs+=specs
    provider.session.post=Mock(return_value=Response(body))
    with pytest.raises(ProviderError):provider.evaluate(state={'fact':1},questions=specs,trace_id='test')

def test_output_free_jev_pricing_supported(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    pipe.cfg.t0.jev_output_cny_per_million=D('0')
    assert pipe.model_budget.jev_cost(100,100)==D('0.0001')

def test_unknown_prices_preflight_does_not_read_keys(capsys,monkeypatch):
    import trademind.runner as runner
    monkeypatch.setattr(runner,'_config_path','config/t0-fast.example.toml')
    from unittest.mock import patch
    with patch('trademind.config._apply_env',side_effect=AssertionError('must not read credentials')):
        assert runner.cmd_t0_fast_check()==2
    assert '"credentials_loaded": false' in capsys.readouterr().out


def test_max_holding_deadline_persisted_and_exits_without_models(tmp_path):
    pipe,tick=make_pipe(tmp_path)
    buy=pipe.run_decision(I,tick[0]);assert buy.order_id
    tick[0]+=timedelta(minutes=1);assert pipe.poll_fills(tick[0])
    marker=pipe.repo.get_coalesce('fast-holding:'+pipe.cfg.account_id+':'+I)
    assert marker['meta']['deadline']
    tick[0]+=timedelta(minutes=5)
    pipe.jev.capability_verified=False
    result=pipe.run_decision(I,tick[0])
    assert result.portfolio_intent.action.value=='SELL' and result.order_id
    assert pipe.jev.session.post.call_count==1


def test_jev_pricing_from_toml_uses_decimals(tmp_path):
    text=__import__('pathlib').Path('config/t0-fast.example.toml').read_text(encoding='utf-8')
    text=text.replace('[t0]','[t0]\njev_input_cny_per_million = "0.3"\njev_output_cny_per_million = "0"')
    path=tmp_path/'prices.toml';path.write_text(text,encoding='utf-8')
    cfg=load_config(path,load_credentials=False)
    assert cfg.t0.jev_input_cny_per_million==D('0.3')
    assert cfg.t0.jev_output_cny_per_million==D('0')


# -- TASK-20261016-08: buy sizing reserves fees; Guard never sizes (Issue16-05) --

def test_buy_quantity_reserves_fees_guard_allows(tmp_path):
    """Issue16-05 repro, fixed: cash 1000/reserve 100/exposure caps 900 at price
    1.5 used to emit target 0.9 → Guard VETO min_cash_reserve_violation; now the
    chain finds the largest lawful lot-multiple and Guard allows the order."""
    pipe,tick=make_pipe(tmp_path)
    pipe.ledger.deposit(D('500'),tick[0]-timedelta(minutes=20))  # cash → 1000
    pipe.cfg.t0.max_single_exposure=D('900')
    pipe.cfg.t0.max_total_exposure=D('900')
    pipe.router.get_minute_bars=Mock(side_effect=lambda *a,**kw:(
        [minute(tick[0]-timedelta(minutes=i),'1.50') for i in reversed(range(20))],None))
    result=pipe.run_decision(I,tick[0])
    assert result.error is None and result.intent.action.value=='BUY'
    # 6 lots = 900 notional + 0.109 fees > 900 cash budget → 5 lots = 750 + 0.1075 ≤ 900
    assert result.qty==500 and result.intent.target_weight==D('0.75')
    assert result.guard_verdict=='ALLOW' and result.order_id


def test_buy_exposure_cap_not_fee_shrunk(tmp_path):
    """A buy that fits the market-value cap exactly stays lawful: fees come out
    of the cash budget only (Guard's own exposure check compares market value)."""
    pipe,tick=make_pipe(tmp_path)
    pipe.ledger.deposit(D('1500'),tick[0]-timedelta(minutes=20))  # cash → 2000
    pipe.cfg.t0.max_single_exposure=D('150')   # exactly 1 lot at 1.5
    pipe.cfg.t0.max_total_exposure=D('300')
    pipe.router.get_minute_bars=Mock(side_effect=lambda *a,**kw:(
        [minute(tick[0]-timedelta(minutes=i),'1.50') for i in reversed(range(20))],None))
    result=pipe.run_decision(I,tick[0])
    assert result.error is None and result.intent.action.value=='BUY'
    assert result.qty==100  # market cap 150 honoured; fee paid from cash budget
    assert result.guard_verdict=='ALLOW' and result.order_id


def test_buy_waits_when_no_lawful_lot(tmp_path):
    """Below one lawful lot (cash budget cannot cover notional+fees) the chain
    WAITs explicitly instead of feeding Guard a doomed quantity."""
    pipe,tick=make_pipe(tmp_path)
    pipe.cfg.t0.min_cash_reserve=D('450')  # cash cap = 500-0-450 = 50 < 1 lot cost
    pipe.router.get_minute_bars=Mock(side_effect=lambda *a,**kw:(
        [minute(tick[0]-timedelta(minutes=i),'1.50') for i in reversed(range(20))],None))
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    # the one-lot pre-check rejects first: 150+fees > cash budget 50
    assert result.intent.reason=='minimum lawful lot unaffordable'
    assert result.order_id is None


def _apply_fill(pipe,at,price='1.50'):
    """A real 100-share fill so the next decision sees an existing position
    (local copy of test_fast_architecture_repairs._apply_buy)."""
    oid=new_id('o')
    order=Order(id=oid,trace_id='test',created_at=at,order_id=oid,client_order_id=oid,
        intent_id='test',guard_id='test',account_id=pipe.cfg.account_id,instrument_id=I,
        mode=RunMode.PAPER,side=OrderSide.BUY,quantity=D('100'),limit_price=D(price),
        updated_at=at,status=OrderStatus.ACKNOWLEDGED)
    fill=Fill(id=new_id('f'),trace_id='test',created_at=at,fill_id=new_id('fill'),
        provider_fill_id=new_id('pf'),order_id=oid,fee=D('0.1'),quantity=D('100'),
        price=D(price),executed_at=at,available_at=at)
    pipe.ledger.apply_fill(fill,order,at)


def test_buy_scale_in_with_position_targets_cumulative_value(tmp_path):
    """Task17-03 repro, fixed (Issue16-05): holding 100@1.50 (mv 150) with
    single cap 300 / total cap 450 and equity 499.90 / cash 349.90, the sizer
    used to emit target==cw and WAIT 'no lawful increase size'. The target is
    the cumulative weight now, so the same account BUYs 1 lot and Guard allows
    it — scale-in is no longer blocked by an existing position."""
    pipe,tick=make_pipe(tmp_path)
    _apply_fill(pipe,tick[0]-timedelta(minutes=15))  # cash 349.90, equity 499.90
    pipe.cfg.t0.max_single_exposure=D('300')
    pipe.cfg.t0.max_total_exposure=D('450')
    result=pipe.run_decision(I,tick[0])
    assert result.error is None and result.intent.action.value=='BUY'
    assert result.intent.reason!='no lawful increase size'
    assert result.intent.target_weight>result.intent.current_weight
    assert result.qty==100 and result.guard_verdict=='ALLOW' and result.order_id


def test_buy_scale_in_waits_when_cash_budget_cannot_cover_lot(tmp_path):
    """With a position, no lawful increment (free cash below one lot's
    notional+fees) WAITs explicitly instead of feeding Guard a doomed target."""
    pipe,tick=make_pipe(tmp_path)
    _apply_fill(pipe,tick[0]-timedelta(minutes=15))
    pipe.cfg.t0.max_single_exposure=D('900')
    pipe.cfg.t0.max_total_exposure=D('900')
    pipe.cfg.t0.min_cash_reserve=D('300')  # cash cap 49.90 < 150 + fees
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    assert result.intent.reason=='no lawful increase size'
    assert result.order_id is None


def test_buy_scale_in_waits_when_single_cap_exhausted(tmp_path):
    """Position already at the single-instrument cap leaves no lawful
    increment: explicit WAIT, no order."""
    pipe,tick=make_pipe(tmp_path)
    _apply_fill(pipe,tick[0]-timedelta(minutes=16))
    _apply_fill(pipe,tick[0]-timedelta(minutes=15))  # 200@1.50 → mv 300 = cap
    pipe.cfg.t0.max_single_exposure=D('300')
    pipe.cfg.t0.max_total_exposure=D('450')
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='WAIT'
    assert result.intent.reason=='no lawful increase size'
    assert result.order_id is None


def test_buy_scale_in_steps_down_inside_cap_at_exact_boundary(tmp_path):
    """Cap-binding boundary (existing 150 mv, headroom exactly 5 lots under
    900/900 with ample cash): the sizer must land strictly inside Guard's cap
    weight — never replan_zero_lot_quantity nor max_single_exposure_limit —
    stepping down a lot when 28-digit rounding would sit on the boundary."""
    pipe,tick=make_pipe(tmp_path)
    _apply_fill(pipe,tick[0]-timedelta(minutes=15))          # mv 150, equity 499.90
    pipe.ledger.deposit(D('1500'),tick[0]-timedelta(minutes=14))  # equity 1999.90
    pipe.cfg.t0.max_single_exposure=D('900')
    pipe.cfg.t0.max_total_exposure=D('900')
    result=pipe.run_decision(I,tick[0])
    assert result.intent.action.value=='BUY'
    assert result.guard_verdict=='ALLOW' and result.order_id
    assert result.intent.target_weight<=D('900')/D('1999.90')
    assert D('150')+result.qty*D('1.50')<=D('900')  # cumulative value inside cap


# -- TASK-20261025-03: BUY plans must carry SELL + exits (ISSUE-20261025-01) --

def test_buy_without_sell_draft_waits_without_order_or_freeze(tmp_path):
    """Issue25-01 repro at the planner boundary: a BUY-capable draft without
    SELL is rejected before caching or JEV; WAIT with categorical reason, no
    order, no freeze, and the failure feeds the existing health backoff."""
    pipe, tick = make_pipe(tmp_path)
    original = pipe.llm.complete.side_effect
    def buy_only(**kw):
        out = original(**kw)
        out.parsed['allowed_actions'] = ['BUY', 'HOLD', 'WAIT']
        return out
    pipe.llm.complete.side_effect = buy_only
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'WAIT' and result.order_id is None
    assert result.intent.reason == 'invalid strategy contract'
    assert pipe.ledger.snapshot(tick[0]).reserved_cash == 0
    assert not pipe.repo.open_orders()
    pipe.jev.session.post.assert_not_called()
    health = pipe._fast_pipeline.planner.health_state(tick[0])
    assert health['consecutive_failures'] == 1
    assert health['last_failure_category'] == 'invalid_strategy_contract'


def test_exit_hit_offers_sell_without_plan_mutation(tmp_path):
    """With the fixed contract the cached plan already carries SELL, so an
    exit-condition hit offers SELL directly (no cached-plan surgery needed)."""
    pipe, tick = make_pipe(tmp_path)
    assert pipe.run_decision(I, tick[0]).order_id
    tick[0] += timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    tick[0] += timedelta(minutes=4)
    original = pipe.jev.session.post.side_effect
    def sell(url, json, timeout):
        offered = json['state']['allowed_actions']
        assert 'SELL' in offered and 'BUY' in offered
        response = original(url, json, timeout)
        options = list(response.body['answers']['action']['probabilities'])
        response.body['answers']['action'].update(
            choice='SELL', probabilities={k: int(k == 'SELL') for k in options})
        return response
    pipe.jev.session.post.side_effect = sell
    result = pipe.run_decision(I, tick[0])
    assert result.intent.action.value == 'SELL' and result.order_id


def test_legacy_buy_only_cached_plan_rejected_and_recorded(tmp_path):
    """A pre-contract BUY-only plan in coalesce storage is refused like a cache
    miss, recorded once with a categorical reason, and the row is not rewritten."""
    pipe, tick = make_pipe(tmp_path)
    pipe.run_decision(I, tick[0])
    planner = pipe._fast_pipeline.planner
    row = pipe.repo.get_coalesce(planner.key(I))
    row['meta']['plan']['allowed_actions'] = ['BUY', 'HOLD', 'WAIT']
    pipe.repo.save_coalesce(planner.key(I), tick[0], row['meta'])
    context = pipe.context_builder.build_minute(I, tick[0])
    assert planner.cached(context, get_profile(I).profile_id, tick[0]) is None
    health = planner.health_state(tick[0])
    assert health['last_cache_reject_category'] == 'invalid_cached_plan'
    assert 'SELL' in health['last_cache_reject_reason']
