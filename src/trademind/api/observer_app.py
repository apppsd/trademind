"""Resident read-only observer app (TASK-20261026-07, ARCH-025).

The observer serves the workbench static bundle and the read-only
``/api/workbench/`` routes while the paper writer runs in its own process.
Hard boundaries:

- No Pipeline/provider import at request time; GET cannot start trading,
  create accounts, run migrations or take the account writer lease.
- No daily-report generation loop: reports are read from artifacts the
  trading side already produced.
- The observer's own state file (``observer-state.json``) carries pid +
  heartbeat so pages can distinguish "web readable" from "trading healthy".
- Port ownership: the observer holds the API port (8321 by default); the
  legacy trading ``serve`` no longer runs during paper sessions.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..execution.paper_run import atomic_json
from ..config import AppConfig
from .workbench_routes import router as workbench_router

_STATE_NAME = 'observer-state.json'


def _static_dir() -> Path:
    return Path(__file__).parent / 'static' / 'workbench'


@asynccontextmanager
async def observer_lifespan(application: FastAPI):
    """Own heartbeat state file; zero business writes."""
    path: Path = application.state.observer_state_path

    def _write(running: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        atomic_json(path, {'pid': os.getpid(), 'running': running,
                           'started_at': getattr(application.state, 'observer_started_at', now),
                           'heartbeat': now, 'account_id': application.state.workbench_cfg.account_id,
                           'database': str(application.state.runtime_root.resolve()),
                           'config_identity': application.state.observer_config_identity,
                           'instance_id': application.state.observer_instance_id})

    from uuid import uuid4
    application.state.observer_instance_id = uuid4().hex
    from datetime import datetime as _dt
    application.state.observer_started_at = _dt.now(timezone.utc).isoformat()
    _write(True)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(1)
            stop_file = path.parent / 'observer-stop.json'
            try:
                import json
                stop = json.loads(stop_file.read_text(encoding='utf-8'))
                if stop.get('instance_id') == application.state.observer_instance_id:
                    callback = getattr(application.state, 'request_shutdown', None)
                    if callback:
                        callback()
                    break
            except (OSError, ValueError, AttributeError):
                pass
            try:
                _write(True)
            except OSError:
                pass  # transient FS error must not kill the observer loop

    task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _write(False)


def create_observer_app(cfg: AppConfig | None = None) -> FastAPI:
    cfg = cfg or _load_cfg_safe()
    app = FastAPI(title='TradeMind Observer', version='0.1.0', lifespan=observer_lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    if cfg.storage.url.startswith('sqlite:///'):
        from ..storage.sqlite_url import sqlite_url_to_path
        root = sqlite_url_to_path(cfg.storage.url).parent
    else:
        root = Path('data/runtime')
    app.state.workbench_cfg = cfg
    from ..execution.observer_identity import observer_identity
    app.state.observer_config_identity = observer_identity(cfg)
    app.state.runtime_root = root
    app.state.observer_state_path = root / _STATE_NAME

    # HTML routes are registered BEFORE the static mounts: a mount defined
    # earlier would shadow '/workbench/' (no index fallback without html=True).
    @app.get('/', response_class=HTMLResponse)
    def index():
        return RedirectResponse('/workbench/')

    @app.get('/workbench', response_class=HTMLResponse)
    def workbench_redirect():
        return RedirectResponse('/workbench/')

    @app.get('/workbench/', response_class=HTMLResponse)
    def workbench_index():
        index_path = _static_dir() / 'index.html'
        if index_path.exists():
            return HTMLResponse(index_path.read_text(encoding='utf-8'))
        return HTMLResponse('<h1>TradeMind 观察服务</h1><p>前端静态产物尚未构建（frontend/ 下执行 npm run build）。</p>')

    @app.get('/api/observer-status')
    def observer_status():
        try:
            import json
            state = json.loads(app.state.observer_state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {'running': False}
        return {'controls_available': False, 'observer': {key: state.get(key) for key in ('pid','running','started_at','heartbeat','account_id')}}

    app.include_router(workbench_router)

    static = _static_dir()
    if static.exists():
        app.mount('/workbench', StaticFiles(directory=str(static), check_dir=False),
                  name='workbench')

    return app


def _load_cfg_safe() -> AppConfig:
    from ..config import load_config
    return load_config(load_credentials=False)
