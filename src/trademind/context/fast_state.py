"""Build raw, visible facts and legal actions for JEV, separate from strategy."""
from datetime import datetime
from decimal import Decimal
from ..domain.strategy import FastDecisionState
from ..domain.enums import TradeAction
from ..errors import ProviderError


def conditions_allow(action, plan, features):
    """Single source of trade-condition semantics (ISSUE-20261013-04).

    BUY requires every entry condition; SELL requires any one exit condition.
    An empty condition set never authorizes a trade, so ``all([])`` can no
    longer turn a SELL into an executable action. Shared by the synchronous
    state build and the asynchronous result re-check so both paths judge the
    same plan against the same features identically.
    """
    if action == TradeAction.BUY:
        return bool(plan.entry_conditions) and all(c.matches(features) for c in plan.entry_conditions)
    if action == TradeAction.SELL:
        return bool(plan.exit_conditions) and any(c.matches(features) for c in plan.exit_conditions)
    return False


def condition_truth_rows(conditions, features):
    """Per-condition evidence rows (t279u item A). A missing or non-finite
    feature stays unknown — actual is never invented, matched stays false,
    mirroring ``Condition.matches`` semantics exactly."""
    rows = []
    for c in conditions:
        actual = features.get(c.field)
        try:
            usable = actual is not None and Decimal(str(actual)).is_finite()
        except (ValueError, ArithmeticError):
            usable = False
        rows.append({'field': c.field, 'operator': c.operator, 'threshold': str(c.value),
                     'actual': str(actual) if usable else 'unknown',
                     'matched': bool(c.matches(features)) if usable else False,
                     'missing': not usable})
    return rows


def action_gate(state):
    """Structured why-only-WAIT evidence for the no-actionable reason.

    Exclusion classes follow the exact gate order in ``build_fast_state`` so
    the reported cause always names the first check that collapsed the
    action: BUY pending→plan→conditions→cash; SELL pending→position→plan→
    conditions; HOLD position→plan. Purely derived from the already-built
    ``FastDecisionState`` — no model call, no new facts.
    """
    plan = state.plan
    features = state.features
    account = state.account
    pos = next((p for p in account['positions'] if p['instrument_id'] == state.instrument_id), None)
    pending = any(o['instrument_id'] == state.instrument_id for o in account['open_orders'])
    allowed = set(state.allowed_actions)
    cash = Decimal(str(account['cash'])) - Decimal(str(account['reserved_cash']))
    reserve = Decimal(str(state.risk_limits.get('reserve', '0')))
    def exclusion(action):
        if action in allowed:
            return None
        if action == TradeAction.HOLD:
            return 'no_position_or_qty' if not pos else 'plan_disallowed'
        if action == TradeAction.BUY:
            if pending:
                return 'pending_order'
            if action not in plan.allowed_actions:
                return 'plan_disallowed'
            if not conditions_allow(action, plan, features):
                return 'conditions_unmet'
            if not cash > reserve:
                return 'cash_reserve'
        elif action == TradeAction.SELL:
            if pending:
                return 'pending_order'
            if not pos or Decimal(str(pos['available_quantity'])) <= 0:
                return 'no_position_or_qty'
            if action not in plan.allowed_actions:
                return 'plan_disallowed'
            if not conditions_allow(action, plan, features):
                return 'conditions_unmet'
        return 'unknown'
    return {'plan_id': plan.plan_id,
            'allowed_actions': [a.value for a in state.allowed_actions],
            'plan_allowed_actions': [a.value for a in plan.allowed_actions],
            'exclusion': {a.value: exclusion(a) for a in (TradeAction.BUY, TradeAction.SELL, TradeAction.HOLD)},
            'entry_conditions': condition_truth_rows(plan.entry_conditions, features),
            'exit_conditions': condition_truth_rows(plan.exit_conditions, features)}


def build_fast_state(context, plan, profile, account, open_orders, cfg, budget, now, risk_state=None):
    bars=context.aux.get('minute_bars',[])
    if not bars or 'freshness' in context.missing_fields or 'bars' in context.missing_fields:
        raise ProviderError('missing fresh raw minute facts')
    for b in bars:
        for key in ('time','source_time','available_at','fetched_at'):
            if key not in b:
                raise ProviderError('missing minute provenance')
            dt=datetime.fromisoformat(b[key])
            if dt.tzinfo is None or dt>context.as_of:
                raise ProviderError('future/unavailable minute fact')
    if context.portfolio is None or account.version!=context.portfolio.portfolio_version:
        raise ProviderError('account changed during strategy generation')
    features=context.features.values
    if any(c.matches(features) for c in plan.invalidation_conditions):
        raise ProviderError('strategy invalidation condition met')
    allowed=[TradeAction.WAIT]
    position=next((p for p in account.positions if p.instrument_id==context.instrument_id),None)
    active_orders=[dict(o) for o in open_orders if o['account_id']==account.account_id]
    if position:
        allowed.append(TradeAction.HOLD)
    pending=any(o['instrument_id']==context.instrument_id for o in active_orders)
    if not pending:
        if (TradeAction.BUY in plan.allowed_actions
            and conditions_allow(TradeAction.BUY, plan, features)
            and account.cash-account.reserved_cash>cfg.min_cash_reserve):
            allowed.append(TradeAction.BUY)
        if (position and position.available_quantity>0 and TradeAction.SELL in plan.allowed_actions
            and conditions_allow(TradeAction.SELL, plan, features)):
            allowed.append(TradeAction.SELL)
    # WAIT always available; HOLD only when there is an actual position.
    allowed=[a for a in allowed if a==TradeAction.WAIT or a in plan.allowed_actions]
    last=bars[-1]
    return _state(context,plan,profile,account,active_orders,cfg,budget,now,allowed,last,risk_state)


def _state(context,plan,profile,account,orders,cfg,budget,now,allowed,last,risk_state):
    return FastDecisionState(context_id=context.context_id,instrument_id=context.instrument_id,
        observed_at=now,data_cutoff=context.as_of,source_time=datetime.fromisoformat(last['source_time']),
        fetched_at=datetime.fromisoformat(last['fetched_at']),available_at=datetime.fromisoformat(last['available_at']),
        market=profile.market,rule_version=profile.profile_id,features=context.features.values,bars=context.aux['minute_bars'],
        account={'id':account.account_id,'version':account.version,'cash':str(account.cash),
                 'reserved_cash':str(account.reserved_cash),'equity':str(account.equity),
                 'positions':[p.model_dump(mode='json') for p in account.positions],
                 'open_orders':[{'instrument_id':o['instrument_id'],'side':o['side'],'quantity':o['quantity'],'status':o['status']} for o in orders]},
        allowed_actions=allowed,plan=plan,
        quality={'status':context.features.quality.value,'missing':context.missing_fields,
                 'feature_missing':context.features.missing_fields,'order_book':None,'order_book_reason':'no verified L1',
                 'age_seconds':(now-datetime.fromisoformat(last['time'])).total_seconds()},
        fees={'schedule':{k:str(getattr(profile.fees,k)) for k in ('commission_rate','commission_min','stamp_duty_rate_sell','transfer_fee_rate')},
              'source':'simulated ETF fee assumptions','price_unit':'CNY','slippage_bps':(risk_state or {}).get('slippage_bps')},
        risk_limits={'initial_cash':str(cfg.initial_cash),'single_exposure':str(cfg.max_single_exposure),
                     'total_exposure':str(cfg.max_total_exposure),'reserve':str(cfg.min_cash_reserve),
                     'stop_loss_ratio':str(cfg.stop_loss_ratio),'daily_loss_ratio':str(cfg.max_daily_loss_ratio),
                     **(risk_state or {})},budget=budget)
