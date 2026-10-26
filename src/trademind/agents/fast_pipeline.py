"""Explicit new profile; persistent cadence, plan reuse, raw JEV, deterministic intent."""
from decimal import Decimal
from datetime import datetime, timedelta
from ..domain.base import new_id
from ..domain.enums import TradeAction, ConfidenceSemantics, ProviderKind, OrderSide
from ..domain.intents import TradeIntent
from ..errors import ProviderError
from ..context.fast_state import build_fast_state
from ..market.base import BEIJING
from ..market.profiles import get_profile
from ..portfolio.ledger import compute_fees
from ..models.jev import TypeSafeJEVProvider
from .strategy_planner import StrategyPlanner
from .fast_decider import FastDecider
from .fast_prompts import FAST_PROMPT_VERSION


class FastDecisionPipeline:
    def __init__(self, llm, jev, repo, cfg, account_id, ledger, calendar, budget, clock, risk_state):
        self.llm,self.jev,self.repo,self.cfg=llm,jev,repo,cfg
        self.account_id,self.ledger,self.calendar,self.budget,self.clock=account_id,ledger,calendar,budget,clock
        self.risk_state=risk_state
        self.planner=StrategyPlanner(llm,repo,cfg,account_id,clock)
        self.decider=FastDecider(jev,cfg,clock)

    def run(self,context,trace_id):
        now=self.clock()
        cw=Decimal(context.portfolio.positions.get(context.instrument_id,{}).get('weight','0')) if context.portfolio else Decimal('0')
        meta={'profile':'jev_fast_decision','jev_called':False}
        action,target,confidence,plan,decision=TradeAction.WAIT,Decimal('0'),Decimal('0'),None,None
        reason='fast decision not eligible'
        try:
            profile=get_profile(context.instrument_id)
            if context.instrument_id not in self.cfg.whitelist or profile.t_plus_days!=0:
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
            if any(o['account_id']==self.account_id and o['instrument_id']==context.instrument_id for o in self.repo.open_orders()):
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
                if notional>self.cfg.max_single_exposure or notional+entry_fees> \
                        context.portfolio.cash-context.portfolio.reserved_cash-self.cfg.min_cash_reserve:
                    raise ProviderError('minimum lawful lot unaffordable')
                risks=self.risk_state()
                baseline=Decimal(risks.get('day_start_equity','0'))
                if (risks.get('consecutive_losses',0)>=self.cfg.consecutive_losses_limit
                    or risks.get('daily_buy_orders',0)>=self.cfg.daily_new_orders_limit
                    or baseline>0 and (baseline-context.portfolio.equity)/baseline>=self.cfg.max_daily_loss_ratio):
                    raise ProviderError('new-risk hard limit reached')
            state_key='fast-eval:'+self.account_id
            refresh_key='fast-refresh:'+self.account_id+':'+context.instrument_id
            refresh=self.repo.get_coalesce(refresh_key)
            force_refresh=bool(refresh and refresh['meta'].get('requested'))
            has_cached=bool(not force_refresh and self.planner.cached(context,profile.profile_id,now))
            if not has_cached and self.planner.in_failure_backoff(now):
                raise ProviderError('strategy model failure backoff')
            with self.repo.db.transaction():
                previous=self.repo.get_coalesce(state_key)
                if previous and (now-previous['last_run_at']).total_seconds()<self.cfg.fast_eval_seconds:
                    raise ProviderError('global model evaluation cooldown')
                if self.budget.exhausted:
                    raise ProviderError('shared model budget exhausted')
                self.repo.save_coalesce(state_key,now,{'instrument':context.instrument_id})
            plan=self.planner.get(context,profile,trace_id,refresh=force_refresh)
            self.repo.save_coalesce(refresh_key,now,{'requested':False})
            account=self.ledger.snapshot(now,{context.instrument_id:context.features.values['close']})
            state=build_fast_state(context,plan,profile,account,self.repo.open_orders(),self.cfg,
                                   self.budget.snapshot(),self.clock(),self.risk_state())
            meta['state']=state.model_dump(mode='json')
            meta['jev_called']=len(state.allowed_actions)>1
            decision=self.decider.decide(state,trace_id)
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
        meta['diagnostics']={'context_expires_at':context.expires_at.isoformat(),
            'data_age_seconds':(self.clock()-datetime.fromisoformat(bar_time)).total_seconds() if bar_time else None,
            'plan_valid_until':plan.valid_until.isoformat() if plan else None,
            'intent_valid_until':valid_until.isoformat(),'reason':reason,'budget':self.budget.snapshot(),
            'strategy_health':self.planner.health_state(self.clock())}
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
