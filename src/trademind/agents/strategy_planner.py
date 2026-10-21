"""Low-frequency flash planner, validated plans persist in existing coalesce storage."""
import json
import re
from datetime import datetime, timedelta
from pydantic import ValidationError
from ..domain.strategy import StrategyPlan
from ..domain.base import new_id, utcnow
from ..errors import ProviderError
from ..market.base import BEIJING
from ..domain.strategy import FEATURE_FIELDS
from .fast_prompts import STRATEGY_PROMPT, STRATEGY_PROMPT_VERSION, STRATEGY_OUTPUT_FIELDS, strategy_output_schema

PROMPT = STRATEGY_PROMPT


class StrategyPlanner:
    def __init__(self, llm, repo, cfg, account_id, clock=utcnow):
        self.llm,self.repo,self.cfg,self.account_id,self.clock=llm,repo,cfg,account_id,clock

    def key(self,iid):
        return 'strategy-plan:'+self.account_id+':'+iid

    def health_key(self):
        return 'strategy-health:'+self.account_id

    def backoff_delay_seconds(self, consecutive_failures):
        if consecutive_failures <= 0:
            return 0
        base = max(1, int(getattr(self.cfg, 'uncertain_refresh_seconds', 300)))
        cap = max(base, 1800)
        return min(cap, base * (2 ** min(consecutive_failures - 1, 4)))

    def health_state(self, now=None):
        now = now or self.clock()
        day = now.astimezone(BEIJING).date().isoformat()
        row = self.repo.get_coalesce(self.health_key())
        meta = dict(row['meta']) if row and isinstance(row.get('meta'), dict) else {}
        if meta.get('day') != day or meta.get('account_id') != self.account_id:
            return {
                'day': day,
                'account_id': self.account_id,
                'consecutive_failures': 0,
                'last_success_at': None,
                'last_plan_id': None,
                'last_failure_at': None,
                'last_failure_category': None,
                'last_failure_reason': None,
                'backoff_seconds': 0,
                'next_allowed_request_at': None,
            }
        meta.setdefault('consecutive_failures', 0)
        meta.setdefault('last_success_at', None)
        meta.setdefault('last_plan_id', None)
        meta.setdefault('last_failure_at', None)
        meta.setdefault('last_failure_category', None)
        meta.setdefault('last_failure_reason', None)
        meta.setdefault('backoff_seconds', 0)
        meta.setdefault('next_allowed_request_at', None)
        return meta

    def in_failure_backoff(self, now=None):
        now = now or self.clock()
        state = self.health_state(now)
        next_at = state.get('next_allowed_request_at')
        if int(state.get('consecutive_failures') or 0) <= 0 or not next_at:
            return False
        try:
            return now < datetime.fromisoformat(next_at)
        except ValueError:
            return False

    def _record_model_failure(self, now, trace_id, exc):
        from ..evaluation.fast_archives import classify_failure
        from ..execution.paper_run import sanitize_evidence
        state = self.health_state(now)
        last_call = getattr(self.llm, 'last_call', None)
        call_matches = last_call is not None and getattr(last_call, 'trace_id', None) == trace_id
        msg = str(exc)
        if msg == 'invalid strategy JSON':
            cat = 'invalid_strategy_json'
        elif msg == 'invalid strategy Chinese text':
            cat = 'invalid_strategy_chinese_text'
        elif msg == 'invalid strategy contract':
            cat = 'invalid_strategy_contract'
        elif call_matches and getattr(last_call, 'failure_category', None) and last_call.failure_category != 'none':
            cat = last_call.failure_category
        else:
            call_dict = last_call.model_dump(mode='json') if call_matches and hasattr(last_call, 'model_dump') else None
            cat = classify_failure(msg, call_dict)[0]
        if call_matches and getattr(last_call, 'failure_category', None) in (None, 'none'):
            last_call.failure_category = cat
            last_call.error = sanitize_evidence(msg, 160)
            if hasattr(self.llm, '_audit'):
                self.llm._audit(last_call)
        consecutive = int(state.get('consecutive_failures') or 0) + 1
        delay = self.backoff_delay_seconds(consecutive)
        next_at = (now + timedelta(seconds=delay)).isoformat()
        state.update({
            'consecutive_failures': consecutive,
            'last_failure_at': now.isoformat(),
            'last_failure_category': cat,
            'last_failure_reason': sanitize_evidence(msg, 160),
            'backoff_seconds': delay,
            'next_allowed_request_at': next_at,
        })
        self.repo.save_coalesce(self.health_key(), now, state)

    def _record_model_success(self, available, plan):
        state = self.health_state(available)
        state.update({
            'consecutive_failures': 0,
            'last_success_at': available.isoformat(),
            'last_plan_id': plan.plan_id,
            'backoff_seconds': 0,
            'next_allowed_request_at': (available + timedelta(seconds=self.cfg.uncertain_refresh_seconds)).isoformat(),
        })
        self.repo.save_coalesce(self.health_key(), available, state)

    def cached(self, context, rule_version, now):
        row=self.repo.get_coalesce(self.key(context.instrument_id))
        if not row or 'plan' not in row['meta']:
            return None
        try:
            plan=StrategyPlan.model_validate(row['meta']['plan'])
        except ValidationError:
            return None
        fingerprint=self.cfg.__dict__.copy()
        fingerprint={k:str(v) for k,v in fingerprint.items()}
        if row['meta'].get('config')!=fingerprint or plan.prompt_version!=STRATEGY_PROMPT_VERSION or plan.data_cutoff > context.as_of or any(c.matches(context.features.values) for c in plan.invalidation_conditions):
            return None
        return plan if plan.valid_at(now,context.instrument_id,rule_version) else None

    def get(self, context, profile, trace_id, refresh=False):
        now=self.clock()
        plan=self.cached(context,profile.profile_id,now)
        attempt_key=self.key(context.instrument_id)+':attempt'
        attempt=self.repo.get_coalesce(attempt_key)
        since=(now-attempt['last_run_at']).total_seconds() if attempt else float('inf')
        if plan and not refresh and (now-plan.created_at).total_seconds()<self.cfg.strategy_refresh_seconds:
            return plan
        if self.in_failure_backoff(now):
            if plan and not refresh:
                return plan
            raise ProviderError('strategy model failure backoff')
        if since<self.cfg.uncertain_refresh_seconds:
            if plan and not refresh:
                return plan
            raise ProviderError('strategy refresh cooldown')
        if getattr(self.llm,'model','')!='deepseek-flash':
            raise ProviderError('strategy requires deepseek-flash')
        self.repo.save_coalesce(attempt_key,now)
        close_at=now.astimezone(BEIJING).replace(hour=15,minute=0,second=0,microsecond=0)
        maximum_horizon=min(14400,int((close_at-now).total_seconds()))
        facts={'instrument_id':context.instrument_id,'as_of':context.as_of.isoformat(),
               'features':context.features.model_dump(mode='json'),
               'minute_bars':context.aux.get('minute_bars',[]),
               'portfolio':context.portfolio.model_dump(mode='json') if context.portfolio else None,
               'output_schema':strategy_output_schema(),
               'condition_contract':{'available_fields':sorted(FEATURE_FIELDS & context.features.values.keys()),
                   'entry_match':'all','exit_match':'any','invalidation_match':'any',
                   'operators':['gt','gte','lt','lte','eq'], 'threshold':'finite numeric constant',
                   'units':{'close':'CNY/unit','vwap':'CNY/unit','atr_14':'CNY/unit',
                       'ret_1m':'decimal ratio','ret_5m':'decimal ratio','ret_15m':'decimal ratio',
                       'volume_ratio':'dimensionless'}},
               'risk_limits':{key:str(getattr(self.cfg,key)) for key in ('max_single_exposure',
                   'max_total_exposure','min_cash_reserve','stop_loss_ratio','max_daily_loss_ratio')},
               'trading_constraints':{'mode':'paper','shorting':False,'leverage':False,
                   'no_new_entry_time':self.cfg.no_new_entry_time,'close_out_time':self.cfg.close_out_time,
                   'max_holding_horizon_seconds':maximum_horizon,'strategy_ttl_seconds':self.cfg.strategy_ttl_seconds},
               'fees':{'source':'simulated ETF fee assumptions',
                   **{key:str(getattr(profile.fees,key)) for key in ('commission_rate','commission_min',
                       'stamp_duty_rate_sell','transfer_fee_rate')}}}
        try:
            output=self.llm.complete(role='strategy',system=PROMPT,user=json.dumps(facts,ensure_ascii=False),trace_id=trace_id,timeout_s=15)
            parsed=output.parsed
            if not isinstance(parsed,dict) or set(parsed)!=set(STRATEGY_OUTPUT_FIELDS):
                raise ProviderError('invalid strategy JSON')
            if any(not isinstance(parsed[k],str) or not re.search(r'[\u3400-\u9fff]',parsed[k])
                   for k in ('trend','risk','critic','reason')):
                raise ProviderError('invalid strategy Chinese text')
            available=self.clock()
            try:
                plan=StrategyPlan(**parsed,plan_id=new_id('plan'),instrument_id=context.instrument_id,
                    market=profile.market,rule_version=profile.profile_id,created_at=now,
                    data_cutoff=context.as_of,available_at=available,
                    valid_until=min(now+timedelta(seconds=self.cfg.strategy_ttl_seconds),close_at),
                    prompt_version=STRATEGY_PROMPT_VERSION,
                    evidence_ids=context.evidence_ids)
                conditions=plan.entry_conditions+plan.exit_conditions+plan.invalidation_conditions
                if (type(parsed['holding_horizon_seconds']) is not int or plan.holding_horizon_seconds>maximum_horizon
                    or any(c.field not in context.features.values for c in conditions)
                    or any(a.value=='SELL' for a in plan.allowed_actions) and not plan.exit_conditions):
                    raise ValueError('unsupported strategy draft')
            except (ValidationError,TypeError,ValueError):
                raise ProviderError('invalid strategy contract') from None
        except Exception as exc:
            self._record_model_failure(now, trace_id, exc)
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError('strategy request failed') from exc
        self.repo.save_coalesce(self.key(context.instrument_id),available,{
            'plan':plan.model_dump(mode='json'), 'config':{k:str(v) for k,v in self.cfg.__dict__.items()}})
        self._record_model_success(available, plan)
        from ..reports.daily import public_plan
        self.repo.db.execute('INSERT OR IGNORE INTO strategy_plan_history '
                            '(account_id,plan_id,available_at,payload_json) VALUES (?,?,?,?)',
                            (self.account_id,plan.plan_id,plan.available_at.isoformat(),
                             json.dumps(public_plan(plan.model_dump(mode='json')),ensure_ascii=False)))
        return plan
