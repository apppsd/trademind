"""Workbench read-only API routes (TASK-20261026-05, ARCH-025).

New namespace ``/api/workbench/`` beside the legacy ``/api/*``. Endpoints are
snapshot reads: no pipeline init, no providers, no migrations, no writer
lease, no account creation. The trading account comes from configuration and
cannot be switched by clients.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from ..config import AppConfig
from ..storage.sqlite_url import sqlite_url_to_path
from .instrument_catalog import attach_context_capabilities, attach_purchase_checks, load_catalog, search_instruments
from .read_models import WorkbenchReadStore
from . import workbench_charts as charts

router = APIRouter(prefix='/api/workbench')


def _configured_store(request: Request) -> tuple[WorkbenchReadStore, AppConfig]:
    cfg = getattr(request.app.state, 'workbench_cfg', None)
    if cfg is None:
        raise HTTPException(503, '工作台未绑定账户配置')
    if not cfg.storage.url.startswith('sqlite:///'):
        raise HTTPException(503, '工作台只读层仅支持SQLite')
    return WorkbenchReadStore(sqlite_url_to_path(cfg.storage.url), cfg.account_id), cfg


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get('/overview')
def workbench_overview(request: Request):
    store, _cfg = _configured_store(request)
    return store.overview(_now())


@router.get('/instruments')
def workbench_instruments(request: Request, q: str | None = Query(default=None, max_length=60)):
    store, _cfg = _configured_store(request)
    catalog = load_catalog()
    try:
        attach_context_capabilities(catalog['instruments'], store)
        attach_purchase_checks(catalog['instruments'], store, _cfg, _now())
    except Exception:
        catalog['source_notes']['context_coverage'] = '本地能力探测失败，曲线能力暂时未知'
    instruments = search_instruments(catalog['instruments'], q or '')
    catalog['instruments'] = instruments
    return catalog


@router.get('/positions')
def workbench_positions(request: Request):
    store, _cfg = _configured_store(request)
    return store.positions(_now())


@router.get('/strategies')
def workbench_strategies(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    store, _cfg = _configured_store(request)
    return store.strategies(_now(), limit, _cfg)


@router.get('/activity')
def workbench_activity(request: Request, kind: str = Query(default='fill'),
                       instrument_id: str | None = Query(default=None, max_length=30),
                       day: str | None = Query(default=None, min_length=8, max_length=10),
                       limit: int = Query(default=50, ge=1, le=200),
                       cursor: str | None = Query(default=None, max_length=200)):
    store, _cfg = _configured_store(request)
    return store.activity(_now(), kind=kind, instrument_id=instrument_id,
                          day=day, limit=limit, cursor=cursor)


@router.get('/activity/{kind}/{item_id}')
def workbench_activity_detail(request: Request, kind: str, item_id: str):
    store, _cfg = _configured_store(request)
    return store.activity_detail(_now(), kind=kind, item_id=item_id)


@router.get('/runtime')
def workbench_runtime(request: Request):
    store, _cfg = _configured_store(request)
    return store.runtime(_now())


@router.get('/alerts')
def workbench_alerts(request: Request):
    store, _cfg = _configured_store(request)
    return store.alerts(_now())


# ---------------------------------------------------------------- charts (26-06)

def _parse_day(day: str | None) -> tuple[datetime, datetime] | None:
    """Shanghai day or ISO range → (from_utc, to_utc); None → default 1 day."""
    from ..market.base import BEIJING
    now = datetime.now(timezone.utc)
    if not day:
        start = datetime.combine(now.astimezone(BEIJING).date(), datetime.min.time(), BEIJING)
        return start.astimezone(timezone.utc), now
    try:
        if 'T' in day:  # ISO timestamp → single point
            point = datetime.fromisoformat(day.replace('Z', '+00:00'))
            if point.utcoffset() is None:
                raise ValueError('timezone required')
            point = min(point, now)
            return point - timedelta(minutes=240), point
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(), BEIJING)
        return start.astimezone(timezone.utc), min(start + timedelta(days=1), now).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(400, '日期格式无效；可用 YYYY-MM-DD 或 ISO 时间')


@router.get('/instruments/{instrument_id}/bars')
def workbench_bars(request: Request, instrument_id: str,
                   period: str = Query(default='1m'),
                   day: str | None = Query(default=None, max_length=40),
                   limit: int = Query(default=charts.DEFAULT_POINTS, ge=10, le=charts.HARD_POINTS)):
    store, _cfg = _configured_store(request)
    rng = _parse_day(day)
    assert rng is not None
    from_utc, to_utc = rng
    return charts.bars_response(store, instrument_id, period, from_utc, to_utc,
                                datetime.now(timezone.utc), limit)


@router.get('/instruments/{instrument_id}/events')
def workbench_events(request: Request, instrument_id: str,
                     day: str | None = Query(default=None, max_length=40)):
    store, _cfg = _configured_store(request)
    rng = _parse_day(day)
    assert rng is not None
    from_utc, to_utc = rng
    return charts.events_response(store, instrument_id, from_utc, to_utc)


@router.get('/account/series')
def workbench_account_series(request: Request, days: int = Query(default=10, ge=1, le=90)):
    store, _cfg = _configured_store(request)
    return charts.account_series_response(store, days, datetime.now(timezone.utc))


@router.get('/reports/{day}')
def workbench_report(request: Request, day: str, fmt: str = Query(default='json', pattern='^(json|md)$')):
    """Read an EXISTING daily-report artifact; never generates one."""
    store, _cfg = _configured_store(request)
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, '日期格式应为 YYYY-MM-DD') from None
    if len(day) != 10:
        raise HTTPException(400, '日期格式应为 YYYY-MM-DD')
    if store._read_report(date.fromisoformat(day)) is None:
        raise HTTPException(404, '该日期日报不存在或不属于配置账户')
    path = store._reports_dir / f'{day}.{"json" if fmt == "json" else "md"}'
    if not path.exists():
        raise HTTPException(404, '该日期日报产物不存在（不触发生成）')
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        raise HTTPException(503, '日报文件暂时不可读') from None
    if fmt == 'json':
        import json as _json
        try:
            body = _json.loads(text)
            if body.get('account_id') != store.account_id:
                raise HTTPException(404, '日报不属于配置账户')
            return body
        except ValueError:
            raise HTTPException(503, '日报文件内容异常') from None
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(text, media_type='text/markdown; charset=utf-8')
