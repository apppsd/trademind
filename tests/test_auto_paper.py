from datetime import date, datetime, timedelta
import json
from unittest.mock import Mock

import pytest

from trademind.execution.auto_paper import Supervisor, session_seconds, result_policy, healthy
from trademind.market.calendar import TradingCalendar

CAL = TradingCalendar([date(2026, 10, 12), date(2026, 10, 13)])


@pytest.mark.parametrize('stamp,seconds', [('09:20',0),('09:30',3600),('11:29',60),('11:30',0),
                                          ('12:59',0),('13:00',3600),('14:56',60),('14:57',0)])
def test_market_windows(stamp,seconds):
    assert session_seconds(CAL,datetime.fromisoformat('2026-10-13T'+stamp+':00+08:00'))==seconds
    assert session_seconds(CAL,datetime.fromisoformat('2026-10-14T'+stamp+':00+08:00'))==0


def setup_supervisor(tmp_path, stamp='11:40'):
    now=[datetime.fromisoformat('2026-10-13T'+stamp+':00+08:00')]
    children=[]
    def launch(command):
        child=Mock()
        child.poll.return_value=None
        children.append((command,child))
        return child
    return Supervisor('config/test.toml',tmp_path,CAL,launch,lambda:now[0]),now,children


def test_lunch_idle_launches_observer_then_paper_at_open(tmp_path):
    # TASK-20261026-07: waiting phases no longer run a serve writer; a
    # resident read-only observer child is launched instead (ARCH-025).
    s,now,children=setup_supervisor(tmp_path)
    state=s.tick()
    assert state['phase'] is None and s.child is None
    assert len(children)==1 and 'observer' in children[0][0]
    assert '--probe-models' not in children[0][0]  # observer: zero paid path
    now[0]=now[0].replace(hour=13,minute=0)
    assert s.tick()['phase']=='paper'
    assert '--probe-models' in children[1][0]
    assert children[1][0][children[1][0].index('--max-seconds')+1]=='3600'
    assert json.loads(s.path.read_text(encoding='utf-8'))['message']=='盘中模拟监控运行中'


def test_observer_respawns_after_crash_and_paper_stays_untouched(tmp_path):
    s,now,children=setup_supervisor(tmp_path,'13:00')
    s.tick()
    assert s.tick()['phase']=='paper'
    paper_child=children[1][1]
    children[0][1].poll.return_value=1  # observer crashed
    s.tick()  # crash is only registered this tick, respawn waits out the backoff
    assert s.observer is None and len(children)==2 and s.halted is False
    now[0]+=timedelta(seconds=61)
    s.tick()
    assert len(children)==3 and 'observer' in children[2][0]
    assert children[1][1] is paper_child  # paper child object untouched
    assert s.halted is False


def test_shutdown_stops_observer_and_persists_terminal_state(tmp_path):
    s,now,children=setup_supervisor(tmp_path,'13:00')
    s.tick()  # observer + paper
    s.observer, s.child, s.kind = children[0][1], children[1][1], 'paper'
    s.shutdown()
    children[0][1].kill.assert_called_once()  # read-only kill never latches a halt
    assert json.loads(s.path.read_text(encoding='utf-8'))['terminal'] is True


def test_blocked_run_backs_off_then_retries(tmp_path):
    s,now,children=setup_supervisor(tmp_path,'13:00')
    s.tick()
    s.report_path.parent.mkdir(exist_ok=True)
    s.report_path.write_text(json.dumps({'status':'blocked','shutdown':{'worker_active':False}}))
    children[1][1].poll.return_value=2
    assert s.tick()['phase'] is None
    now[0]+=timedelta(seconds=301)
    assert s.tick()['phase']=='paper'


def test_missing_report_latches_halt_across_restart(tmp_path):
    s,now,children=setup_supervisor(tmp_path,'13:00')
    s.tick()
    children[1][1].poll.return_value=1
    assert s.tick()['halted'] is True
    restarted,_,_=setup_supervisor(tmp_path,'13:20')
    assert restarted.tick()['phase'] is None and restarted.halted


def test_unconfirmed_child_stop_never_launches_another_writer(tmp_path):
    import subprocess
    s,now,children=setup_supervisor(tmp_path,'13:00')
    s.tick()
    assert s.tick()['phase']=='paper'
    children[1][1].wait.side_effect=subprocess.TimeoutExpired('test',100)
    s.run_deadline=now[0]-timedelta(seconds=1)
    s.tick()  # deadline force-stop: exit unconfirmed, halt latches, no new writer
    assert s.halted is True
    paper_cmds=[c for c,_ in children if '--probe-models' in c]
    assert len(children)==2 and len(paper_cmds)==1  # observer only, never a second writer


def test_calendar_coverage_gate_blocks_paper_and_paid_requests(tmp_path):
    # 2026-10-14 would sit in an afternoon session, but the calendar's
    # verified coverage ends 2026-10-13: unknown can never start paper or spend.
    s,now,children=setup_supervisor(tmp_path,'11:40')
    assert s.tick()['phase'] is None  # lunch break inside coverage
    now[0]=now[0].replace(day=14,hour=13,minute=0)
    state=s.tick()
    assert state['phase'] is None
    assert state['calendar_covered'] is False
    assert state['health_status']=='日历覆盖不足'
    assert '日历覆盖不足' in state['message'] and '付费' in state['message']
    assert all('--probe-models' not in cmd for cmd,_ in children)  # observer only, zero paid path
    now[0]=now[0].replace(day=13)  # back inside coverage: paper can start
    state=s.tick()
    assert state['phase']=='paper'
    assert state['calendar_covered'] is True


def test_worker_pending_halts_and_heartbeat_stale_is_unhealthy(tmp_path):
    assert result_policy({'shutdown':{'worker_active':True}})[0]
    assert result_policy({'shutdown':{'worker_active':False},'model_pending':True})[0]
    s,_,_=setup_supervisor(tmp_path)
    s.tick()
    # Historical fixture heartbeat cannot falsely claim today's live process is healthy.
    state=json.loads(s.path.read_text(encoding='utf-8'))
    state['heartbeat']='2020-01-01T00:00:00+00:00'
    s.path.write_text(json.dumps(state))
    assert not healthy(s.path)


def test_windows_health_never_sends_unix_signal_zero(tmp_path,monkeypatch):
    """Windows liveness uses a ctypes process query; os.kill(pid,0) can terminate."""
    import os, sys
    from datetime import timezone
    from trademind.execution import auto_paper
    if sys.platform != 'win32':
        pytest.skip('Windows-specific liveness path')
    path=tmp_path/'state.json'
    path.write_text(json.dumps({'pid':os.getpid(),'heartbeat':datetime.now(timezone.utc).isoformat()}))
    monkeypatch.setattr(auto_paper.os,'kill',lambda *a:pytest.fail('Windows signal zero can terminate process'))
    assert healthy(path)


def test_strategy_failure_backoff_recovery_and_account_day_isolation(tmp_path):
    from test_fast_decision import make_pipe, strategy, I
    from trademind.domain.enums import ProviderKind
    from trademind.errors import ProviderError
    from trademind.execution.paper_run import read_intraday_health
    from trademind.models.base import ModelOutput, build_call

    pipe, tick = make_pipe(tmp_path)
    pipe.cfg.universe = [I, '518880.SH']
    pipe.cfg.t0.whitelist = [I, '518880.SH']
    pipe.ledger.deposit(__import__('decimal').Decimal('1000'), tick[0] - timedelta(minutes=10))

    # Simulate consecutive strategy failures: 1st failure -> 300s backoff, 2nd failure -> 600s backoff
    def fail_empty(**kw):
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK, model='deepseek-flash',
                          system=kw['system'], user=kw['user'], trace_id=kw['trace_id'])
        call.status = 'error'
        call.failure_category = 'length_truncated'
        call.error = 'completion truncated at max_tokens (finish_reason=length)'
        pipe.llm.last_call = call
        pipe.repo.save_model_call(call)
        pipe.model_budget.reserve_request(100, 1024, role='strategy')
        raise ProviderError(call.error)

    pipe.llm.complete = Mock(side_effect=fail_empty)
    r1 = pipe.run_decision(I, tick[0])
    assert r1.intent.action.value == 'WAIT'
    h1 = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h1['health_status'] == '策略请求失败退避'
    assert h1['consecutive_failures'] == 1
    assert h1['backoff_seconds'] == 300
    assert h1['last_failure_category'] == 'length_truncated'
    assert '输出达到词元上限被截断' in h1['last_failure_reason_zh']
    assert h1['requested_count_today'] == 1

    # Rotating to second instrument within 300s is blocked by account-level model failure backoff (no extra LLM call)
    tick[0] += timedelta(seconds=60)
    r_backoff = pipe.run_decision('518880.SH', tick[0])
    assert r_backoff.intent.action.value == 'WAIT'
    assert r_backoff.intent.reason == 'strategy model failure backoff'
    assert pipe.llm.complete.call_count == 1

    # Supervisor tick surfaces '策略请求失败退避' in Chinese while child is running
    sup = Supervisor('config/test.toml', tmp_path, CAL, lambda cmd: Mock(poll=lambda: None),
                     lambda: tick[0], account_id=pipe.cfg.account_id, db_path=pipe.db.path, t0_cfg=pipe.cfg.t0)
    st = sup.tick()
    assert st['phase'] == 'paper'
    assert st['health_status'] == '策略请求失败退避'
    assert '策略请求失败退避' in st['message']

    # At t + 301s, backoff expires and 2nd failure doubles backoff to 600s
    tick[0] += timedelta(seconds=241)
    pipe.run_decision(I, tick[0])
    assert pipe.llm.complete.call_count == 2
    h2 = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h2['consecutive_failures'] == 2
    assert h2['backoff_seconds'] == 600

    # At t + 301s + 601s, valid WAIT strategy succeeds and resets failure backoff
    tick[0] += timedelta(seconds=601)
    def ok_wait(**kw):
        pipe.model_budget.reserve_request(100, 1024, role=kw['role'])
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK, model='deepseek-flash',
                          system=kw['system'], user=kw['user'], trace_id=kw['trace_id'])
        call.failure_category = 'none'
        pipe.llm.last_call = call
        pipe.repo.save_model_call(call)
        plan_dict = strategy()
        plan_dict['allowed_actions'] = ['WAIT']
        return ModelOutput(text='', parsed=plan_dict, call=call)

    pipe.llm.complete = Mock(side_effect=ok_wait)
    r_ok = pipe.run_decision(I, tick[0])
    assert r_ok.intent.action.value == 'WAIT'
    h3 = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h3['health_status'] == '有效策略观望'
    assert h3['consecutive_failures'] == 0
    assert h3['backoff_seconds'] == 0
    assert h3['last_strategy_success_at'] is not None

    # Account & date isolation: another account or next day never inherits failure state
    pipe.llm.complete = Mock(side_effect=fail_empty)
    tick[0] += timedelta(seconds=601)
    pipe.run_decision('518880.SH', tick[0])
    assert read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True)['consecutive_failures'] == 1
    assert read_intraday_health(pipe.db.path, 'other-account', now=tick[0], session_open=True)['consecutive_failures'] == 0
    next_day = tick[0] + timedelta(days=1)
    assert read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=next_day, session_open=True)['consecutive_failures'] == 0


def test_wait_cooldown_unaffordable_not_counted_as_failure_and_risk_exit_works_during_backoff(tmp_path):
    from decimal import Decimal as D
    from test_fast_decision import make_pipe, minute, I
    from test_t0_runtime_repairs import buy_position
    from trademind.domain.enums import ProviderKind
    from trademind.errors import ProviderError
    from trademind.execution.paper_run import read_intraday_health
    from trademind.models.base import build_call

    pipe, tick = make_pipe(tmp_path)
    # 1. Unaffordable minimum lot (price=10.0 -> 1000 CNY > 500 cash) does not count as model failure
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([minute(tick[0] - timedelta(minutes=i), '10.0') for i in reversed(range(20))], None))
    res_unaff = pipe.run_decision(I, tick[0])
    assert res_unaff.intent.reason == 'minimum lawful lot unaffordable'
    h_unaff = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_unaff['consecutive_failures'] == 0
    assert h_unaff['health_status'] == '有效策略观望'
    pipe.llm.complete.assert_not_called()

    # 2. Restore affordable price, trigger model failure backoff, then verify open position stop-loss still executes
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([minute(tick[0] - timedelta(minutes=i), '1.50') for i in reversed(range(20))], None))
    def fail_timeout(**kw):
        call = build_call(role=kw['role'], provider=ProviderKind.DEEPSEEK, model='deepseek-flash',
                          system=kw['system'], user=kw['user'], trace_id=kw['trace_id'])
        call.status = 'timeout'
        call.failure_category = 'timeout'
        pipe.llm.last_call = call
        pipe.repo.save_model_call(call)
        raise ProviderError('model request timeout')

    pipe.llm.complete = Mock(side_effect=fail_timeout)
    tick[0] += timedelta(minutes=1)
    pipe.run_decision(I, tick[0])
    assert pipe._fast_pipeline.planner.in_failure_backoff(tick[0]) is True

    # Open position with stop-loss breach (avg_cost=1.50, current=1.47) during strategy failure backoff
    buy_position(pipe, at=tick[0] - timedelta(minutes=2))
    h_pos = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_pos['health_status'] == '持仓风险处理'

    tick[0] += timedelta(seconds=30)
    pipe.router.get_minute_bars = Mock(side_effect=lambda *a, **kw: ([minute(tick[0] - timedelta(minutes=i), '1.47') for i in reversed(range(20))], None))
    exit_res = pipe.run_decision(I, tick[0])
    assert exit_res.portfolio_intent is not None
    assert exit_res.portfolio_intent.action.value == 'SELL'
    assert exit_res.order_id is not None
    # Stop-loss exit did not invoke any additional LLM or JEV calls
    assert pipe.llm.complete.call_count == 1
    pipe.jev.session.post.assert_not_called()
    tick[0] += timedelta(minutes=1)
    assert pipe.poll_fills(tick[0])
    assert not pipe.ledger.snapshot(tick[0]).positions


def test_intraday_health_seven_chinese_statuses_and_legacy_unknown_category(tmp_path):
    from test_fast_decision import make_pipe, I
    from trademind.execution.paper_run import read_intraday_health
    from trademind.reports.locale import INTRADAY_HEALTH_STATUS

    pipe, tick = make_pipe(tmp_path)
    # 1. 等待开市
    h_closed = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=False, t0_cfg=pipe.cfg.t0)
    assert h_closed['health_status'] == '等待开市'

    # 2. 有效策略观望
    h_wait = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_wait['health_status'] == '有效策略观望'

    # 3. 行情不足
    (pipe.db.path.parent / 'intraday-health.json').write_text(json.dumps({
        'account_id': pipe.cfg.account_id, 'date': tick[0].date().isoformat(),
        'data_insufficient': True, 'last_guard_verdict': 'WAIT_DATA_QUALITY',
    }), encoding='utf-8')
    h_data = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_data['health_status'] == '行情不足'

    # 4. 策略请求失败退避 + 旧记录缺少 failure_category 时不猜测（标记为 unknown）
    old_call = {
        'call_id': 'mc-legacy', 'trace_id': 'tr-legacy', 'role': 'strategy',
        'provider': 'deepseek', 'model': 'deepseek-flash', 'status': 'refused',
        'created_at': tick[0].isoformat(), 'error': 'empty completion (refusal?)',
    }
    pipe.db.execute(
        'INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)',
        ('mc-legacy', 'tr-legacy', 'strategy', 'deepseek', 'deepseek-flash', 'refused', tick[0].isoformat(), json.dumps(old_call)),
    )
    h_legacy = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_legacy['health_status'] == '策略请求失败退避'
    assert h_legacy['last_failure_category'] == 'unknown'
    assert h_legacy['last_failure_reason_zh'] == '历史记录缺少失败分类'
    # Audit rows are never used to impersonate requested_count_today when budget row is absent
    assert h_legacy['audit_rows_today'] == 1
    assert h_legacy['requested_count_today'] is None

    # 5. 预算限制
    pipe.cfg.t0.daily_decision_call_budget = 2
    pipe.repo.save_coalesce('model-budget:' + pipe.cfg.account_id, tick[0], {
        'day': tick[0].date().isoformat(), 'used': 2, 'strategy': 2,
        'cost_settled_cny': '0.05', 'cost_pending_cny': '0', 'cost_reserved_cny': '0.05',
    })
    h_budget = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h_budget['health_status'] == '预算限制'
    assert h_budget['requested_count_today'] == 2

    # 6. 需要人工核对
    h_halt = read_intraday_health(pipe.db.path, pipe.cfg.account_id, now=tick[0], session_open=True, halted=True, t0_cfg=pipe.cfg.t0)
    assert h_halt['health_status'] == '需要人工核对'
    assert set(INTRADAY_HEALTH_STATUS) == {'等待开市', '行情不足', '有效策略观望', '策略请求失败退避', '预算限制', '持仓风险处理', '需要人工核对'}


def test_intraday_health_reads_bounded_window_not_full_history(tmp_path):
    """TASK-20261013-16: health reads only the Shanghai-day window; stale
    failures and plans (even inside the ±2-day SQL margin) never leak into
    today's trailing failure count, audit rows or last-success timestamp."""
    from datetime import timezone
    from test_fast_decision import make_pipe
    from trademind.execution.paper_run import read_intraday_health

    pipe, tick = make_pipe(tmp_path)
    acct = pipe.cfg.account_id

    def stale_call(ident, at):
        c = {'call_id': ident, 'role': 'strategy', 'provider': 'deepseek', 'model': 'deepseek-flash',
             'status': 'error', 'created_at': at.isoformat(), 'failure_category': 'timeout',
             'error': 'stale failure ' + ident}
        pipe.db.execute(
            'INSERT INTO model_calls(call_id,trace_id,role,provider,model,status,created_at,payload_json) VALUES (?,?,?,?,?,?,?,?)',
            (ident, 'tr-' + ident, 'strategy', 'deepseek', 'deepseek-flash', 'error', at.isoformat(), json.dumps(c)))

    # Yesterday's failure sits inside the ±2-day SQL margin and must still be
    # excluded by the exact Shanghai-day filter, exactly as the old full scan
    # did; the 2020 row is outside any window.
    stale_call('mc-yesterday', tick[0] - timedelta(days=1))
    stale_call('mc-ancient', datetime(2020, 1, 1, tzinfo=timezone.utc))
    old_avail = (tick[0] - timedelta(days=1)).isoformat()
    pipe.db.execute('INSERT INTO strategy_plan_history VALUES (?,?,?,?)',
                    (acct, 'plan-stale', old_avail,
                     json.dumps({'plan_id': 'plan-stale', 'available_at': old_avail})))
    h = read_intraday_health(pipe.db.path, acct, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h['consecutive_failures'] == 0
    assert h['audit_rows_today'] == 0
    assert h['last_failure_category'] is None
    assert h['last_strategy_success_at'] is None
    # Today's failure still counts: the window never drops current-day rows.
    stale_call('mc-today', tick[0])
    h2 = read_intraday_health(pipe.db.path, acct, now=tick[0], session_open=True, t0_cfg=pipe.cfg.t0)
    assert h2['consecutive_failures'] == 1
    assert h2['audit_rows_today'] == 1
    assert h2['last_failure_category'] == 'timeout'

