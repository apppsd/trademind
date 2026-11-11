"""Explicit new profile; persistent cadence, plan reuse, raw JEV, deterministic intent."""
from decimal import Decimal
from datetime import datetime, timedelta
from ..domain.base import new_id
from ..domain.enums import TradeAction, ConfidenceSemantics, ProviderKind, OrderSide
from ..domain.intents import TradeIntent
from ..errors import ProviderError
from ..context.fast_state import action_gate, build_fast_state
from ..market.base import BEIJING
from ..market.profiles import get_profile
from ..portfolio.ledger import compute_fees
from ..models.jev import TypeSafeJEVProvider
from .strategy_planner import StrategyPlanner
from .fast_decider import FastDecider
from .fast_prompts import FAST_PROMPT_VERSION


class FastDecisionPipeline:
    def _check_model_window(self, now):
        previous=self.repo.get_coalesce('fast-eval:'+self.account_id)
        if previous and (now-previous['last_run_at']).total_seconds()<self.cfg.fast_eval_seconds:
            raise ProviderError('global model evaluation cooldown')
        if self.budget.exhausted:
            raise ProviderError('shared model budget exhausted')

    def __init__(self, llm, jev, repo, cfg, account_id, ledger, calendar, budget, clock, risk_state,
                 profile_resolver=None, allow_t_plus_one=False, paper_new_entry_filter=False,
                 quote_max_age_seconds=180):
        self.llm,self.jev,self.repo,self.cfg=llm,jev,repo,cfg
        self.account_id,self.ledger,self.calendar,self.budget,self.clock=account_id,ledger,calendar,budget,clock
        self.risk_state=risk_state
        # Injectable resolver + T+1 tolerance exist for the stock chain
        # (Task27-05/ARCH-028); defaults keep ETF behavior byte-identical.
        self._profile_resolver = profile_resolver or get_profile
        self._allow_t_plus_one = allow_t_plus_one
        self._paper_new_entry_filter = (paper_new_entry_filter and
            account_id=='paper-t0-local-1k' and not allow_t_plus_one)
        # Effective quote freshness gate (review R4, tightened by B01):
        # min(180, configured cap). It gates the decision itself — the decider
        # deadline below uses it — and is reported as age_threshold_seconds.
        # The 180-second intent validity bound below is a separate limit and is
        # reported separately as intent_max_age_seconds — one field must not
        # silently redefine the other.
        self._age_threshold_seconds = min(180, quote_max_age_seconds)
        self.planner=StrategyPlanner(llm,repo,cfg,account_id,clock)
        self.decider=FastDecider(jev,cfg,clock,quote_max_age_seconds=quote_max_age_seconds)

    def run(self,context,trace_id):
        now=self.clock()
        cw=Decimal(context.portfolio.positions.get(context.instrument_id,{}).get('weight','0')) if context.portfolio else Decimal('0')
        meta={'profile':'jev_fast_decision','jev_called':False,'model_path_claimed':False}
        def before_model_call(role, reserve):
            # Provider admission and window claim commit together before HTTP.
            # A rejected reservation leaves both persisted states unchanged.
            if meta['model_path_claimed']:
                reserved=reserve()
            else:
                with self.repo.db.transaction():
                    charged_at=self.clock()
                    self._check_model_window(charged_at)
                    reserved=reserve()
                    self.repo.save_coalesce('fast-eval:'+self.account_id,charged_at,
                        {'instrument':context.instrument_id,'trace_id':trace_id,'role':role})
                meta['model_path_claimed']=True
            if role=='jev':
                meta['jev_called']=True
            return reserved
        action,target,confidence,plan,decision=TradeAction.WAIT,Decimal('0'),Decimal('0'),None,None
        reason='fast decision not eligible'
        try:
            profile=self._profile_resolver(context.instrument_id)
            if context.instrument_id not in self.cfg.whitelist or (
                    profile.t_plus_days != 0 and not self._allow_t_plus_one):
                raise ProviderError('instrument not in verified T0 whitelist')
            if not self.calendar.is_session_open(now) or now>=context.expires_at:
                raise ProviderError('outside continuous session or expired context')
            if not isinstance(self.jev,TypeSafeJEVProvider) or getattr(self.llm,'kind',None)!=ProviderKind.DEEPSEEK:
                raise ProviderError('fast profile requires real flash and official JEV')
            if self.jev.base_url != 'https://api.typesafe.ai':
                raise ProviderError('fast profile requires official JEV endpoint')
            if not self.llm.capability_verified or not self.jev.capability_verified:
                raise ProviderError('real model probe capability unverified')
            self.budget.jev_cost(1, 0)
            if self.cfg.model_input_cny_per_million is None or self.cfg.model_output_cny_per_million is None:
                raise ProviderError('flash pricing unconfigured')
            position=context.portfolio.positions.get(context.instrument_id) if context.portfolio else None
            orders=self.repo.open_orders()
            if (position or not self._paper_new_entry_filter) and any(
                    o['account_id']==self.account_id and o['instrument_id']==context.instrument_id for o in orders):
                raise ProviderError('pending order; no new model decision')
            if not position:
                if now.astimezone(BEIJING).strftime('%H:%M') >= self.cfg.no_new_entry_time:
                    raise ProviderError('new-entry session cutoff')
                price=context.features.values['close']
                lot=Decimal(profile.lot_size)
                notional=price*lot
                # One lot must fit the market-value cap, and its cash cost
                # (notional + fees) must fit the free-cash budget — the same
                # split the target sizer below uses (Issue16-05).
                entry_fees=compute_fees(OrderSide.BUY,lot,price,profile.fees)
                if self._paper_new_entry_filter:
                    from ..evaluation.tradability import paper_entry_report
                    report=paper_entry_report(context,self.cfg,orders,now,self._profile_resolver)
                    meta['tradability']=report
                    entry=report['instruments'][0]
                    if not entry['new_entry_candidate']:
                        raise ProviderError('paper new-entry observation only')
                elif notional>self.cfg.max_single_exposure or notional+entry_fees> \
                        context.portfolio.cash-context.portfolio.reserved_cash-self.cfg.min_cash_reserve:
                    raise ProviderError('minimum lawful lot unaffordable')
                risks=self.risk_state()
                baseline=Decimal(risks.get('day_start_equity','0'))
                if (risks.get('consecutive_losses',0)>=self.cfg.consecutive_losses_limit
                    or risks.get('daily_buy_orders',0)>=self.cfg.daily_new_orders_limit
                    or baseline>0 and (baseline-context.portfolio.equity)/baseline>=self.cfg.max_daily_loss_ratio):
                    raise ProviderError('new-risk hard limit reached')
            refresh_key='fast-refresh:'+self.account_id+':'+context.instrument_id
            refresh=self.repo.get_coalesce(refresh_key)
            force_refresh=bool(refresh and refresh['meta'].get('requested'))
            has_cached=bool(not force_refresh and self.planner.cached(context,profile.profile_id,now))
            if not has_cached and self.planner.in_failure_backoff(now):
                raise ProviderError('strategy model failure backoff')
            with self.repo.db.transaction():
                self._check_model_window(now)
                # Stock/T+1 retains its existing observation-window semantics.
                if self._allow_t_plus_one:
                    self.repo.save_coalesce('fast-eval:'+self.account_id,now,
                                            {'instrument':context.instrument_id})
                    meta['model_path_claimed']=True
            plan=self.planner.get(context,profile,trace_id,refresh=force_refresh,
                                  before_model_call=before_model_call)
            self.repo.save_coalesce(refresh_key,now,{'requested':False})
            account=self.ledger.snapshot(now,{context.instrument_id:context.features.values['close']})
            state=build_fast_state(context,plan,profile,account,self.repo.open_orders(),self.cfg,
                                   self.budget.snapshot(),self.clock(),self.risk_state())
            meta['state']=state.model_dump(mode='json')
            decision=self.decider.decide(state,trace_id,before_model_call=before_model_call)
            # Why-only-WAIT evidence, computed from the already-built state
            # without any model call (t279u item A).
            if decision.reason=='no actionable strategy conditions' and decision.action==TradeAction.WAIT:
                meta['action_gate']=action_gate(state)
            meta['outcome']=decision.model_dump(mode='json')
            if decision.refresh_requested:
                self.repo.save_coalesce(refresh_key,now,{'requested':True})
            if self.ledger.snapshot(self.clock()).version!=decision.account_version:
                raise ProviderError('account version changed after decision')
            action,confidence,reason=decision.action,decision.action_confidence,decision.reason
            if action==TradeAction.BUY:
                equity=account.equity
                # Market-value caps compare *cumulative* value without fees; the
                # cash budget (cash − reserved − min reserve) must additionally
                # cover notional + buy fees (Issue16-05). pos_value comes from
                # the ledger snapshot — the intent target is the *cumulative*
                # weight (existing value + lawful increment). Using the headroom
                # alone as the target made every scale-in with a position emit
                # target<=cw → WAIT 'no lawful increase size' (Task17-03).
                pos_value=next((p.market_value or Decimal('0') for p in account.positions
                                if p.instrument_id==context.instrument_id),Decimal('0'))
                total_value=sum((p.market_value or Decimal('0')) for p in account.positions)
                price=context.features.values['close']
                lot=Decimal(profile.lot_size)
                cap_lots=(((min(self.cfg.max_single_exposure-pos_value,
                                self.cfg.max_total_exposure-total_value)/price)/lot)
                          .to_integral_value(rounding='ROUND_DOWN')
                          if equity>0 and price>0 else Decimal('0'))
                cash_cap=account.cash-account.reserved_cash-self.cfg.min_cash_reserve
                qty_lots=Decimal('0')
                if price>0 and cash_cap>0:
                    qty_lots=((cash_cap/price)/lot).to_integral_value(rounding='ROUND_DOWN')
                    while qty_lots>0 and qty_lots*lot*price+compute_fees(OrderSide.BUY,qty_lots*lot,price,profile.fees)>cash_cap:
                        qty_lots-=1  # fees come out of the cash budget, never the caps
                    qty_lots=min(qty_lots,cap_lots)
                if equity<=0 or qty_lots<=Decimal('0'):
                    action,target,reason=TradeAction.WAIT,Decimal('0'),'no lawful increase size'
                else:
                    # Emit the smallest 28-digit cumulative weight whose Guard
                    # lot-floor still yields qty_lots, never strictly above the
                    # cap weight (Guard vetoes strictly-above at
                    # max_single_exposure_limit). Decimal rounding at either
                    # boundary must not eat the marginal lot; when the cap
                    # itself binds, stepping down one lot keeps the intent
                    # lawful. Guard remains an independent hard check, never a
                    # sizer.
                    cap_weight=min(self.cfg.max_single_exposure,
                                   self.cfg.max_total_exposure-total_value+pos_value)/equity
                    target=Decimal('0')
                    while qty_lots>0:
                        notional=qty_lots*lot*price
                        candidate=(pos_value+notional)/equity
                        for _ in range(3):
                            if candidate>cap_weight:
                                break
                            derived=((candidate*equity-pos_value)/price/lot).to_integral_value(rounding='ROUND_DOWN')*lot
                            if derived>=notional/price:
                                target=candidate
                                break
                            candidate=candidate.next_plus()
                        if target>0:
                            break
                        qty_lots-=1
                    if target<=0 or target<=cw:
                        action,target,reason=TradeAction.WAIT,Decimal('0'),'no lawful increase size'
            elif action==TradeAction.HOLD:
                target=cw
        except (ProviderError,ValueError,KeyError) as exc:
            # Only categorical reasons are exposed, never transport body/credentials.
            reason=str(exc) if isinstance(exc,ProviderError) else 'invalid fast-decision state'
            action,target=TradeAction.WAIT,Decimal('0')
        meta['reason']=reason
        last_bar = (context.aux.get('minute_bars') or [{}])[-1]
        bar_time = last_bar.get('time') or last_bar.get('bar_time')
        valid_until=min(context.expires_at,context.as_of+timedelta(seconds=180),plan.valid_until if plan else context.expires_at,
                        decision.valid_until if decision else context.expires_at)
        # Read-only report of the persisted global-window claim (cooldown
        # diagnostics only; the interval itself is never changed here).
        interval=self.cfg.fast_eval_seconds
        window=self.repo.get_coalesce('fast-eval:'+self.account_id)
        remaining=(max(0,int(interval-(self.clock()-window['last_run_at']).total_seconds()))
                   if interval and window else None)
        meta['diagnostics']={'context_expires_at':context.expires_at.isoformat(),
            'data_age_seconds':(self.clock()-datetime.fromisoformat(bar_time)).total_seconds() if bar_time else None,
            # Effective quote freshness gate (review R4): min(180, configured
            # quote_max_age_seconds), wired from the decision pipeline. The
            # intent-validity 180s bound above is reported separately.
            'age_threshold_seconds':self._age_threshold_seconds,
            'intent_max_age_seconds':180,
            'data_window':context.aux.get('data_window'),
            # Persisted global-window claim, for cooldown diagnostics only.
            'global_cooldown_seconds_remaining':remaining,
            'model_window':{'key':'fast-eval:'+self.account_id,'scope':'account',
                'effective_interval_seconds':interval,
                'last_claim_at':window['last_run_at'].isoformat() if window else None,
                'next_eligible_at':(window['last_run_at']+timedelta(seconds=interval)).isoformat()
                    if window and interval else None,
                'remaining_seconds':remaining,
                'claim_this_tick':bool(meta.get('model_path_claimed')),
                'claim_role':(window.get('meta') or {}).get('role') if window else None,
                'claim_trace_id':(window.get('meta') or {}).get('trace_id') if window else None},
            'plan_valid_until':plan.valid_until.isoformat() if plan else None,
            'intent_valid_until':valid_until.isoformat(),'reason':reason,'budget':self.budget.snapshot(),
            'strategy_health':self.planner.health_state(self.clock())}
        if 'tradability' in meta:
            meta['diagnostics']['tradability']=meta['tradability']
        if 'action_gate' in meta:
            meta['diagnostics']['action_gate']=meta['action_gate']
        if 'tradability' in meta:
            meta['diagnostics']['tradability']=meta['tradability']
        intent=TradeIntent(id=new_id('ti'),trace_id=trace_id,created_at=self.clock(),
            decision_id=decision.decision_id if decision else new_id('fast'),context_id=context.context_id,
            thesis_id=plan.plan_id if plan else 'fast-no-plan',instrument_id=context.instrument_id,
            portfolio_version=context.portfolio.portfolio_version if context.portfolio else 0,
            account_id=self.account_id,action=action,target_weight=target,current_weight=cw,
            confidence=confidence,confidence_semantics=ConfidenceSemantics.ANSWER_DISTRIBUTION_CONFIDENCE,
            holding_horizon_seconds=plan.holding_horizon_seconds if plan and action!=TradeAction.WAIT else 0,
            evidence_ids=context.evidence_ids,reason=reason,exit_conditions=[],valid_until=valid_until,
            model=decision.model if decision else '',prompt_id=FAST_PROMPT_VERSION)
        return intent,meta
