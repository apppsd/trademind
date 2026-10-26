"""Workbench chart read-models (TASK-20261026-06, ARCH-025).

Rebuilds bounded, point-in-time-correct chart views from what already exists
locally: Context minute bars, orders/fills/intents, ledger cash events and
daily-report artifacts. Hard rules implemented here:

- A GET never touches providers or the model, never writes the database.
- Minute facts are deduplicated per ``bar_time``: rows whose ``available_at``
  is after the query ``as_of`` are dropped first, then the latest visible
  revision wins (never the newest revision filtered afterwards).
- 5m/15m aggregates follow Shanghai session boundaries; a bucket is
  ``complete`` only when every constituent minute is present inside one
  session. Lunch/overnight never glue bars together.
- Daily K lines are not offered: local Context minutes are not a complete
  authoritative daily series; capability stays False instead of faking one.
- Point caps: default 2000, hard 5000 per response.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from decimal import Decimal, InvalidOperation

from .read_models import WorkbenchReadStore, _iso, _parse

DEFAULT_POINTS = 2000
HARD_POINTS = 5000
MAX_CONTEXT_ROWS = 800
PERIODS = {'1m', '5m', '15m', 'timeshare'}
SESSIONS = (('09:30', '11:30'), ('13:00', '15:00'))


def _beijing(at: datetime) -> datetime:
    from ..market.base import BEIJING
    return at.astimezone(BEIJING)


def _hhmm(at: datetime) -> str:
    return at.strftime('%H:%M')


def _in_session(at_bj: datetime) -> bool:
    t = _hhmm(at_bj)
    return any(lo <= t <= hi for lo, hi in SESSIONS)


def _bucket_start(at_bj: datetime, minutes: int) -> datetime:
    floor_min = (at_bj.minute // minutes) * minutes
    return at_bj.replace(minute=floor_min, second=0, microsecond=0)


def collect_minute_bars(store: WorkbenchReadStore, instrument_id: str,
                        from_utc: datetime, to_utc: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    """Visible minute facts from stored Contexts, point-in-time dedup.

    Rows with ``available_at > to`` are invisible; among the rest the latest
    ``available_at`` per ``bar_time`` wins. Returns (bars asc, missing_notes).
    """
    notes: list[str] = []
    if not store.db_path.exists():
        return [], ['本地数据库不存在']
    with store._connect() as conn:
        if conn is None or 'context_snapshots' not in store._tables(conn):
            return [], ['本地没有Context快照表']
        rows = conn.execute(
            "SELECT as_of, payload_json FROM context_snapshots WHERE instrument_id=? "
            "AND json_extract(payload_json,'$.portfolio.account_id')=? "
            "AND julianday(as_of)<=julianday(?) ORDER BY julianday(as_of) DESC LIMIT ?",
            (instrument_id, store.account_id, _iso(to_utc), MAX_CONTEXT_ROWS)).fetchall()
    if not rows:
        return [], ['所选范围内没有已保存的分钟Context']
    seen: dict[str, dict[str, Any]] = {}
    scanned = 0
    for r in rows:
        try:
            payload = json.loads(r['payload_json'])
        except (ValueError, TypeError):
            continue
        aux = payload.get('aux') or {}
        bars = aux.get('minute_bars') or []
        scanned += 1
        for bar in bars:
            bar_time = bar.get('bar_time') or bar.get('time')
            available = bar.get('available_at') or bar.get('source_time')
            t = _parse(bar_time)
            a = _parse(available)
            if t is None or a is None:
                continue
            if a > to_utc:
                continue  # future revision not visible at query time
            if t < from_utc or t > to_utc:
                continue
            bar = dict(bar, bar_time=_iso(t), available_at=_iso(a))
            key = _iso(t)
            prev = seen.get(key)
            if prev is None or a >= _parse(prev['available_at']):
                seen[key] = bar
    bars = sorted(seen.values(), key=lambda b: _parse(b['bar_time']))
    if scanned == MAX_CONTEXT_ROWS:
        notes.append(f'Context扫描已达上限{MAX_CONTEXT_ROWS}行，更早历史未参与本视图')
    if not bars:
        notes.append('所选范围内没有可见分钟记录')
    return bars, notes


def aggregate(bars: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    """5m/15m OHLC per session bucket; incomplete buckets keep partial flag."""
    out: dict[str, dict[str, Any]] = {}
    for bar in bars:
        t = _parse(bar['bar_time'])
        if t is None or not _in_session(_beijing(t)):
            continue
        t_bj = _beijing(t)
        start = _bucket_start(t_bj, minutes)
        key = start.isoformat()
        slot = out.get(key)
        if slot is None:
            slot = {'bar_time': _iso(start), 'open': bar.get('open'), 'high': bar.get('high'),
                    'low': bar.get('low'), 'close': bar.get('close'),
                    'volume': 0, 'amount': 0, 'minute_count': 0, 'complete': False}
            out[key] = slot
        slot['high'] = str(max(Decimal(str(slot['high'])), Decimal(str(bar['high']))))
        slot['low'] = str(min(Decimal(str(slot['low'])), Decimal(str(bar['low']))))
        slot['close'] = bar.get('close')
        slot['volume'] = Decimal(str(slot['volume'])) + Decimal(str(bar.get('volume') or 0))
        slot['amount'] = Decimal(str(slot['amount'])) + Decimal(str(bar.get('amount') or 0))
        slot['minute_count'] += 1
    result = sorted(out.values(), key=lambda b: b['bar_time'])
    # 上海时段（09:30-11:30 / 13:00-15:00）均与 5/15 分钟对齐，桶不会跨午休/隔夜；
    # 完整 ⇔ 分钟数齐足，缺分钟保持 partial 而不跨段凑K线。
    for slot in result:
        slot['complete'] = slot['minute_count'] >= minutes
    for slot in result:
        slot.pop('minute_count', None)
        slot['amount'] = str(slot['amount'])
        slot['volume'] = str(slot['volume'])
    return result


def gaps_between(bars: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Explicit gap notes between consecutive minutes inside one session."""
    notes: list[dict[str, str]] = []
    prev: datetime | None = None
    for bar in bars:
        t = _parse(bar['bar_time'])
        if t is None:
            continue
        t_bj = _beijing(t)
        if prev is not None and _in_session(t_bj) and _in_session(_beijing(prev)):
            delta = (t_bj - prev).total_seconds() / 60.0
            if delta > 1.0:
                notes.append({'from': _iso(prev), 'to': _iso(t),
                              'reason': f'缺{int(delta)-1}根分钟（缺口断线，不补假数据）'})
        prev = t
    return notes


def bars_response(store: WorkbenchReadStore, instrument_id: str, period: str,
                  from_utc: datetime, to_utc: datetime, as_of: datetime,
                  limit: int) -> dict[str, Any]:
    if period not in PERIODS:
        return store._envelope(source='context_snapshots', data=None, status='error', missing=['周期仅支持1m/5m/15m/timeshare'])
    period = '1m' if period == 'timeshare' else period
    limit = max(10, min(int(limit), HARD_POINTS))
    bars, missing = collect_minute_bars(store, instrument_id, from_utc, min(to_utc, as_of))
    data = {'instrument_id': instrument_id, 'period': period, 'bars': [], 'gap_notes': [],
            'volume_unit': 'share', 'capabilities': {'intraday_bars': bool(bars), 'daily_bars': False,
            'daily_bars_reason': '本地Context分钟存量不能冒充完整权威日线'}}
    if not bars:
        return store._envelope(source='context_snapshots', data=data, status='unavailable', missing=missing, as_of=to_utc)
    if len(bars)>limit:
        bars=bars[-limit:]; missing.append(f'已截取最近{limit}根，早期数据未显示')
    gaps = gaps_between(bars)
    shown = aggregate(bars, int(period[:-1])) if period in ('5m','15m') else bars
    data.update(bars=shown, gap_notes=gaps, coverage={'from':bars[0]['bar_time'], 'to':bars[-1]['bar_time'], 'count':len(shown)})
    if gaps:
        missing.append('本地分钟存量存在缺口，图表按缺口断线')
    return store._envelope(source='context_snapshots', data=data, status='partial' if missing else 'ok', missing=missing,
                           as_of=_parse(bars[-1]['bar_time']), stale_after=90)


def events_response(store: WorkbenchReadStore, instrument_id: str,
                    from_utc: datetime, to_utc: datetime) -> dict[str, Any]:
    events=[]
    missing=[]
    # Filter the requested window in SQL before applying the bounded point cap.
    specs=(('fill','fills f JOIN orders o ON o.order_id=f.order_id','o.account_id','o.instrument_id','f.executed_at','f.payload_json'),
           ('order_submitted','orders o','o.account_id','o.instrument_id','o.created_at','o.payload_json'),
           ('intent','intents o',"json_extract(o.payload_json,'$.account_id')",'o.instrument_id',"json_extract(o.payload_json,'$.created_at')",'o.payload_json'))
    with store._connect() as conn:
        if conn is None:
            return store._envelope(source='activity',data={'events':[],'instrument_id':instrument_id},status='unavailable',missing=['本地数据库不存在'])
        names=store._tables(conn)
        for kind,table,account,instrument,clock,payload in specs:
            need={'fill':{'fills','orders'},'order_submitted':{'orders'},'intent':{'intents'}}[kind]
            if not need<=names: continue
            extra = ', o.side AS fill_side' if kind == 'fill' else ''
            rows=conn.execute(f'SELECT {payload} AS payload_json{extra} FROM {table} WHERE {account}=? AND {instrument}=? AND julianday({clock})>=julianday(?) AND julianday({clock})<=julianday(?) ORDER BY julianday({clock}) LIMIT ?',
                              (store.account_id,instrument_id,_iso(from_utc),_iso(to_utc),HARD_POINTS+1)).fetchall()
            if len(rows)>HARD_POINTS: missing.append('事件超过展示上限，部分未显示')
            for row in rows[:HARD_POINTS]:
                item=json.loads(row['payload_json'])
                events.append({'type':kind,'time':item.get('executed_at') or item.get('created_at'),
                               'action':row['fill_side'] if kind == 'fill' else item.get('action') or item.get('side'),'quantity':item.get('quantity'),
                               'price':item.get('price') or item.get('limit_price'), 'id':item.get('fill_id') or item.get('order_id') or item.get('decision_id')})
    events.sort(key=lambda e:_parse(e['time']))
    return store._envelope(source='activity',data={'instrument_id':instrument_id,'events':events,
                           'mark_semantics':{'fill':'实际模拟成交','order_submitted':'订单提交（非成交）','intent':'决策意图（非成交）'}},
                           missing=missing,status='partial' if missing else 'ok',as_of=to_utc)


def account_series_response(store: WorkbenchReadStore, days: int,
                            as_of: datetime) -> dict[str, Any]:
    """Day-level equity/trading-pnl/model-cost from existing report files plus
    a real cash-only intraday curve for today. Deposits never render as profit."""
    from ..market.base import BEIJING
    days = max(1, min(int(days), 90))
    today = as_of.astimezone(BEIJING).date()
    equity_daily: list[dict[str, Any]] = []
    pnl_daily: list[dict[str, Any]] = []
    model_daily: list[dict[str, Any]] = []
    missing: list[str] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        got = store._read_report(day)
        if got is None:
            missing.append(f'{day.isoformat()} 日报缺失')
            continue
        report, _age = got
        report_at = _parse(report.get('as_of'))
        if report_at is None or report_at > as_of:
            missing.append(f'{day.isoformat()} 日报晚于查询时点或时间未知')
            continue
        ending = report.get('ending') or {}
        equity_daily.append({'date': day.isoformat(), 'equity': ending.get('equity') if ending.get('valuation_complete') is True else None,
                             'valuation_complete': ending.get('valuation_complete', True)})
        pnl_daily.append({'date': day.isoformat(), 'trading_pnl': report.get('trading_pnl'),
                          'net_external_cash_flow': report.get('net_external_cash_flow')})
        model = report.get('model_cost') or {}
        model_daily.append({'date': day.isoformat(), 'settled': model.get('settled_estimate'),
                            'pending': model.get('pending_reservation')})
    cash_intraday, cash_missing = _cash_curve_today(store, as_of)
    if cash_missing:
        missing.extend(cash_missing)
    data = {
        'equity_daily': equity_daily,
        'trading_pnl_daily': pnl_daily,
        'model_cost_daily': model_daily,
        'cash_intraday_today': cash_intraday,
        'semantics': {
            'equity': '现金（含冻结）+ 持仓市值；估值不完整的日子如实标记',
            'trading_pnl': '期末权益−期初权益−净外部资金流；入金不算收益',
            'cash_intraday': '仅现金事实重放，不含估值',
        },
    }
    return store._envelope(source='daily_report+account_events', data=data, status='partial' if missing else 'ok', missing=missing[:40], as_of=as_of)


def _cash_curve_today(store: WorkbenchReadStore, as_of: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    """Replay today's account_events cash facts (Shanghai day), amounts only."""
    from ..market.base import BEIJING
    from .read_models import _shanghai_day_bounds
    day = as_of.astimezone(BEIJING).date()
    start, stop = _shanghai_day_bounds(day)
    points: list[dict[str, Any]] = []
    missing: list[str] = []
    if not store.db_path.exists():
        return [], ['本地数据库不存在']
    import sqlite3
    with store._connect() as conn:
        if conn is None or 'account_events' not in store._tables(conn):
            return [], ['本地没有账本事件表']
        rows=conn.execute('SELECT seq, kind, amount, effective_at FROM account_events WHERE account_id=? AND julianday(effective_at)<=julianday(?) ORDER BY seq',
                          (store.account_id,_iso(as_of))).fetchall()
    if not rows or rows[0]['kind']!='deposit':
        return [], ['缺少初始入金事件，不能确认现金期初']
    cash=Decimal(0)
    opening_written=False
    for r in rows:
        at=_parse(r['effective_at'])
        if at is None or at>as_of: continue
        if r['kind'] not in {'deposit','withdraw','fill_buy','fill_sell','fee','reserve','release','rollover'}:
            return [], ['账本含未知事件，现金重建停止']
        if at>=start and not opening_written:
            points.append({'time':_iso(start),'cash':str(cash),'event':'opening'})
            opening_written=True
        try:
            amount=Decimal(str(r['amount'] or 0))
            if not amount.is_finite(): raise InvalidOperation
        except InvalidOperation:
            return [], ['账本金额无效，现金重建停止']
        if r['kind'] in ('deposit','fill_sell'): cash+=amount
        elif r['kind'] in ('withdraw','fill_buy','fee'): cash-=amount
        if at>=start:
            points.append({'time':_iso(at),'cash':str(cash),'event':r['kind']})
    if not opening_written:
        points.append({'time':_iso(start),'cash':str(cash),'event':'opening'})
    return points, missing
