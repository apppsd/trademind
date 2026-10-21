"""FastAPI management app (TASK-21).

Read endpoints need no token. Mutating control endpoints (kill switch, mode)
require the control token (config api.token or TRADEMIND_API_TOKEN); without
a token configured they are disabled entirely. No broker credentials or raw
provider responses are ever exposed here.
"""

from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from ..config import AppConfig, load_config
from ..pipeline import Pipeline
from ..storage import AuditSink, Database

@asynccontextmanager
async def daily_report_lifespan(application):
    """Refresh financial artifacts only; no model providers or account writes."""
    async def loop():
        from ..reports.daily import generate_for_config
        from ..market.base import BEIJING
        from datetime import timedelta
        while True:
            cfg = _state.get('cfg')
            if cfg:
                today = datetime.now(timezone.utc).astimezone(BEIJING).date()
                for day in (today - timedelta(days=1), today):
                    try:
                        await asyncio.to_thread(generate_for_config, cfg, day)
                        _state['daily_report_status'] = 'generated'
                    except Exception as exc:
                        _state['daily_report_status'] = type(exc).__name__
            await asyncio.sleep(60)
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="TradeMind Workbench", version="0.1.0", lifespan=daily_report_lifespan)
_state: dict[str, Any] = {"pipeline": None, "cfg": None}


@app.get('/reports/daily', response_class=HTMLResponse)
def daily_report_today():
    return daily_report_html(None)


@app.get('/reports/daily/{day}', response_class=HTMLResponse)
def daily_report_html(day: str | None):
    from ..reports.daily import generate_for_config
    cfg = _state.get('cfg')
    if cfg is None:
        raise HTTPException(503, 'report configuration unavailable')
    try:
        report, path = generate_for_config(cfg, day)
        return HTMLResponse(Path(path).read_text(encoding='utf-8'))
    except ValueError:
        raise HTTPException(400, 'invalid report date or account data') from None
    except Exception:
        raise HTTPException(503, 'daily report temporarily unavailable') from None


@app.get('/api/reports/daily/{day}')
def daily_report_json(day: str):
    from ..reports.daily import generate_for_config
    cfg = _state.get('cfg')
    if cfg is None:
        raise HTTPException(503, 'report configuration unavailable')
    try:
        return generate_for_config(cfg, day)[0]
    except ValueError:
        raise HTTPException(400, 'invalid report date or account data') from None
    except Exception:
        raise HTTPException(503, 'daily report temporarily unavailable') from None


@app.get('/api/market-observations')
def market_observations(kind: str | None = None, limit: int = 50):
    """Read the latest local observation, without providers or model startup."""
    from ..market.multi_asset import MARKETS
    if kind is not None and kind not in MARKETS:
        raise HTTPException(400, 'unknown market')
    if not 1 <= limit <= 500:
        raise HTTPException(400, 'limit must be 1..500')
    target = Path('data/runtime/market-observation.json')
    if not target.exists():
        return {'status': 'not_scanned', 'assets': [], 'sources': [], 'instrument_count': 0,
                'mode': 'observation_only'}
    try:
        report = json.loads(target.read_text(encoding='utf-8'))
        report['assets'] = [a for a in report['assets'] if kind is None or a['kind'] == kind][:limit]
        report['report_age_seconds'] = max(0, datetime.now(timezone.utc).timestamp()-target.stat().st_mtime)
        return report
    except (ValueError, KeyError, OSError):
        raise HTTPException(503, 'market observation report unavailable') from None


def get_pipeline() -> Pipeline:
    if _state.get("pipeline") is None:
        init_app(load_config())
    return _state["pipeline"]


def init_app(cfg: AppConfig) -> Pipeline:
    pipe = Pipeline(cfg)
    pipe.ensure_account()
    _state["pipeline"] = pipe
    _state["cfg"] = cfg
    return pipe


def require_token(x_api_token: str = Header(default="")) -> None:
    cfg: AppConfig = _state.get("cfg") or load_config()
    if not cfg.api.token:
        raise HTTPException(403, "control endpoints disabled: no API token configured")
    if x_api_token != cfg.api.token:
        raise HTTPException(401, "invalid control token")


@app.get("/api/status")
def status(p=Depends(get_pipeline)):
    cfg: AppConfig = _state["cfg"]
    automatic = None
    intraday_health = None
    if cfg.storage.url.startswith('sqlite:///'):
        from ..storage.sqlite_url import sqlite_url_to_path
        db_path = sqlite_url_to_path(cfg.storage.url)
        state_path = db_path.parent/'auto-paper-state.json'
        try:
            from ..execution.auto_paper import healthy
            data = json.loads(state_path.read_text(encoding='utf-8'))
            automatic = {key:data.get(key) for key in ('phase','halted','message','heartbeat','retry_at','health_status','intraday_health')}
            automatic['active'] = healthy(state_path)
        except (OSError, ValueError, TypeError):
            pass
        try:
            from ..execution.paper_run import read_intraday_health
            intraday_health = read_intraday_health(
                db_path,
                cfg.account_id,
                halted=bool(automatic and automatic.get('halted')),
                t0_cfg=getattr(cfg, 't0', None),
            )
            if automatic is not None and not automatic.get('health_status'):
                automatic['health_status'] = intraday_health['health_status']
        except Exception:
            pass
    ks = p.repo.get_kill_switch()
    stub = p.llm_is_stub or p.jev_is_stub
    return {
        "mode": p.mode,
        "automatic": automatic,
        "intraday_health": intraday_health,
        "account_id": cfg.account_id,
        "universe": cfg.universe,
        "universe_version": cfg.universe_version,
        "kill_switch": ks,
        "llm_provider": p.llm.kind.value,
        "llm_model": p.llm.model,
        "jev_provider": p.jev.kind.value,
        "llm_capability_verified": p.llm.capability_verified,
        "jev_capability_verified": p.jev.capability_verified,
        "is_stub_chain": stub,
        "stub_warning": ("显式 stub：无模型参与，结果仅验证流水线形状，"
                         "不得当作模型表现" if stub else None),
        "live_available": False,
        "orchestrator": p.orchestrator.snapshot(),
        "llm_budget": {"used": p.llm.budget.used, "limit": p.llm.budget.limit},
        "t0": {
            "enabled": bool(getattr(cfg, "t0", None) and cfg.t0.enabled),
            "profile": getattr(cfg.t0, "profile", "jev_deepseek") if getattr(cfg, "t0", None) else None,
            "whitelist": getattr(cfg.t0, "whitelist", []) if getattr(cfg, "t0", None) else [],
            "initial_cash": str(getattr(cfg.t0, "initial_cash", "500")) if getattr(cfg, "t0", None) else "500",
        },
    }


@app.get("/api/account")
def account(p=Depends(get_pipeline)):
    prices = {}
    for inst in _state["cfg"].universe:
        try:
            prices[inst] = p.reference_price(inst, datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            pass
    snap = p.ledger.snapshot(datetime.now(timezone.utc), prices)
    return json.loads(snap.model_dump_json())


@app.get("/api/orders")
def orders(limit: int = 50, p=Depends(get_pipeline)):
    rows = p.repo.db.query("SELECT payload_json FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
    return [json.loads(r["payload_json"]) for r in rows]


@app.get("/api/decisions")
def decisions(limit: int = 50, p=Depends(get_pipeline)):
    rows = p.repo.db.query("SELECT payload_json FROM intents ORDER BY rowid DESC LIMIT ?", (limit,))
    return [json.loads(r["payload_json"]) for r in rows]


@app.get("/api/instrument-names")
def instrument_names():
    """Verified Chinese fund names (symbol -> name) for workbench rendering."""
    from ..market.profiles import etf_display_names

    return etf_display_names()


@app.get("/api/theses")
def theses(limit: int = 50, p=Depends(get_pipeline)):
    rows = p.repo.db.query("SELECT payload_json FROM theses ORDER BY rowid DESC LIMIT ?", (limit,))
    return [json.loads(r["payload_json"]) for r in rows]


@app.get("/api/judgments")
def judgments(limit: int = 50, p=Depends(get_pipeline)):
    rows = p.repo.db.query("SELECT payload_json FROM judgment_batches ORDER BY rowid DESC LIMIT ?", (limit,))
    return [json.loads(r["payload_json"]) for r in rows]


@app.get("/api/memories")
def memories(p=Depends(get_pipeline)):
    return p.memory.visible(datetime.now(timezone.utc))


@app.get("/api/audit")
def audit(limit: int = 100, p=Depends(get_pipeline)):
    return p.audit.tail(limit)


@app.get("/api/trace/{trace_id}")
def trace(trace_id: str, p=Depends(get_pipeline)):
    """One trace across the whole chain: context → thesis → judgments →
    intent → review → guard → order → fills."""
    out: dict[str, Any] = {"trace_id": trace_id}
    queries = {
        "model_calls": "SELECT payload_json FROM model_calls WHERE trace_id=?",
        "contexts": ("SELECT c.payload_json FROM context_snapshots c JOIN json_each(c.payload_json,'$.trace_id') "
                     "WHERE json_each.value=?"),
    }
    for name, sql in queries.items():
        out[name] = [json.loads(r[0]) for r in p.repo.db.query(sql, (trace_id,))]
    for table, key in (("theses", "trace_id"), ("intents", "trace_id"),
                       ("orders", "trace_id")):
        rows = p.repo.db.query(
            f"SELECT payload_json FROM {table} WHERE json_extract(payload_json,'$.trace_id')=?",
            (trace_id,))
        out[table] = [json.loads(r[0]) for r in rows]
    return out


@app.post("/api/control/kill_switch")
def kill_switch(engaged: bool, reason: str = "", _=Depends(require_token),
                p=Depends(get_pipeline)):
    p.repo.set_kill_switch(engaged, reason)
    p.audit.record_critical("api", "kill_switch", str(engaged), {"reason": reason})
    return p.repo.get_kill_switch()


@app.post("/api/control/decide/{instrument_id}")
def decide(instrument_id: str, _=Depends(require_token), p=Depends(get_pipeline)):
    if p.repo.get_kill_switch()["engaged"]:
        raise HTTPException(409, "kill switch engaged")
    res = p.run_decision(instrument_id, datetime.now(timezone.utc))
    return res.summary()


@app.post("/api/control/poll_fills")
def poll_fills(_=Depends(require_token), p=Depends(get_pipeline)):
    filled = p.poll_fills(datetime.now(timezone.utc))
    return {"filled": filled}


_WEB = Path(__file__).parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse)
def index():
    if _WEB.exists():
        return HTMLResponse(_WEB.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>TradeMind</h1><p>web assets missing</p>")
