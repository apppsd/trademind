"""Network-free replay of archived intents on identical visible minutes.

No current model is called to synthesize past decisions. Missing or mismatched
archives remain unavailable; paper accounts use the existing Guard/OMS/ledger.
"""
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import AppConfig, _apply
from dataclasses import asdict
from ..domain.context import ContextSnapshot
from ..domain.base import new_id
from ..domain.enums import TradeAction, ConfidenceSemantics
from ..domain.intents import TradeIntent
from ..domain.strategy import StrategyPlan
from ..market.profiles import get_profile
from ..market.base import BEIJING
from ..pipeline import Pipeline
from .prompt_paper import recorded_bars


def facts_hash(context):
    facts={'instrument':context.instrument_id,'as_of':context.as_of.isoformat(),
           'features':context.features.model_dump(mode='json'),'bars':context.aux.get('minute_bars',[])}
    # Identity metadata in features does not change market facts.
    facts['features']={k:v for k,v in facts['features'].items() if k in ('values','window','missing_fields','quality','feature_version')}
    return hashlib.sha256(json.dumps(facts,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def percentile(values,p):
    if not values:return None
    values=sorted(values)
    import math
    return values[max(0,math.ceil(len(values)*p)-1)]

class ArchivedChain:
    def __init__(self,pipe,archives,rule=False,completion_clock=None):
        self.pipe,self.archives,self.rule=pipe,archives,rule
        self.missing=[]
        self.calls=[]
        self.completion_clock=completion_clock
        self.end_to_end=[]
        self.last_eval=None

    def run(self,context,trace_id=None):
        now=context.as_of
        if self.pipe.cfg.t0.profile=='jev_fast_decision':
            if self.last_eval and (now-self.last_eval).total_seconds()<self.pipe.cfg.t0.fast_eval_seconds:
                return self._wait(context,trace_id,'archived fast evaluation cooldown')
            self.last_eval=now
        cw=Decimal(context.portfolio.positions.get(context.instrument_id,{}).get('weight','0'))
        action,target,confidence,reason=TradeAction.WAIT,Decimal('0'),Decimal('0'),'missing archived decision'
        archived=self.archives.get(context.context_id)
        deadline=now+timedelta(seconds=180)
        horizon=300
        if self.rule:
            signal=context.features.values.get('ret_5m')
            if signal is not None:
                if signal>0 and cw==0:
                    action,target=TradeAction.BUY,min(Decimal('1'),Decimal('200')/context.portfolio.equity)
                elif signal<0 and cw>0:
                    action=TradeAction.SELL
                elif cw>0:
                    action,target=TradeAction.HOLD,cw
            reason='deterministic ret_5m baseline, no model'
        elif archived:
            available=datetime.fromisoformat(archived['available_at'])
            expected={'cash':str(context.portfolio.cash),'reserved_cash':str(context.portfolio.reserved_cash),
                      'positions':context.portfolio.positions}
            if available.tzinfo is None or available<now or (available>now and 'valid_until' not in archived):
                raise ValueError('archive unavailable at replay decision time')
            deadline=min(context.expires_at,datetime.fromisoformat(archived.get('valid_until',deadline.isoformat())))
            if deadline.tzinfo is None or available>=deadline or (available-now).total_seconds()>180:
                raise ValueError('archive completion outside validity window')
            horizon=int(archived.get('holding_horizon_seconds',300))
            if archived['facts_hash']!=facts_hash(context):
                raise ValueError('archive market facts hash mismatch')
            if self.pipe.cfg.t0.profile=='jev_fast_decision' and archived.get('strategy_plan'):
                plan=StrategyPlan.model_validate(archived['strategy_plan'])
                if (not plan.valid_at(available,context.instrument_id,get_profile(context.instrument_id).profile_id)
                    or any(c.matches(context.features.values) for c in plan.invalidation_conditions)):
                    raise ValueError('archived strategy invalid at completion')
                deadline=min(deadline,plan.valid_until)
                horizon=plan.holding_horizon_seconds
            if archived.get('account')!=expected:
                self.missing.append({'context_id':context.context_id,'reason':'account_state_mismatch'})
                reason='archive account mismatch'
            else:
                if 'model_calls' not in archived or (archived['action'] in ('BUY','SELL') and not archived['model_calls']):
                    raise ValueError('actionable archive missing model-call provenance')
                action=TradeAction(archived['action'])
                target=Decimal(str(archived['target_weight']))
                confidence=Decimal(str(archived.get('confidence','0')))
                reason='archived intent replay, no current model calls'
                self.calls.extend(archived.get('model_calls',[]))
                self.end_to_end.append((available-now).total_seconds()*1000)
                if self.completion_clock:
                    self.completion_clock(available)
        else:
            self.missing.append({'context_id':context.context_id,'reason':'archive_missing'})
        intent=TradeIntent(id=new_id('ti'),trace_id=trace_id or new_id('tr'),created_at=now,
            decision_id=new_id('replay'),context_id=context.context_id,thesis_id='archived-or-rule',
            instrument_id=context.instrument_id,portfolio_version=context.portfolio.portfolio_version,
            account_id=context.portfolio.account_id,action=action,target_weight=target,current_weight=cw,
            confidence=confidence,confidence_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
            holding_horizon_seconds=horizon if action==TradeAction.BUY else 0,evidence_ids=context.evidence_ids,
            reason=reason,valid_until=deadline,model='archived' if not self.rule else 'rule')
        return intent,{'replay':True,'rule':self.rule,'current_model_calls':0}

    def _wait(self,context,trace_id,reason):
        intent=TradeIntent(id=new_id('ti'),trace_id=trace_id or new_id('tr'),created_at=context.as_of,
            decision_id=new_id('replay'),context_id=context.context_id,thesis_id='archive-cooldown',
            instrument_id=context.instrument_id,portfolio_version=context.portfolio.portfolio_version,
            account_id=context.portfolio.account_id,action=TradeAction.WAIT,target_weight=Decimal('0'),
            confidence=Decimal('0'),confidence_semantics=ConfidenceSemantics.SELF_REPORTED_ANSWER_CONFIDENCE,
            holding_horizon_seconds=0,evidence_ids=context.evidence_ids,reason=reason,valid_until=context.expires_at)
        return intent,{'replay':True,'current_model_calls':0}


def compare_archives(contexts,archives,run_id='fast-comparison',artifact_kind='recorded',t0_configs=None,runtime_configs=None):
    if artifact_kind not in ('recorded','fixture'):
        raise ValueError('unknown artifact kind')
    if not contexts or any(a.as_of>=b.as_of for a,b in zip(contexts,contexts[1:])):
        raise ValueError('nonempty strictly chronological contexts required')
    if any(c.features.as_of != c.as_of for c in contexts):
        raise ValueError('feature time mismatch')
    for c in contexts:
        for row in c.aux.get('minute_bars',[]):
            if 'fetched_at' in row and datetime.fromisoformat(row['fetched_at']) > c.as_of:
                raise ValueError('future fetched minute')
    bars=recorded_bars(contexts)
    if not bars:
        raise ValueError('complete visible raw minutes required')
    results={}
    # TASK-20261013-07 (ISSUE-20261012-04): the replay fund is the configured
    # t0.initial_cash (default 500 keeps old archives compatible; Pipeline
    # syncs paper<-t0 at construction). Comparability gates stay and are
    # checked before any replay account exists: positive and finite, equal to
    # the recorded paper fund when one is present, and equal across the A/B
    # pair — different funds are never mixed into one comparison.
    def _pair_cash(label):
        cfg = AppConfig(); cfg.t0.enabled = True
        runtime = (runtime_configs or {}).get(label)
        if runtime:
            if set(runtime) - {'t0', 'risk', 'paper'}:
                raise ValueError('replay runtime config only accepts t0/risk/paper')
            for section, values in runtime.items():
                _apply(getattr(cfg, section), {k: v for k, v in values.items() if v is not None})
        if t0_configs and label in t0_configs:
            _apply(cfg.t0, {k: v for k, v in t0_configs[label].items() if v is not None})
            cfg.t0.enabled = True
        cash = cfg.t0.initial_cash
        if not cash.is_finite() or cash <= 0:
            raise ValueError('comparison requires a positive finite initial cash')
        recorded_paper = (runtime or {}).get('paper', {}).get('initial_cash')
        if recorded_paper is not None and Decimal(str(recorded_paper)) != cash:
            raise ValueError('comparison requires equal t0/paper initial cash')
        return cash
    pair_cash = {}
    for label in ('old', 'new'):
        pair_cash[label] = _pair_cash(label)
    if 'old' in pair_cash and 'new' in pair_cash and pair_cash['old'] != pair_cash['new']:
        raise ValueError('A/B comparison requires equal initial cash across archives')
    with TemporaryDirectory() as temp:
        for label in ('old','new','rules'):
            cfg=AppConfig();cfg.mode='replay';cfg.t0.enabled=True
            runtime=(runtime_configs or {}).get(label)
            if runtime:
                for section, values in runtime.items():
                    _apply(getattr(cfg,section),{k:v for k,v in values.items() if v is not None})
            if t0_configs and label in t0_configs:
                _apply(cfg.t0,{k:v for k,v in t0_configs[label].items() if v is not None})
                cfg.t0.enabled=True
            if label=='new':
                cfg.t0.profile='jev_fast_decision'
            base = pair_cash.get(label, cfg.t0.initial_cash)
            if label == 'rules' and (pair_cash.get('old') or pair_cash.get('new')):
                base = pair_cash.get('new', pair_cash.get('old'))
                cfg.t0.initial_cash = base  # keep the no-model baseline comparable
            cfg.universe=sorted({c.instrument_id for c in contexts})
            if not set(cfg.universe)<=set(cfg.t0.whitelist):
                raise ValueError('comparison cannot expand verified ETF whitelist')
            cfg.account_id=run_id+'-'+label;cfg.storage.url='sqlite:///'+str(Path(temp)/(label+'.db'))
            cfg.data.provider_pref=['fixture'];cfg.llm.provider='stub'
            if not runtime and not (t0_configs and label in t0_configs):
                cfg.t0.candidate_cooldown_seconds=0
            tick=[contexts[0].as_of]
            pipe=Pipeline(cfg,clock=lambda:tick[0]);pipe.ensure_account(at=tick[0])
            chain=ArchivedChain(pipe,archives.get(label,{}),rule=label=='rules',completion_clock=lambda at:tick.__setitem__(0,at))
            if label != 'rules':
                for context in contexts:
                    if context.context_id not in archives.get(label, {}):
                        chain.missing.append({'context_id': context.context_id, 'reason': 'archive_missing'})
            if label=='new':
                pipe._fast_pipeline=chain
                if artifact_kind=='recorded':
                    for context_id, row in archives.get(label,{}).items():
                        if row.get('action') in ('BUY','SELL') and not all(k in row for k in ('valid_until','holding_horizon_seconds','strategy_plan')):
                            chain.missing.append({'context_id':context_id,'reason':'fast_execution_provenance_incomplete'})
            else:
                pipe._dual_model_pipeline=chain
            if label!='rules' and artifact_kind=='recorded' and not (runtime and all(k in runtime for k in ('t0','risk','paper'))):
                chain.missing.append({'reason':'runtime_config_unrecorded'})
            pipe.router.get_minute_bars=lambda iid,start,end,**kw:([b for b in bars if b.instrument_id==iid and start<=b.bar_time<=end and b.available_at<=end],None)
            actions=Counter();errors=[];peak=base;drawdown=Decimal('0');prices={}
            for source in contexts:
                if tick[0]>source.as_of:
                    errors.append('archive completion overlaps next context')
                    continue
                tick[0]=source.as_of
                pipe.poll_fills(tick[0])
                for iid in cfg.universe:
                    visible=[b for b in bars if b.instrument_id==iid and b.available_at<=tick[0] and b.bar_time<=tick[0]]
                    if visible:prices[iid]=visible[-1].close
                pipe.on_new_trading_day(tick[0].astimezone(BEIJING).date(), prices)
                rebuilt=pipe.context_builder.build_minute(source.instrument_id,tick[0],max_stale_seconds=180)
                # Preserve supplied visible features/provenance with a fresh independent account view.
                ctx=source.model_copy(deep=True);ctx.portfolio=rebuilt.portfolio
                pipe.context_builder.build_minute=lambda *a,**kw:ctx
                r=pipe.run_decision(source.instrument_id,tick[0])
                # Restore actual builder for the next account snapshot.
                from ..context import ContextBuilder
                pipe.context_builder=ContextBuilder(pipe.router,pipe.calendar,pipe.ledger,ttl_seconds=cfg.risk.context_ttl_seconds)
                actions[r.intent.action.value if r.intent else r.portfolio_intent.action.value if r.portfolio_intent else 'NO_INTENT']+=1
                if r.error:errors.append(r.error)
                equity=pipe.ledger.snapshot(tick[0],prices).equity
                peak=max(peak,equity);drawdown=max(drawdown,(peak-equity)/peak)
            snap=pipe.ledger.snapshot(tick[0],prices)
            known=all(c.get('cost_amount') is not None and c.get('cost_currency')=='CNY' and c.get('cost_source') not in (None,'unmeasured','operator_price_request_upper_bound','operator_price_request_reservation','mixed_usage_and_reservation') for c in chain.calls)
            # Missing archives imply unknown total costs, even if no archived calls were supplied.
            known=known and not chain.missing
            costs=sum((Decimal(str(c['cost_amount'])) for c in chain.calls),Decimal('0')) if known else None
            if label=='rules':costs=Decimal('0')
            events=pipe.repo.account_events(cfg.account_id)
            fees=sum((Decimal(part.split('=',1)[1]) for e in events if e['kind'] in ('fill_buy','fill_sell') for part in e['note'].split(';') if part.startswith('fee=')),Decimal('0'))
            latencies=[c['latency_ms'] for c in chain.calls if isinstance(c.get('latency_ms'),int)]
            results[label]={'account_id':cfg.account_id,'initial_cash':str(base),'cash':str(snap.cash),'equity':str(snap.equity),
                'trading_net':str(snap.equity-base),'model_cost_cny':str(costs) if costs is not None else None,
                'net_after_model_cost':str(snap.equity-base-costs) if costs is not None else None,
                'transaction_fees':str(fees),'max_drawdown':str(drawdown),'open_positions':len(snap.positions),
                'open_orders':len(pipe.repo.open_orders()),'actions':dict(actions),'errors':errors,'archive_gaps':chain.missing,
                'archived_model_calls':len(chain.calls),'p50_ms':percentile(latencies,.5),'p95_ms':percentile(latencies,.95),
                'profile':cfg.t0.profile,'t0_config':json.loads(json.dumps(asdict(cfg.t0),default=str)),
                'runtime_config':json.loads(json.dumps({k:asdict(getattr(cfg,k)) for k in ('t0','risk','paper')},default=str)),
                'end_to_end_p50_ms':percentile(chain.end_to_end,.5),'end_to_end_p95_ms':percentile(chain.end_to_end,.95)}
            pipe.release_writer_lease()
            pipe.db.close()
    return {'run_id':run_id,'artifact_kind':artifact_kind,'current_model_calls':0,
        'facts_hashes':[facts_hash(c) for c in contexts], 'chronological_window':[contexts[0].as_of.isoformat(),contexts[-1].as_of.isoformat()],
        'status':'incomplete_archives' if any(results[k]['archive_gaps'] or results[k]['errors'] for k in ('old','new')) else 'archived_intents_replayed',
        'calibration':'unavailable_without_defined_labels_and_held_out_samples',
        'limitations':['Archived intents are replayed, not re-generated by current models.','No verified L1; next-visible-minute fills with configured slippage.','No live performance or profitability inference.'], 'accounts':results}


def compare_bundle_file(path):
    bundle=json.loads(Path(path).read_text(encoding='utf-8'))
    if bundle.get('schema_version', 1) not in (1, 2):
        raise ValueError('unsupported archive schema version')
    if not bundle.get('contexts'):
        return {'run_id': bundle.get('run_id'), 'status': 'incomplete_archives', 'current_model_calls': 0,
                'accounts': {}, 'archive_gaps': bundle.get('gaps', []),
                'calibration': 'unavailable_without_recorded_contexts'}
    return compare_archives([ContextSnapshot.model_validate(c) for c in bundle['contexts']],bundle.get('archives',{}),
                            bundle.get('run_id','fast-comparison'),bundle.get('artifact_kind','recorded'),bundle.get('t0_configs'),bundle.get('runtime_configs'))
