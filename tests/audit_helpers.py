from datetime import timedelta
from decimal import Decimal
from trademind.config import RiskConfig
from trademind.domain.context import PortfolioView
from trademind.domain.base import new_id
from trademind.guard import Guard
from test_guard_oms_pipeline import make_snapshot


def authorize(repo, ledger, audit, intent, now, reference=Decimal('40')):
    snap = make_snapshot(None).model_copy(update={
        'instrument_id': intent.instrument_id, 'as_of': now,
        'expires_at': now + timedelta(minutes=15),
        'portfolio': PortfolioView(id=new_id('pv'), trace_id=new_id('t'), created_at=now,
                                  account_id=ledger.account_id, portfolio_version=ledger.version,
                                  as_of=now, currency='CNY', cash=ledger.snapshot(now).cash,
                                  equity=ledger.snapshot(now).equity, positions={})})
    guard = Guard(repo, ledger, RiskConfig(), intent.mode.value, [intent.instrument_id], audit)
    result, qty = guard.check(intent, snap, reference, now)
    assert result.verdict.value == 'ALLOW', result.reason_codes
    return result, qty
