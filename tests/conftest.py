"""Shared test fixtures: factories for legal contract objects."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trademind.domain.base import new_id  # noqa: E402

T0 = datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)  # 14:00 Beijing


def legal_evidence(**kw):
    from trademind.domain.evidence import Evidence
    from trademind.domain.enums import Dataset

    base = dict(
        id=new_id("ev"),
        trace_id=new_id("tr"),
        created_at=T0,
        evidence_id=new_id("ev"),
        source_id="fixture",
        source_uri="fixture://daily_bars/600519.SH",
        dataset=Dataset.DAILY_BARS,
        instrument_ids=["600519.SH"],
        event_time=T0 - timedelta(days=1),
        published_at=T0 - timedelta(days=1),
        available_at=T0 - timedelta(hours=1),
        ingested_at=T0,
        content_hash="a" * 64,
        payload_ref="fixtures://daily/600519.SH",
    )
    base.update(kw)
    return Evidence(**base)


def legal_instrument(**kw):
    from trademind.domain.instruments import Instrument

    base = dict(
        id=new_id("ins"),
        trace_id=new_id("tr"),
        created_at=T0,
        instrument_id="600519.SH",
        market="XSHG",
        symbol="600519",
        name="贵州茅台",
        currency="CNY",
        calendar_id="XSHG",
        price_tick=Decimal("0.01"),
        lot_size=100,
    )
    base.update(kw)
    return Instrument(**base)


def legal_thesis(**kw):
    from trademind.domain.enums import ConfidenceSemantics, Direction
    from trademind.domain.research import TradeThesis

    base = dict(
        id=new_id("th"),
        trace_id=new_id("tr"),
        created_at=T0,
        thesis_id=new_id("th"),
        instrument_id="600519.SH",
        context_id=new_id("ctx"),
        direction=Direction.LONG,
        horizon_seconds=86400 * 5,
        confidence=Decimal("0.7"),
        confidence_semantics=ConfidenceSemantics.EVIDENCE_SUPPORT,
        evidence_ids=[new_id("ev")],
        supporting_reasons=["price above MA20 with rising volume"],
        counterarguments=["valuation above sector median"],
        invalidations=["close below MA20 on above-average volume"],
        valid_until=T0 + timedelta(hours=8),
    )
    base.update(kw)
    return TradeThesis(**base)


def legal_intent(**kw):
    from trademind.domain.enums import ConfidenceSemantics, TradeAction
    from trademind.domain.intents import TradeIntent

    base = dict(
        id=new_id("ti"),
        trace_id=new_id("tr"),
        created_at=T0,
        decision_id=new_id("dec"),
        context_id=new_id("ctx"),
        thesis_id=new_id("th"),
        instrument_id="600519.SH",
        portfolio_version=1,
        account_id="paper-001",
        action=TradeAction.BUY,
        target_weight=Decimal("0.15"),
        current_weight=Decimal("0"),
        confidence=Decimal("0.8"),
        confidence_semantics=ConfidenceSemantics.EVIDENCE_SUPPORT,
        holding_horizon_seconds=86400 * 5,
        evidence_ids=[new_id("ev")],
        reason="thesis LONG with healthy liquidity",
        valid_until=T0 + timedelta(minutes=5),
        exit_conditions=[{"description": "stop at -5%", "kind": "stop_loss", "reference": "1500"}],
    )
    base.update(kw)
    return TradeIntent(**base)
