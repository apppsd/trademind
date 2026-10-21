"""Isolated historical paper accounts for prompt comparisons; no invented bars."""
from datetime import datetime
from decimal import Decimal

from ..config import AppConfig
from ..domain.base import new_id
from ..domain.context import PortfolioView
from ..market.base import MinuteBar, BEIJING
from ..pipeline import Pipeline
from ..agents.pipeline import DualModelPipeline


def recorded_bars(contexts):
    bars = {}
    for ctx in contexts:
        if 'close' not in ctx.features.values or not ctx.aux.get('minute_bars'):
            return None
        for row in ctx.aux['minute_bars']:
            if not all(k in row for k in ('time', 'available_at', 'source_time', 'open', 'high', 'low', 'close', 'volume')):
                return None
            at = datetime.fromisoformat(row['time'])
            source = datetime.fromisoformat(row['source_time'])
            available = datetime.fromisoformat(row['available_at'])
            if source > ctx.as_of or available > ctx.as_of or at > ctx.as_of:
                raise ValueError('recorded bar is not visible at source context')
            bar = MinuteBar(
                instrument_id=ctx.instrument_id, trade_date=at.date(), bar_time=at,
                open=row['open'], high=row['high'], low=row['low'], close=row['close'],
                volume=row['volume'], amount=row.get('amount', 0), provider='recorded-context',
                source_time=source, available_at=available, fetched_at=available)
            bars[(bar.instrument_id, at)] = bar
    return list(bars.values())


class PromptPaperAccount:
    def __init__(self, path, account_id, contexts, bars, llm, jev, version, initial_cash=None):
        cfg = AppConfig()
        cfg.t0.enabled = True
        # TASK-20261013-07 (ISSUE-20261012-04): the isolated fund is
        # configurable (defaults to the t0 500 baseline); zero/negative/
        # non-finite is rejected before the account is created.
        cash = Decimal(str(initial_cash)) if initial_cash is not None else cfg.t0.initial_cash
        if not cash.is_finite() or cash <= 0:
            raise ValueError('prompt paper account requires a positive finite initial cash')
        cfg.t0.initial_cash = cash
        cfg.paper.initial_cash = cash
        self.initial_cash = cash
        self.initial_cash_source = 'explicit_parameter' if initial_cash is not None else 'config.t0.initial_cash'
        cfg.universe = sorted({ctx.instrument_id for ctx in contexts})
        cfg.t0.whitelist = list(cfg.universe)
        cfg.t0.candidate_cooldown_seconds = 0
        cfg.account_id = account_id
        cfg.storage.url = f'sqlite:///{path}'
        cfg.data.provider_pref = ['fixture']
        cfg.llm.provider = 'stub'
        self.now = contexts[0].as_of
        self.pipe = Pipeline(cfg, clock=lambda: self.now)
        self.pipe.ensure_account(at=self.now)
        self.pipe.router.get_minute_bars = lambda iid, start, end, **kw: (
            [b for b in bars if b.instrument_id == iid and start <= b.bar_time <= end and b.available_at <= end], None)
        self.pipe._dual_model_pipeline = DualModelPipeline(llm_provider=llm, jev_provider=jev,
                                                           account_id=account_id, t0_config=cfg.t0,
                                                           prompt_version=version)
        self.results = []
        self.llm = llm
        self.peak = cash
        self.max_drawdown = Decimal('0')
        self.prices = {}

    def step(self, context):
        self.now = context.as_of
        for iid in self.prices:
            self.prices[iid] = self.pipe.reference_price(iid, self.now)
        self.prices[context.instrument_id] = self.pipe.reference_price(context.instrument_id, self.now)
        self.pipe.poll_fills(self.now)
        self.pipe.on_new_trading_day(self.now.astimezone(BEIJING).date(), self.prices)
        snapshot = self.pipe.ledger.snapshot(self.now, self.prices)
        ctx = context.model_copy(deep=True)
        ctx.context_id = new_id('experiment-ctx')
        ctx.portfolio = PortfolioView(
            id=new_id('pv'), trace_id=new_id('tr'), created_at=self.now,
            portfolio_version=snapshot.version, account_id=snapshot.account_id,
            as_of=self.now, currency='CNY', cash=snapshot.cash, equity=snapshot.equity,
            reserved_cash=snapshot.reserved_cash,
            positions={p.instrument_id: {'qty': str(p.quantity), 'weight': str(snapshot.position_weight(p.instrument_id)),
                                        'market_value': str(p.market_value)} for p in snapshot.positions})
        ctx.content_hash = ctx.compute_hash()
        self.pipe.context_builder.build_minute = lambda *a, **kw: ctx
        result = self.pipe.run_decision(ctx.instrument_id, self.now)
        self.results.append(result)
        equity = self.pipe.ledger.snapshot(self.now, self.prices).equity
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak-equity)/self.peak)
        return result

    def report(self):
        import json
        snap = self.pipe.ledger.snapshot(self.now, self.prices)
        fills = self.pipe.repo.account_events(self.pipe.cfg.account_id)
        fees = Decimal('0')
        for event in fills:
            if event['kind'] in ('fill_buy', 'fill_sell'):
                for part in event['note'].split(';'):
                    if part.startswith('fee='):
                        fees += Decimal(part[4:])
        model_calls = []
        auditor = getattr(self.llm, 'auditor', None)
        if auditor:
            for result in self.results:
                model_calls.extend(json.loads(r['payload_json']) for r in auditor.repo.db.query(
                    'SELECT payload_json FROM model_calls WHERE trace_id=?', (result.trace_id,)))
        cost_known = bool(model_calls) and all(c.get('cost_amount') is not None for c in model_calls)
        deepseek_cost = str(sum((Decimal(c['cost_amount']) for c in model_calls), Decimal('0'))) if cost_known else None
        return {'account_id': snap.account_id, 'kind': 'historical_paper',
                'initial_cash': str(self.initial_cash), 'equity': str(snap.equity),
                'net_pnl': str(snap.equity-self.initial_cash),
                'initial_cash_source': self.initial_cash_source,
                'trading_fees_cny': str(fees), 'model_cost_cny': None,
                'net_pnl_definition': 'After trading fees, before unverified model costs',
                'deepseek_cost_upper_bound_cny': deepseek_cost,
                'model_cost_note': 'Not derivable from intent snapshots; use provider audit and pricing. JEV pricing excluded.',
                'max_drawdown': str(self.max_drawdown),
                'orders': len([r for r in self.results if r.order_id]),
                'fills': sum(e['kind'] in ('fill_buy', 'fill_sell') for e in fills),
                'rejections': sum(r.guard_verdict == 'VETO' for r in self.results),
                'errors': [r.error for r in self.results if r.error],
                'pending_orders': len(self.pipe.repo.open_orders()),
                'open_positions': len(snap.positions),
                'source_contexts': len(self.results),
                'archive': {
                    'account_events': fills,
                    'contexts': [json.loads(r['payload_json']) for r in self.pipe.db.query('SELECT payload_json FROM context_snapshots')],
                    'intents': [json.loads(r['payload_json']) for r in self.pipe.db.query('SELECT payload_json FROM intents')],
                    'orders': [json.loads(r['payload_json']) for r in self.pipe.db.query('SELECT payload_json FROM orders')],
                    'audit': [json.loads(r['payload_json']) for r in self.pipe.db.query('SELECT payload_json FROM control_audit')],
                    'model_calls': model_calls,
                }}

    def close(self):
        self.pipe.release_writer_lease()
        self.pipe.db.close()
