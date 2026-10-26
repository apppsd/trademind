"""Production producer/consumer regressions for TASK-20261026-16; offline only."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from trademind.api.observer_app import create_observer_app
from trademind.api.read_models import WorkbenchReadStore
from trademind.api.workbench_charts import bars_response, account_series_response, collect_minute_bars, events_response, _cash_curve_today
from trademind.config import AppConfig

AT = datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)
IID = '513050.SH'


@pytest.fixture
def facts(tmp_path):
    path = tmp_path/'facts.db'
    db = sqlite3.connect(path)
    db.executescript(Path('src/trademind/storage/schema.sql').read_text(encoding='utf-8'))
    yield db, WorkbenchReadStore(path, 'local-1k')
    db.close()


def context(db, account, at=AT, close='1.1', available=None, suffix=''):
    # Exact shape emitted by context/builder.py, including "time", string volume.
    payload = {'as_of': at.isoformat(), 'portfolio': {'account_id': account},
               'features': {'values': {'close': close}},
               'aux': {'minute_bars': [{'time': at.isoformat(), 'available_at': (available or at).isoformat(),
                       'source_time': at.isoformat(), 'open': close, 'close': close, 'high': close, 'low': close,
                       'volume': '100', 'volume_unit': 'share'}]}}
    db.execute('INSERT INTO context_snapshots VALUES (?,?,?,?,?,?)',
               (account+at.isoformat()+suffix, IID, at.isoformat(), (at+timedelta(minutes=1)).isoformat(), 'hash', json.dumps(payload)))
    db.commit()


def test_explicit_cfg_is_app_scoped_without_loader_or_database_creation(tmp_path, monkeypatch):
    import trademind.config as config
    monkeypatch.setattr(config, 'load_config', Mock(side_effect=AssertionError('must not load configuration on GET')))
    clients = []
    for account in ('local-1k', 'other'):
        cfg = AppConfig(account_id=account)
        cfg.storage.url = f'sqlite:///{tmp_path/account/"missing.db"}'
        clients.append(TestClient(create_observer_app(cfg)))
    for client, expected in zip(clients, ('local-1k', 'other')):
        for route in ('overview','positions','strategies','runtime','alerts','account/series','instruments/513050.SH/bars'):
            response = client.get('/api/workbench/'+route)
            assert response.status_code == 200
            assert response.json()['account_alias'] == expected
        assert client.get('/daily-reports/other/2026-10-26.json').status_code == 404
    assert not list(tmp_path.rglob('*.db'))
    config.load_config.assert_not_called()


def test_real_context_fields_offsets_account_and_revision_visibility(facts):
    db, store = facts
    context(db, 'local-1k')
    context(db, 'other', close='99')
    context(db, 'local-1k', close='7', available=AT+timedelta(minutes=1), suffix='future')
    bars, _ = collect_minute_bars(store, IID, AT-timedelta(minutes=1), AT)
    assert len(bars) == 1 and bars[0]['close'] == '1.1'
    # Equivalent +08 storage timestamp must not be excluded by text comparison.
    db.execute('UPDATE context_snapshots SET as_of=? WHERE context_id=?',
               ('2026-10-26T10:00:00+08:00', 'local-1k'+AT.isoformat()))
    db.commit()
    env = bars_response(store,IID,'1m',AT-timedelta(minutes=1),AT,AT,100)
    assert env['data']['bars'][0]['close'] == '1.1'
    assert env['data']['volume_unit'] == 'share'
    assert {'generated_at','account_alias','partial','as_of','data_status','data','missing_reasons'} <= env.keys()


@pytest.mark.parametrize('period',['bad','1m','5m','15m','timeshare'])
def test_all_chart_responses_have_common_envelope(facts,period):
    db,store=facts
    env=bars_response(store,IID,period,AT-timedelta(hours=1),AT,AT,100)
    assert env['generated_at'] and env['account_alias']=='local-1k' and 'data' in env


def test_cash_opening_fee_and_future_events(facts):
    db,store=facts
    for seq,kind,amount,at in [(1,'deposit','1000',AT-timedelta(days=1)),(2,'fill_buy','100.10',AT),(3,'deposit','500',AT+timedelta(minutes=1))]:
        db.execute('INSERT INTO account_events(account_id,seq,kind,amount,effective_at,payload_json) VALUES (?,?,?,?,?,?)',
                   (store.account_id,seq,kind,amount,at.isoformat(),'{}'))
    db.commit()
    points,missing=_cash_curve_today(store,AT)
    assert not missing
    assert points[0]['cash']=='1000' and points[-1]['cash']=='899.90'
    assert all(datetime.fromisoformat(p['time'])<=AT for p in points)


def test_report_cost_fields_and_foreign_artifact_rejected(facts):
    _,store=facts
    store._reports_dir.mkdir(parents=True)
    report={'account_id':store.account_id,'as_of':AT.isoformat(),'generated_at':AT.isoformat(),
            'ending':{'equity':None,'valuation_complete':False},
            'model_cost':{'settled_estimate':'0.197','pending_reservation':'0.1'}}
    path=store._reports_dir/'2026-10-26.json'
    path.write_text(json.dumps(report),encoding='utf-8')
    today,_,_=store.today_report_block(AT)
    assert today['model_cost_settled']=='0.197'
    series=account_series_response(store,1,AT)
    assert series['generated_at'] and series['data']['equity_daily'][0]['equity'] is None
    assert series['data']['model_cost_daily'][0]['settled']=='0.197'
    report['account_id']='foreign'
    path.write_text(json.dumps(report),encoding='utf-8')
    assert store._read_report(AT.date()) is None


def order(db, ident, at, account='local-1k'):
    data={'order_id':ident,'account_id':account,'instrument_id':IID,'side':'BUY','created_at':at.isoformat()}
    db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)',(ident,'client-'+ident,'intent-'+ident,account,IID,'BUY','FILLED',at.isoformat(),json.dumps(data)))


def test_random_ids_chronological_cursor_and_fill_detail_identity(facts):
    db,store=facts
    for ident,minutes in [('a',0),('z',1),('m',2)]:
        order(db,ident,AT-timedelta(minutes=minutes))
        data={'fill_id':'fill-'+ident,'order_id':ident,'executed_at':(AT-timedelta(minutes=minutes)).isoformat(),'price':'1','quantity':'100'}
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?)',('fill-'+ident,'provider-'+ident,ident,data['executed_at'],json.dumps(data)))
    db.commit()
    for kind,prefix in [('order',''),('fill','fill-')]:
        first=store.activity(AT,kind=kind,limit=1)['data']
        second=store.activity(AT,kind=kind,limit=1,cursor=first['next_cursor'])['data']
        assert first['items'][0]['id']==prefix+'a'
        assert second['items'][0]['id']==prefix+'z'
        detail=store.activity_detail(AT,kind=kind,item_id=second['items'][0]['id'])
        assert detail['data']['id']==prefix+'z'


def test_decision_cursor_uses_time_and_rowid_and_real_schema(facts):
    db,store=facts
    for ident in ('a','z','m'):
        data={'decision_id':ident,'account_id':store.account_id,'created_at':AT.isoformat()}
        db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?)',(ident,1,IID,'WAIT',AT.isoformat(),'created',json.dumps(data)))
    db.commit()
    first=store.activity(AT,kind='decision',day='2026-10-26',limit=1)['data']
    second=store.activity(AT,kind='decision',day='2026-10-26',limit=1,cursor=first['next_cursor'])['data']
    assert first['items'][0]['id']=='m:1' and second['items'][0]['id']=='z:1'


def test_historical_events_filtered_before_cap(facts):
    db,store=facts
    order(db,'historical',AT-timedelta(days=1))
    for n in range(205): order(db,str(n),AT)
    db.commit()
    env=events_response(store,IID,AT-timedelta(days=1,minutes=1),AT-timedelta(days=1)+timedelta(minutes=1))
    assert env['generated_at'] and [e['id'] for e in env['data']['events']]==['historical']


def test_observer_cli_dispatch_and_no_credentials(monkeypatch):
    from trademind import runner
    from trademind.api import observer_app
    import uvicorn
    config=AppConfig()
    loader=Mock(return_value=config)
    monkeypatch.setattr(runner,'load_config',loader)
    monkeypatch.setattr(observer_app,'create_observer_app',Mock(return_value=Mock()))
    server=Mock()
    monkeypatch.setattr(uvicorn,'Server',Mock(return_value=server))
    assert runner.main(['--config','safe.toml','observer'])==0
    loader.assert_called_once_with('safe.toml',load_credentials=False)
    server.run.assert_called_once()


def test_runtime_uses_heartbeat_not_file_mtime(facts):
    _,store=facts
    old=(AT-timedelta(hours=1)).isoformat()
    store._state_file.write_text(json.dumps({'account_id':store.account_id,'pid':os.getpid(),'phase':'paper','heartbeat':old}),encoding='utf-8')
    data=store.runtime(AT)['data']
    assert data['supervisor']['active'] is False
    assert data['supervisor']['state_age_seconds']==3600


@pytest.mark.parametrize('adopt', [True, False])
def test_real_observer_process_adoption_shutdown_and_zero_business_writes(tmp_path, adopt):
    import socket
    import subprocess
    import sys
    import time
    from urllib.request import urlopen
    from trademind.execution.auto_paper import Supervisor, AdoptedObserver
    from trademind.market.calendar import TradingCalendar

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    database = tmp_path/'fast.db'
    cfg = tmp_path/'observer.toml'
    cfg.write_text(f'account_id="fast-test"\n[storage]\nurl="sqlite:///{database.as_posix()}"\n[api]\nhost="127.0.0.1"\nport={port}\n',encoding='utf-8')
    log = open(tmp_path/'observer.log','w',encoding='utf-8')
    process = subprocess.Popen([sys.executable,'-m','trademind.runner','--config',str(cfg),'observer'],
                               stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    try:
        deadline=time.monotonic()+20
        while True:
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/workbench/overview',timeout=1) as response:
                    body=json.load(response)
                break
            except OSError:
                assert process.poll() is None, 'isolated observer exited before HTTP readiness'
                if time.monotonic()>=deadline: raise AssertionError('observer did not become ready')
                time.sleep(.1)
        assert body['account_alias']=='fast-test'
        assert not database.exists()
        # A real paper Pipeline owns the same DB/account in the pytest process;
        # external providers are deterministic test doubles, never network calls.
        from test_fast_decision import make_pipe, I
        from trademind.pipeline import Pipeline
        from trademind.execution.writer_lock import WriterLockHeld
        pipe,tick=make_pipe(tmp_path)
        try:
            decision=pipe.run_decision(I,tick[0])
            assert decision.error is None and decision.order_id
            tick[0]+=timedelta(minutes=1)
            assert pipe.poll_fills(tick[0])
            with pytest.raises(WriterLockHeld): Pipeline(pipe.cfg)
            before=pipe.db.execute('SELECT COUNT(*) FROM account_events').fetchone()[0]
            budget_before=pipe.model_budget.used
            with urlopen(f'http://127.0.0.1:{port}/api/workbench/overview',timeout=2) as response:
                observed=json.load(response)
            assert observed['data']['account']['positions_count']==1
            assert pipe.db.execute('SELECT COUNT(*) FROM account_events').fetchone()[0]==before
            assert pipe.model_budget.used==budget_before
        except Exception:
            pipe.release_writer_lease()
            raise
        supervisor=Supervisor(str(cfg),tmp_path,TradingCalendar([AT.date()]),launch=Mock(side_effect=AssertionError('must adopt')),
                              account_id='fast-test',db_path=database)
        supervisor.halted=True
        if adopt:
            supervisor.ensure_observer(datetime.now(timezone.utc))
            assert isinstance(supervisor.observer,AdoptedObserver)
        else:
            supervisor.observer=process  # exact Popen handle returned by a normal launch
        # Windows venv python.exe may be a launcher that owns a child Python.
        # Verify the actual service identity, not the launcher's outer PID.
        state=json.loads((tmp_path/'observer-state.json').read_text(encoding='utf-8'))
        if adopt:
            assert supervisor.observer.pid==state['pid']
        assert state['account_id']=='fast-test' and state['running'] is True
        supervisor.stop_observer()
        assert process.wait(timeout=5)==0
        with pytest.raises(WriterLockHeld): Pipeline(pipe.cfg)
        assert supervisor.halted is True
        state=json.loads((tmp_path/'observer-state.json').read_text(encoding='utf-8'))
        assert state['running'] is False
        assert database.exists()
        assert pipe.model_budget.used==budget_before
    finally:
        if 'pipe' in locals():
            pipe.release_writer_lease()
            pipe.db.close()
        state_file=tmp_path/'observer-state.json'
        if state_file.exists():
            state=json.loads(state_file.read_text(encoding='utf-8'))
            if state.get('account_id')=='fast-test' and state.get('instance_id'):
                (tmp_path/'observer-stop.json').write_text(json.dumps({'instance_id':state['instance_id']}),encoding='utf-8')
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        log.close()


def test_catalog_t1_and_frozen_single_limit(facts):
    from trademind.api.instrument_catalog import load_catalog, attach_purchase_checks
    db,store=facts
    snap={'positions': [], 'cash':'1000','equity':'1000','market_value':'0'}
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',(store.account_id,1,'1000','0',AT.isoformat(),json.dumps(snap)))
    context(db,store.account_id,close='3')  # one lot is 300 > frozen single limit 200
    cfg=AppConfig(account_id=store.account_id)
    cfg.t0.enabled=True
    cfg.t0.whitelist=[IID,'510050.SH']
    entries=load_catalog()['instruments']
    attach_purchase_checks(entries,store,cfg,AT)
    checks={e['instrument_id']:e for e in entries}
    assert checks[IID]['purchase_check']['status']=='仅观察'
    assert any('单标的' in reason for reason in checks[IID]['purchase_check']['reasons'])
    assert checks['510050.SH']['capabilities']['tradable'] is False


def test_strategy_conditions_use_historical_context_and_do_not_call_valid_unknown_cache(facts):
    db,store=facts
    context(db,store.account_id,AT-timedelta(minutes=1),close='1')
    context(db,store.account_id,AT,close='9')
    plan={'plan_id':'p1','instrument_id':IID,'created_at':(AT-timedelta(seconds=30)).isoformat(),
          'available_at':AT.isoformat(),'valid_until':(AT+timedelta(minutes=1)).isoformat(),
          'entry_conditions':[{'field':'close','operator':'lt','value':'2'}]}
    db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',(store.account_id,'p1',AT.isoformat(),json.dumps(plan)))
    db.commit()
    found=store.strategies(AT)['data']['plans'][0]
    assert found['entry_conditions'][0]['actual']=='1'
    assert found['entry_conditions'][0]['matched'] is True
    assert found['display_status']=='状态未知'
