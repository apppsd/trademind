import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from trademind.storage import Database
from trademind.reports.daily import generate_report, write_report, generate_for_config

NOW = datetime.fromisoformat('2026-10-12T10:00:00+08:00')
ACCOUNT = 'paper-test'


@pytest.fixture
def ledger_db(tmp_path):
    db = Database('sqlite:///'+str(tmp_path/'daily.db'))
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)', (ACCOUNT,1,'500','0',NOW.isoformat(),'{}'))
    yield db
    db.close()


def event(db, seq, kind, at, amount='0', qty=None, price=None, fee='0', iid='513050.SH'):
    e = {'account_id':ACCOUNT, 'seq':seq, 'kind':kind, 'effective_at':at,
         'amount':amount, 'quantity':qty, 'currency':'CNY', 'instrument_id':iid,
         'note':f'price={price};fee={fee}' if price else '', 'order_id':f'order-{seq}'}
    db.execute('INSERT INTO account_events(account_id,seq,kind,instrument_id,amount,effective_at,payload_json) VALUES (?,?,?,?,?,?,?)',
               (ACCOUNT,seq,kind,iid,amount,at,json.dumps(e)))


def fund(db):
    event(db,1,'deposit','2026-10-11T10:00:00+08:00',amount='500')


def budget(db, settled='0.03', pending='0.01', day='2026-10-12', key=None):
    db.execute('INSERT INTO coalesce_state VALUES (?,?,?)', (key or 'model-budget:'+ACCOUNT,NOW.isoformat(),
               json.dumps({'day':day,'used':2,'cost_settled_cny':settled,'cost_pending_cny':pending})))


def test_complete_round_trip_cash_and_model_cost(ledger_db):
    db=ledger_db
    fund(db)
    event(db,2,'fill_buy','2026-10-12T09:40:00+08:00',qty='100',price='2',fee='1')
    event(db,3,'fill_sell','2026-10-12T09:45:00+08:00',qty='100',price='2.1',fee='1')
    budget(db)
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['opening']['cash']=='500' and r['ending']['cash']=='508.0'
    assert r['trading_pnl']=='8.0' and r['realized_pnl']=='8.0000'
    assert r['trading_fees']=='2'
    assert r['net_after_settled_model_estimate']=='7.97'
    assert r['net_after_model_reservation_bound']=='7.96'
    assert r['trades'][0]['price']=='2' and r['trades'][0]['cash_after_fill']=='299'
    assert r['model_cost']['pending_reservation']=='0.01'
    # Report generation is read-only; fixture's stored cash isn't rewritten.
    assert db.query('SELECT cash FROM account_state')[0]['cash']=='500'


def test_partial_position_not_confused_with_cash_or_double_fees(ledger_db):
    db=ledger_db
    fund(db)
    event(db,2,'fill_buy','2026-10-12T09:40:00+08:00',qty='100',price='2',fee='1')
    event(db,3,'fill_sell','2026-10-12T09:45:00+08:00',qty='50',price='2.2',fee='0.5')
    ctx={'instrument_id':'513050.SH','as_of':NOW.isoformat(),
         'aux':{'minute_bars':[{'time':'2026-10-12T09:59:00+08:00','close':'2.15','available_at':NOW.isoformat()}]}}
    db.execute('INSERT INTO context_snapshots VALUES (?,?,?,?,?,?)',('ctx','513050.SH',NOW.isoformat(),NOW.isoformat(),'hash',json.dumps(ctx)))
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['ending']['cash']=='408.5'
    assert r['ending']['equity']=='516.00'
    assert r['trading_pnl']=='16.00'
    assert r['realized_pnl']=='9.0000'
    assert r['ending']['positions'][0]['unrealized_pnl']=='7.0000'


def test_external_funding_and_initial_deposit_not_profit(ledger_db):
    db=ledger_db
    event(db,1,'deposit','2026-10-12T09:30:00+08:00',amount='500')
    event(db,2,'deposit','2026-10-12T09:35:00+08:00',amount='200')
    event(db,3,'withdraw','2026-10-12T09:40:00+08:00',amount='100')
    event(db,4,'reserve','2026-10-12T09:45:00+08:00',amount='50')
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['opening']['cash']=='500'
    assert r['net_external_cash_flow']=='100'
    assert r['ending']['cash']=='600'
    assert r['ending']['available_cash']=='550'
    assert r['trading_pnl']=='0'


def test_missing_marks_are_unknown_not_cost_basis(ledger_db):
    db=ledger_db
    fund(db)
    event(db,2,'fill_buy','2026-10-11T10:30:00+08:00',qty='100',price='2',fee='1')
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['opening']['equity'] is None
    assert r['ending']['equity'] is None
    assert r['trading_pnl'] is None
    assert r['realized_pnl']=='0'


def call(db, ident, at, cost):
    c={'created_at':at,'role':'strategy','provider':'deepseek','model':'deepseek-flash',
       'status':'ok','cost_amount':cost,'cost_currency':'CNY','input_tokens':100,'output_tokens':20}
    db.execute('INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)',
               (ident,ident,'strategy','deepseek','deepseek-flash','ok',at,json.dumps(c)))


def test_shanghai_date_and_account_budget_history(ledger_db):
    db=ledger_db
    fund(db)
    call(db,'old','2026-10-11T15:59:00Z','0.2')
    call(db,'today','2026-10-11T16:01:00Z',None)
    budget(db,'0.4','0.7',key='model-budget:'+ACCOUNT+':history:2026-10-12')
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['model_cost']['audit_rows']==1
    assert r['model_cost']['settled_estimate']=='0.4'
    assert r['model_cost']['pending_reservation']=='0.7'


def test_missing_cost_and_multiaccount_not_attributed_as_zero(ledger_db):
    db=ledger_db
    fund(db)
    call(db,'today','2026-10-12T01:00:00Z',None)
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['model_cost']['pending_reservation'] is None
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)',('other',1,'1','0',NOW.isoformat(),'{}'))
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    assert r['model_cost']['settled_estimate'] is None
    assert r['model_cost']['audit_scope']=='database_unattributed'


def test_report_files_and_strategy_escape(ledger_db,tmp_path,monkeypatch):
    db=ledger_db
    fund(db)
    p={'plan_id':'plan-1','instrument_id':'513050.SH','available_at':'2026-10-12T09:40:00+08:00',
       'prompt_version':'strategy-3','trend':'<script>bad()</script>','reason':'token=YOUR_TOKEN_HERE',
       'allowed_actions':['WAIT']}
    db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',(ACCOUNT,p['plan_id'],p['available_at'],json.dumps(p)))
    import requests
    monkeypatch.setattr(requests.Session,'request',Mock(side_effect=AssertionError('no network')))
    r=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    path=Path(write_report(r,tmp_path/'reports'))
    assert '<script>' not in path.read_text(encoding='utf-8')
    assert 'YOUR_TOKEN_HERE' not in path.with_suffix('.json').read_text(encoding='utf-8')
    assert path.with_suffix('.md').exists()
    assert not list(path.parent.glob('*.tmp'))
    assert len(r['strategies'])==1


def test_future_and_unsafe_account_rejected(ledger_db):
    with pytest.raises(ValueError):
        generate_report(ledger_db.path,ACCOUNT,'2026-10-13',NOW)
    with pytest.raises(ValueError):
        generate_report(ledger_db.path,'../escape','2026-10-12',NOW)


def test_workbench_auto_generates_today_and_yesterday(monkeypatch):
    import asyncio
    from datetime import timedelta
    from trademind.api.app import daily_report_lifespan, app, _state
    from trademind.market.base import BEIJING
    dates=[]
    def fake_generate(cfg, day):
        dates.append(day)
        return {}, 'unused'
    monkeypatch.setitem(_state,'cfg',object())
    monkeypatch.setattr('trademind.reports.daily.generate_for_config',fake_generate)
    async def check():
        async with daily_report_lifespan(app):
            for _ in range(30):
                if len(dates)>=2:
                    break
                await asyncio.sleep(.01)
            today=datetime.now(BEIJING).date()
            assert dates==[today-timedelta(days=1),today]
    asyncio.run(check())


def test_report_web_routes_and_bad_date(tmp_path):
    from fastapi.testclient import TestClient
    from trademind.api.app import app,init_app
    from trademind.config import AppConfig
    cfg=AppConfig()
    cfg.storage.url='sqlite:///'+str(tmp_path/'web.db')
    init_app(cfg)
    client=TestClient(app)
    assert client.get('/reports/daily').status_code==200
    assert client.get('/reports/daily/not-a-date').status_code==400
    from trademind.market.base import BEIJING
    today=datetime.now(BEIJING).date().isoformat()
    result=client.get('/api/reports/daily/'+today)
    assert result.status_code==200
    assert result.json()['timezone']=='Asia/Shanghai'


def test_paper_finish_report_hook_does_not_block_transactions(tmp_path,monkeypatch):
    from test_fast_decision import make_pipe,I
    from trademind.execution.paper_run import PaperRun
    pipe,tick=make_pipe(tmp_path)
    run=PaperRun(pipe,[I],str(tmp_path/'run.json'))
    report=run.finish('blocked',2,[],{'ok':False},{'unconsumed_result':False,'worker_active':False})
    assert report['daily_report']['status']=='generated'
    assert Path(report['daily_report']['html']).exists()
    monkeypatch.setattr('trademind.reports.daily.generate_for_config',Mock(side_effect=OSError()))
    report=run.finish('blocked',2,[],{'ok':False},{'unconsumed_result':False,'worker_active':False})
    assert report['daily_report']=={'status':'failed','error_type':'OSError'}
    assert report['status']=='blocked'


def test_account_underscore_is_literal_not_sql_wildcard(ledger_db):
    from trademind.reports.daily import read_snapshot
    db=ledger_db
    for key in ('model-budget:paper_a:history:2026-10-12','model-budget:paperXa:history:2026-10-12'):
        db.execute('INSERT INTO coalesce_state VALUES (?,?,?)',(key,NOW.isoformat(),'{}'))
    for key in ('strategy-plan:paper_a:513050.SH','strategy-plan:paperXa:518880.SH'):
        db.execute('INSERT INTO coalesce_state VALUES (?,?,?)',(key,NOW.isoformat(),'{}'))
    snapshot=read_snapshot(db.path,'paper_a')
    assert len(snapshot['budgets'])==1
    assert snapshot['budgets'][0]['key']=='model-budget:paper_a:history:2026-10-12'
    assert len(snapshot['cached'])==1


def test_chinese_presentation_preserves_archived_protocol(ledger_db):
    import hashlib
    from trademind.reports.daily import render_markdown
    from trademind.reports.locale import explanation, condition_text
    db=ledger_db
    fund(db)
    original='Price is below VWAP.'
    p={'plan_id':'old-plan','instrument_id':'513050.SH','available_at':NOW.isoformat(),
       'trend':original,'allowed_actions':['WAIT'],
       'entry_conditions':[{'field':'close','operator':'lt','value':'2'}]}
    db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',(ACCOUNT,p['plan_id'],p['available_at'],json.dumps(p)))
    folder=db.path.parent/'daily-reports'
    folder.mkdir()
    (folder/'translations.zh.json').write_text(json.dumps({hashlib.sha256(original.encode()).hexdigest():'价格低于成交量加权均价。'},ensure_ascii=False),encoding='utf-8')
    report=generate_report(db.path,ACCOUNT,'2026-10-12',NOW)
    rendered=render_markdown(report)
    assert '价格低于成交量加权均价。' in rendered
    assert '允许动作：观望' in rendered and 'Price is below' not in rendered
    assert report['strategies'][0]['trend']==original
    assert report['strategies'][0]['allowed_actions']==['WAIT']
    assert original in db.query('SELECT payload_json FROM strategy_plan_history')[0]['payload_json']
    assert explanation('global model evaluation cooldown')=='模型判断处于全局冷却期'
    assert condition_text(p['entry_conditions'])=='收盘价小于2'


def test_budget_used_and_audit_attempts_reconciliation(ledger_db):
    from trademind.reports.daily import render_markdown
    db = ledger_db
    fund(db)
    # Reproduce 2026-10-12 full day: 6 probe calls (attempts=1) + 4 strategy calls (attempts=2) = 10 rows, 14 HTTP requests
    for i in range(6):
        c = {'created_at': '2026-10-12T09:50:00+08:00', 'role': 'probe', 'provider': 'deepseek',
             'model': 'deepseek-flash', 'status': 'ok', 'attempts': 1, 'cost_amount': '0.001', 'cost_currency': 'CNY'}
        db.execute('INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)',
                   (f'probe-{i}', f'tr-p-{i}', 'probe', 'deepseek', 'deepseek-flash', 'ok', c['created_at'], json.dumps(c)))
    for i in range(4):
        c = {'created_at': '2026-10-12T09:51:00+08:00', 'role': 'strategy', 'provider': 'deepseek',
             'model': 'deepseek-flash', 'status': 'ok', 'attempts': 2, 'cost_amount': '0.025', 'cost_currency': 'CNY'}
        db.execute('INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)',
                   (f'strat-{i}', f'tr-s-{i}', 'strategy', 'deepseek', 'deepseek-flash', 'ok', c['created_at'], json.dumps(c)))
    db.execute('INSERT INTO coalesce_state VALUES (?,?,?)',
               ('model-budget:' + ACCOUNT, NOW.isoformat(),
                json.dumps({'day': '2026-10-12', 'used': 14, 'probe': 6, 'strategy': 8,
                            'cost_settled_cny': '0.106', 'cost_pending_cny': '0'})))
    report = generate_report(db.path, ACCOUNT, '2026-10-12', NOW)
    mc = report['model_cost']
    assert mc['requested_count'] == 14
    assert mc['audit_rows'] == 10
    assert mc['audit_attempts'] == 14
    assert 'count_semantics' in mc
    assert any('预算请求数（14）按HTTP计费请求累计，审计行数（10）按逻辑调用留档' in w for w in report['warnings'])
    assert not any('不一致' in w for w in report['warnings'])
    md = render_markdown(report)
    assert '预算请求数：14；审计行数：10（累计尝试数：14）' in md


def test_paper_run_and_provider_attempt_accounting_on_failures(tmp_path):
    from decimal import Decimal
    import requests
    from test_fast_decision import make_pipe, I
    from trademind.errors import ProviderError, ProviderTimeout
    from trademind.execution.paper_run import PaperRun
    from trademind.models.base import CallAuditor
    from trademind.models.deepseek import DeepSeekProvider

    pipe, tick = make_pipe(tmp_path)
    auditor = CallAuditor(pipe.repo)
    llm = DeepSeekProvider('TEST_ONLY', budget=pipe.model_budget, auditor=auditor, max_repair_attempts=1)
    run = PaperRun(pipe, [I], str(tmp_path / 'run.json'))

    # 1. Success with 1 repair retry -> 1 audit row, attempts=2, budget.used += 2
    def resp(text):
        r = Mock(status_code=200)
        r.json.return_value = {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
                               'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}
        return r
    llm.session.post = Mock(side_effect=[resp('not-json'), resp('{"ok":true}')])
    out = llm.complete(role='strategy', system='json', user='u', trace_id='t1')
    assert out.call.attempts == 2
    assert pipe.model_budget.used == 2

    # 2. Timeout -> 1 audit row, attempts=1, budget.used += 1
    llm.session.post = Mock(side_effect=requests.exceptions.Timeout('timeout'))
    with pytest.raises(ProviderTimeout):
        llm.complete(role='strategy', system='json', user='u', trace_id='t2')
    assert pipe.model_budget.used == 3

    # 3. Budget exhausted before transport -> 1 audit row with attempts=0, budget.used unchanged
    pipe.cfg.t0.daily_decision_call_budget = 3
    pipe.model_budget.limit = 3
    llm.session.post = Mock(side_effect=AssertionError('must not call HTTP'))
    with pytest.raises(ProviderError):
        llm.complete(role='strategy', system='json', user='u', trace_id='t3')
    assert pipe.model_budget.used == 3

    finished = run.finish('failed', 1, [], {'ok': True}, {'unconsumed_result': True, 'worker_active': False})
    assert finished['model_calls'] == 3
    assert finished['model_call_attempts'] == 3
    assert finished['budget_requests_delta'] == 3
    assert finished['model_pending'] is True


# -- TASK-20261013-16: bounded health/report history reads ---------------------

def history_rows(days, account=ACCOUNT):
    """Synthetic pre-window history: one call/context/intent/plan per past day."""
    from datetime import timedelta, timezone as tz
    calls, ctxs, intents, plans = [], [], [], []
    for i in range(1, days + 1):
        at = (NOW - timedelta(days=i)).astimezone(tz.utc).isoformat()
        calls.append((f'call-old-{i}', f'tr-old-{i}', 'strategy', 'deepseek', 'deepseek-flash', 'ok', at,
                      json.dumps({'created_at': at, 'role': 'strategy', 'provider': 'deepseek',
                                  'model': 'deepseek-flash', 'status': 'ok', 'attempts': 1,
                                  'cost_amount': '0.5', 'cost_currency': 'CNY'})))
        ctxs.append((f'ctx-old-{i}', '513050.SH', at, at, 'h',
                     json.dumps({'instrument_id': '513050.SH', 'as_of': at, 'aux': {'minute_bars': []}})))
        intents.append((f'dec-old-{i}', 1, '513050.SH', 'BUY', at, 'SAVED',
                        json.dumps({'decision_id': f'dec-old-{i}', 'created_at': at,
                                    'context_id': f'ctx-old-{i}', 'intent_version': 1,
                                    'instrument_id': '513050.SH', 'action': 'BUY', 'reason': 'old'}), at))
        plans.append((account, f'plan-old-{i}', at,
                      json.dumps({'plan_id': f'plan-old-{i}', 'instrument_id': '513050.SH',
                                  'available_at': at, 'allowed_actions': ['WAIT']})))
    return calls, ctxs, intents, plans


def today_content(db):
    """One day's full report content: funding, partial position, mark, plan,
    calls (one missing CNY cost), an intent and the day budget with a
    cross-day pending reservation."""
    fund(db)
    event(db, 2, 'fill_buy', '2026-10-12T09:40:00+08:00', qty='100', price='2', fee='1')
    mark = {'time': '2026-10-12T09:59:00+08:00', 'close': '2.15', 'available_at': '2026-10-12T09:59:00+08:00'}
    ctx = {'instrument_id': '513050.SH', 'as_of': mark['time'], 'aux': {'minute_bars': [mark]}}
    db.execute('INSERT INTO context_snapshots VALUES (?,?,?,?,?,?)',
               ('ctx-today', '513050.SH', ctx['as_of'], ctx['as_of'], 'h', json.dumps(ctx)))
    p = {'plan_id': 'plan-today', 'instrument_id': '513050.SH',
         'available_at': '2026-10-12T09:40:00+08:00', 'allowed_actions': ['WAIT']}
    db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',
               (ACCOUNT, p['plan_id'], p['available_at'], json.dumps(p)))
    call(db, 'call-today-1', '2026-10-12T01:00:00+00:00', '0.02')
    call(db, 'call-today-2', '2026-10-12T01:05:00+00:00', None)
    intent = {'decision_id': 'dec-today', 'created_at': '2026-10-12T01:01:00+00:00',
              'context_id': 'ctx-today', 'intent_version': 1, 'instrument_id': '513050.SH',
              'action': 'BUY', 'reason': 'fresh signal'}
    db.execute('INSERT INTO intents (decision_id,intent_version,instrument_id,action,valid_until,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
               ('dec-today', 1, '513050.SH', 'BUY', '2026-10-12T02:00:00+00:00', 'SAVED',
                json.dumps(intent), intent['created_at']))
    budget(db, '0.03', '0.7')


def insert_history(db, calls, ctxs, intents, plans):
    db.execute('BEGIN')
    for row in calls:
        db.execute('INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)', row)
    for row in ctxs:
        db.execute('INSERT INTO context_snapshots VALUES (?,?,?,?,?,?)', row)
    for row in intents:
        db.execute('INSERT INTO intents (decision_id,intent_version,instrument_id,action,valid_until,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)', row)
    for row in plans:
        db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)', row)
    db.commit()


@pytest.mark.parametrize('history_days', [1, 30, 365])
def test_report_windowed_history_matches_exact_day_baseline(ledger_db, tmp_path, history_days):
    from trademind.storage import Database
    db = ledger_db
    today_content(db)
    insert_history(db, *history_rows(history_days))

    base = Database('sqlite:///' + str(tmp_path / 'baseline.db'))
    base.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)', (ACCOUNT, 1, '500', '0', NOW.isoformat(), '{}'))
    today_content(base)

    full = generate_report(db.path, ACCOUNT, '2026-10-12', NOW)
    baseline = generate_report(base.path, ACCOUNT, '2026-10-12', NOW)
    base.close()
    assert full == baseline  # history depth never changes today's report
    # Today's content really survived the window (not vacuously equal).
    assert baseline['model_cost']['audit_rows'] == 2
    assert baseline['model_cost']['pending_reservation'] == '0.7'
    assert [p['plan_id'] for p in baseline['strategies']] == ['plan-today']
    assert [d['decision_id'] for d in baseline['decisions']] == ['dec-today']
    assert baseline['ending']['positions'][0]['last_price'] == '2.15'
    assert baseline['opening']['cash'] == '500'


def test_windowed_report_isolates_accounts(ledger_db):
    db = ledger_db
    today_content(db)
    db.execute('INSERT INTO account_state VALUES (?,?,?,?,?,?)', ('other', 1, '999', '0', NOW.isoformat(), '{}'))
    other_event = {'account_id': 'other', 'seq': 1, 'kind': 'deposit',
                   'effective_at': '2026-10-11T10:00:00+08:00', 'amount': '999', 'currency': 'CNY'}
    db.execute('INSERT INTO account_events(account_id,seq,kind,instrument_id,amount,effective_at,payload_json) VALUES (?,?,?,?,?,?,?)',
               ('other', 1, 'deposit', None, '999', '2026-10-11T10:00:00+08:00', json.dumps(other_event)))
    p_other = {'plan_id': 'plan-other', 'instrument_id': '513050.SH',
               'available_at': '2026-10-12T09:45:00+08:00', 'allowed_actions': ['BUY']}
    db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',
               ('other', p_other['plan_id'], p_other['available_at'], json.dumps(p_other)))
    ctx_other = {'instrument_id': '513050.SH', 'as_of': '2026-10-12T09:50:00+08:00',
                 'portfolio': {'account_id': 'other'}, 'aux': {'minute_bars': []}}
    db.execute('INSERT INTO context_snapshots VALUES (?,?,?,?,?,?)',
               ('ctx-other', '513050.SH', ctx_other['as_of'], ctx_other['as_of'], 'h', json.dumps(ctx_other)))
    other_intent = {'decision_id': 'dec-other', 'created_at': '2026-10-12T01:50:00+00:00',
                    'context_id': 'ctx-other', 'intent_version': 1, 'instrument_id': '513050.SH',
                    'action': 'SELL', 'reason': 'other account exit'}
    db.execute('INSERT INTO intents (decision_id,intent_version,instrument_id,action,valid_until,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)',
               ('dec-other', 1, '513050.SH', 'SELL', '2026-10-12T03:00:00+00:00', 'SAVED',
                json.dumps(other_intent), other_intent['created_at']))
    r = generate_report(db.path, ACCOUNT, '2026-10-12', NOW)
    assert [p['plan_id'] for p in r['strategies']] == ['plan-today']
    assert all(d['decision_id'] != 'dec-other' for d in r['decisions'])
    assert r['opening']['cash'] == '500' and r['ending']['cash'] == '299'
    assert r['model_cost']['audit_scope'] == 'database_unattributed'
    # Pre-existing rule, unchanged: without a portfolio account marker in the
    # referenced context, intents are not attributed in multi-account bases.
    assert r['decisions'] == []


def test_windowed_queries_hit_indexes_and_bounded_rows(ledger_db):
    import sqlite3
    from datetime import timedelta, timezone as tz
    from trademind.reports.daily import read_snapshot
    db = ledger_db
    today_content(db)
    insert_history(db, *history_rows(400))
    start = datetime.fromisoformat('2026-10-12T00:00:00+08:00')
    stop = datetime.fromisoformat('2026-10-13T00:00:00+08:00')
    snap = read_snapshot(db.path, ACCOUNT, window=(start, stop))
    # Bounded reads: today's rows plus only the margin-day stragglers
    # (±2 days for calls/intents/plans, 7-day lookback for contexts) —
    # never the 400-day history. The ±2-day margin rows are still exactly
    # filtered by the Shanghai-day rules downstream.
    assert len(snap['calls']) == 4
    assert len(snap['contexts']) == 8
    assert len(snap['intents']) == 3
    assert len(snap['plans']) == 3
    # Account-event replay keeps its full history by design (opening books):
    # the deposit and the fill are both present regardless of the window.
    assert len(snap['events']) == 2

    lo = (start - timedelta(days=2)).astimezone(tz.utc).isoformat()
    hi = (stop + timedelta(days=2)).astimezone(tz.utc).isoformat()
    ctx_lo = (start - timedelta(days=7)).astimezone(tz.utc).isoformat()
    ro = sqlite3.connect(str(db.path))
    try:
        checks = [
            ('SELECT payload_json FROM model_calls WHERE created_at>=? AND created_at<=?', (lo, hi),
             'ix_calls_created', 'model_calls', 4),
            ('SELECT payload_json FROM context_snapshots WHERE as_of>=? AND as_of<=?', (ctx_lo, hi),
             'ix_ctx_asof', 'context_snapshots', 8),
            ('SELECT payload_json FROM intents WHERE created_at>=? AND created_at<=?', (lo, hi),
             'ix_intents_created', 'intents', 3),
            ('SELECT payload_json FROM strategy_plan_history WHERE account_id=? AND available_at>=? AND available_at<=?',
             (ACCOUNT, lo, hi), 'ix_plan_hist_acct_avail', 'strategy_plan_history', 3),
        ]
        for sql, args, index, table, expected in checks:
            detail = ' '.join(r[-1] for r in ro.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert index in detail, detail       # window is index-backed…
            assert f'SCAN {table}' not in detail  # …never a full table scan
            assert len(ro.execute(sql, args).fetchall()) == expected
    finally:
        ro.close()


def test_report_empty_day_and_missing_report_tables(tmp_path):
    import sqlite3
    p = tmp_path / 'bare.db'
    raw = sqlite3.connect(str(p))
    raw.execute('CREATE TABLE account_events (account_id TEXT NOT NULL, seq INTEGER NOT NULL, '
                'kind TEXT NOT NULL, instrument_id TEXT, fill_id TEXT, amount TEXT, '
                'effective_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (account_id, seq))')
    raw.commit()
    raw.close()
    r = generate_report(str(p), 'paper-x', '2026-10-12', NOW)
    assert r['model_cost']['audit_rows'] == 0
    assert r['trades'] == [] and r['strategies'] == [] and r['decisions'] == []
    assert any('缺少账户事件历史' in w for w in r['warnings'])
    assert r['opening']['equity'] is None and r['ending']['equity'] is None

