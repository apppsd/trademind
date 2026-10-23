"""Local lifecycle (TASK-20261022-01): Windows liveness, stop flag, lock, run_local."""
import datetime
import gc
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import Mock

import pytest

from trademind.execution import auto_paper
from trademind.execution.auto_paper import Supervisor, _pid_alive, healthy


def _fixed_clock(hour=3):
    # UTC 03:00 = 11:00 Beijing: inside the morning session, so `seconds >= 5`
    # holds and the desired phase is 'paper' unless gated otherwise.
    base = datetime.datetime(2026, 10, 22, hour, 0, 0, tzinfo=datetime.timezone.utc)
    return lambda: base


def test_pid_alive_current_process():
    assert _pid_alive(os.getpid())


def _reaped_pid():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait(timeout=30)
    pid = proc.pid
    del proc  # release the process handle so the exited pid is observable gone
    gc.collect()
    return pid


def test_pid_alive_reaped_process():
    assert not _pid_alive(_reaped_pid())


def test_healthy_live_process_fresh_heartbeat(tmp_path):
    path = tmp_path/'state.json'
    path.write_text(json.dumps({'pid': os.getpid(),
                                'heartbeat': datetime.datetime.now(
                                    datetime.timezone.utc).isoformat()}),
                    encoding='utf-8')
    assert healthy(path)


def test_healthy_dead_process(tmp_path):
    path = tmp_path/'state.json'
    path.write_text(json.dumps({'pid': _reaped_pid(),
                                'heartbeat': datetime.datetime.now(
                                    datetime.timezone.utc).isoformat()}),
                    encoding='utf-8')
    assert not healthy(path)


def test_stop_flag_drains_supervisor_without_new_writer(tmp_path, monkeypatch):
    def no_launch(command):
        pytest.fail('no writer may start while draining')
    monkeypatch.setattr(auto_paper.TradingCalendar, 'load', classmethod(lambda cls: Mock()))
    sup = Supervisor('config/x.toml', tmp_path, Mock(), no_launch, _fixed_clock(),
                     account_id='a', db_path=tmp_path/'db.sqlite', t0_cfg=None)
    (tmp_path/'auto-paper.stop').write_text('now', encoding='utf-8')
    state = sup.tick()
    assert sup.stopping and not (tmp_path/'auto-paper.stop').exists()
    assert '停止' in state['message']
    assert state['phase'] is None and sup.child is None


class FakeChild:
    pid = 31337

    def __init__(self, exit_on_kill):
        self.killed = False
        self._exit_on_kill = exit_on_kill
        self.kill_calls = 0
    def poll(self):
        return 0 if self.killed and self._exit_on_kill else None
    def kill(self):
        self.kill_calls += 1
        self.killed = True
    def wait(self, timeout=None):
        if self.killed and self._exit_on_kill:
            return 0
        raise subprocess.TimeoutExpired(cmd='child', timeout=timeout)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows stop path')
def test_stop_child_windows_paper_overrun_forces_and_halts(tmp_path):
    sup = Supervisor.__new__(Supervisor)
    sup.path = tmp_path/'auto-paper-state.json'
    sup.clock = _fixed_clock()
    sup.retry_at = _fixed_clock()()
    sup.report_path = None
    sup.child, sup.kind, sup.halted, sup.note = FakeChild(exit_on_kill=True), 'paper', False, ''
    sup.stop_child()
    assert sup.child is None and sup.kind is None
    assert sup.halted and '需人工核对' in sup.note
    persisted = json.loads(sup.path.read_text(encoding='utf-8'))
    assert persisted['halted'] is True and persisted['terminal'] is True


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows stop path')
def test_stop_child_windows_paper_drains_without_force():
    child = FakeChild(exit_on_kill=True)
    child.killed = True  # exits on its own within the graceful wait
    sup = Supervisor.__new__(Supervisor)
    sup.child, sup.kind, sup.halted, sup.note = child, 'paper', False, ''
    sup.stop_child()
    assert child.kill_calls == 0 and not sup.halted


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows stop path')
def test_stop_child_windows_serve_kills_directly():
    child = FakeChild(exit_on_kill=True)
    sup = Supervisor.__new__(Supervisor)
    sup.child, sup.kind, sup.halted, sup.note = child, 'serve', False, ''
    sup.stop_child()
    assert child.kill_calls == 1 and not sup.halted  # serve holds no orders


def test_windows_supervisor_lock_mutual_exclusion(tmp_path):
    if sys.platform != 'win32':
        pytest.skip('msvcrt region locks are Windows-specific')
    import msvcrt
    first = (tmp_path/'auto-paper.lock').open('a')
    msvcrt.locking(first.fileno(), msvcrt.LK_NBLCK, 1)
    second = (tmp_path/'auto-paper.lock').open('a')
    with pytest.raises(OSError):
        msvcrt.locking(second.fileno(), msvcrt.LK_NBLCK, 1)
    first.close()
    second.close()


def _module(monkeypatch):
    scripts = Path(__file__).parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('run_local', scripts/'run_local.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LOCAL_TOML = '''
mode = "paper"
account_id = "paper-t0-local-1k"
[t0]
enabled = true
profile = "jev_fast_decision"
initial_cash = "1000"
[storage]
url = "sqlite:///{db}"
[api]
host = "127.0.0.1"
port = 8399
'''


def _config(tmp_path, monkeypatch):
    db = tmp_path/'rt'/'trademind.db'
    path = tmp_path/'t0-local.toml'
    path.write_text(LOCAL_TOML.format(db=db.as_posix()), encoding='utf-8')
    monkeypatch.chdir(Path(__file__).parents[1])
    return path, db


def test_local_example_config_1000_isolated():
    from trademind.config import load_config
    cfg = load_config(Path(__file__).parents[1]/'config'/'t0-local.example.toml',
                      load_credentials=False)
    assert cfg.account_id == 'paper-t0-local-1k'
    assert cfg.t0.initial_cash == cfg.paper.initial_cash
    assert str(cfg.t0.initial_cash) == '1000'
    assert cfg.api.host == '127.0.0.1' and cfg.t0.max_single_exposure == pytest.approx(200)


def test_start_refuses_duplicate_supervisor(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    db.parent.mkdir(parents=True)
    (m.state_path(db)).write_text(json.dumps(
        {'pid': os.getpid(), 'heartbeat': datetime.datetime.now(
            datetime.timezone.utc).isoformat()}), encoding='utf-8')
    assert m.cmd_start(str(path)) == 2


def test_start_refuses_busy_port(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(('127.0.0.1', 8399))
    blocker.listen(1)
    try:
        assert m.cmd_start(str(path)) == 2
    finally:
        blocker.close()


def test_start_refuses_without_proxy(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    monkeypatch.delenv('HTTPS_PROXY', raising=False)
    assert m.cmd_start(str(path)) == 2


def test_start_launches_detached_and_records_pid(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7890')
    calls = {}
    proc = Mock(pid=4321, poll=lambda: None)
    def fake_popen(cmd, **kwargs):
        calls['cmd'] = cmd
        calls['creationflags'] = kwargs.get('creationflags')
        return proc
    monkeypatch.setattr(m.subprocess, 'Popen', fake_popen)
    def fake_running(d):
        if calls:
            return 4321, {'heartbeat': datetime.datetime.now(
                datetime.timezone.utc).isoformat(), 'pid': 4321}
        return None, None
    monkeypatch.setattr(m, '_running', fake_running)
    monkeypatch.setattr(m.time, 'sleep', lambda s: None)
    assert m.cmd_start(str(path)) == 0
    assert 'trademind.execution.auto_paper' in ' '.join(calls['cmd'])
    assert 't0-local.toml' in ' '.join(calls['cmd'])
    if sys.platform == 'win32':
        assert calls['creationflags'] & 0x00000008  # detached
        assert calls['creationflags'] & 0x01000000  # breakaway from launching job


def test_stop_writes_flag_and_confirms_drain(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    db.parent.mkdir(parents=True)
    (m.state_path(db)).write_text(json.dumps(
        {'pid': 4711, 'heartbeat': datetime.datetime.now(
            datetime.timezone.utc).isoformat()}), encoding='utf-8')
    alive = iter([True, True, False])
    monkeypatch.setattr(m, '_pid_alive', lambda pid: next(alive))
    monkeypatch.setattr(m.time, 'sleep', lambda s: None)
    assert m.cmd_stop(str(path)) == 0
    assert m.stop_flag(db).exists()


def test_stop_escalation_verifies_image_then_marks_halted(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    db.parent.mkdir(parents=True)
    (m.state_path(db)).write_text(json.dumps(
        {'pid': 4711, 'heartbeat': datetime.datetime.now(
            datetime.timezone.utc).isoformat(), 'halted': False}), encoding='utf-8')
    alive = iter([True, False])  # initial _running, then post-kill check
    monkeypatch.setattr(m, '_pid_alive', lambda pid: next(alive))
    monkeypatch.setattr(m, '_image_name', lambda pid: 'python.exe')
    kills = []
    monkeypatch.setattr(m.subprocess, 'run',
                        lambda cmd, **kw: kills.append(cmd) or Mock(returncode=0))
    monkeypatch.setattr(m.time, 'sleep', lambda s: None)
    monkeypatch.setattr(m, 'GRACE_SECONDS', 0)
    assert m.cmd_stop(str(path)) == 1
    assert kills and kills[0][0] == 'taskkill' and '/PID' in kills[0]
    assert json.loads(m.state_path(db).read_text(encoding='utf-8'))['halted'] is True


def test_stop_refuses_to_kill_foreign_image(tmp_path, monkeypatch):
    m = _module(monkeypatch)
    path, db = _config(tmp_path, monkeypatch)
    db.parent.mkdir(parents=True)
    (m.state_path(db)).write_text(json.dumps(
        {'pid': 4711, 'heartbeat': datetime.datetime.now(
            datetime.timezone.utc).isoformat()}), encoding='utf-8')
    monkeypatch.setattr(m, '_pid_alive', lambda pid: True)
    monkeypatch.setattr(m, '_image_name', lambda pid: 'notepad.exe')
    kills = []
    monkeypatch.setattr(m.subprocess, 'run',
                        lambda cmd, **kw: kills.append(cmd) or Mock(returncode=0))
    monkeypatch.setattr(m.time, 'sleep', lambda s: None)
    monkeypatch.setattr(m, 'GRACE_SECONDS', 0)
    assert m.cmd_stop(str(path)) == 1
    assert not kills


class RecordingLaunch:
    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(list(command))
        return FakeChild(exit_on_kill=True)


def _supervisor(tmp_path, launch, hour=3):
    # hour=3 UTC = 11:00 Beijing: inside the morning session.
    return Supervisor('config/x.toml', tmp_path, Mock(), launch, _fixed_clock(hour),
                      account_id='a', db_path=tmp_path/'db.sqlite', t0_cfg=None)


def test_stop_with_active_serve_starts_no_writer(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.start('serve')
    assert len(launch.commands) == 1
    (tmp_path/'auto-paper.stop').write_text('now', encoding='utf-8')
    sup.tick()
    assert sup.stopping
    assert len(launch.commands) == 1  # BUG-1 regression: zero new launches


def test_stop_with_active_paper_starts_no_writer(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.start('paper', seconds=600)
    assert len(launch.commands) == 1
    (tmp_path/'auto-paper.stop').write_text('now', encoding='utf-8')
    sup.tick()
    assert len(launch.commands) == 1


def test_stop_with_exited_child_starts_no_writer(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.start('serve')
    sup.child.killed = True  # exited just before the stop flag landed
    (tmp_path/'auto-paper.stop').write_text('now', encoding='utf-8')
    sup.tick()
    assert sup.child is None
    assert len(launch.commands) == 1


def test_start_rejects_none_unknown_and_stopping(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    with pytest.raises(ValueError):
        sup.start(None)
    with pytest.raises(ValueError):
        sup.start('bogus')
    sup.stopping = True
    with pytest.raises(RuntimeError):
        sup.start('paper', seconds=10)
    assert launch.commands == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows stop path')
def test_forced_paper_stop_halt_survives_rebuild(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.child, sup.kind = FakeChild(exit_on_kill=True), 'paper'
    sup.stop_child()
    state = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert state['halted'] is True and '人工核对' in state['message']
    launch2 = RecordingLaunch()
    sup2 = _supervisor(tmp_path, launch2)
    assert sup2.halted is True  # BUG-2 regression: rebuilt instance stays halted
    st = sup2.tick()            # session open: read-only serve allowed, paper never
    assert all('t0-paper' not in ' '.join(c) for c in launch2.commands)
    assert st['health_status'] == '需要人工核对'


def test_shutdown_persists_final_state_and_preserves_existing_halt(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.shutdown()
    state = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert state['halted'] is False and state['terminal'] is True
    sup2 = _supervisor(tmp_path, launch)
    assert sup2.halted is False
    sup2.halted = True  # e.g. an unknown worker outcome halted an earlier run
    sup2.note = '运行结果需人工核对，暂停自动交易'
    sup2.shutdown()
    state2 = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert state2['halted'] is True and '人工核对' in state2['message']


class ExplodingChild:
    def poll(self):
        return None

    def kill(self):
        raise OSError('kill failed')

    def wait(self, timeout=None):
        raise OSError('wait failed')


def test_stop_child_error_marks_unknown_and_persists(tmp_path):
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    sup.child, sup.kind = ExplodingChild(), 'serve'
    sup.shutdown()  # must not raise, must not report a clean drain
    state = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert state['halted'] is True and '停止过程异常' in state['message']


class GracefulPaper:
    """Exits on its own within the graceful wait; signalable on both platforms."""

    def poll(self):
        return None

    def send_signal(self, sig):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_shutdown_classifies_final_paper_report(tmp_path):
    cases = [
        ('missing', None, True),
        ('unknown', {'status': 'incomplete', 'model_pending': True,
                     'shutdown': {'worker_active': True, 'unconsumed_result': True}}, True),
        ('error', {'status': 'failed', 'error': 'ProviderError',
                   'shutdown': {'worker_active': False}}, True),
        ('clean', {'status': 'completed', 'model_pending': False,
                   'shutdown': {'worker_active': False, 'unconsumed_result': False}}, False),
    ]
    for name, report, expected_halt in cases:
        root = tmp_path/name
        root.mkdir()
        sup = _supervisor(root, RecordingLaunch())
        sup.child, sup.kind = GracefulPaper(), 'paper'
        sup.report_path = root/'paper.json'
        if report is not None:
            sup.report_path.write_text(json.dumps(report), encoding='utf-8')
        sup.shutdown()
        state = json.loads((root/'auto-paper-state.json').read_text(encoding='utf-8'))
        assert state['halted'] is expected_halt, name
        if expected_halt:
            assert '人工核对' in state['message'], name


def test_shutdown_report_check_preserves_existing_halt(tmp_path):
    sup = _supervisor(tmp_path, RecordingLaunch())
    sup.child, sup.kind = GracefulPaper(), 'paper'
    sup.report_path = tmp_path/'paper.json'
    sup.report_path.write_text(json.dumps(
        {'status': 'completed', 'model_pending': False,
         'shutdown': {'worker_active': False, 'unconsumed_result': False}}), encoding='utf-8')
    sup.halted = True  # an earlier unknown/risk halt must not be cleared
    sup.note = '运行结果需人工核对，暂停自动交易'
    sup.shutdown()
    state = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert state['halted'] is True and '人工核对' in state['message']


CLEAN_REPORT = {'status': 'completed', 'model_pending': False,
                'shutdown': {'worker_active': False, 'unconsumed_result': False}}


def _write_prev_state(root, *, phase='paper', halted=False, terminal=None,
                      report=None, child_pid=None):
    report_path = root/'paper.json'
    if report is not None:
        report_path.write_text(json.dumps(report), encoding='utf-8')
    now = _fixed_clock()().isoformat()
    (root/'auto-paper-state.json').write_text(json.dumps({
        'pid': 999, 'heartbeat': now, 'phase': phase, 'child_pid': child_pid,
        'halted': halted, 'session_open': True, 'terminal': terminal,
        'retry_at': now,
        'report_path': str(report_path) if report is not None else None,
        'message': 'x'}), encoding='utf-8')


def test_restart_after_hard_kill_with_missing_report_halts(tmp_path):
    _write_prev_state(tmp_path, phase='paper', report=None)
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    assert sup.halted is True and '外部终止' in sup.note  # ISSUE-20261022-02
    persisted = json.loads((tmp_path/'auto-paper-state.json').read_text(encoding='utf-8'))
    assert persisted['halted'] is True
    st = sup.tick()  # session open: no paid paper may start
    assert all('t0-paper' not in ' '.join(c) for c in launch.commands)
    assert st['health_status'] == '需要人工核对'


def test_restart_after_hard_kill_with_clean_report_resumes(tmp_path):
    _write_prev_state(tmp_path, phase='paper', report=CLEAN_REPORT)
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    assert sup.halted is False  # the interrupted run had finished cleanly
    sup.tick()
    assert any('t0-paper' in ' '.join(c) for c in launch.commands)  # lawful resume


def test_restart_after_clean_stop_does_not_halt(tmp_path):
    _write_prev_state(tmp_path, phase=None, terminal=True)
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    assert sup.halted is False
    sup.tick()
    assert any('t0-paper' in ' '.join(c) for c in launch.commands)


def test_restart_after_serve_kill_does_not_halt(tmp_path):
    _write_prev_state(tmp_path, phase='serve')
    sup = _supervisor(tmp_path, RecordingLaunch())
    assert sup.halted is False  # serve holds no orders


def test_restart_with_live_child_pid_halts(tmp_path):
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        _write_prev_state(tmp_path, phase='paper', child_pid=proc.pid, report=CLEAN_REPORT)
        sup = _supervisor(tmp_path, RecordingLaunch())
        assert sup.halted is True and '仍存活' in sup.note
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_restart_preserves_existing_halt(tmp_path):
    _write_prev_state(tmp_path, phase='paper', halted=True, report=CLEAN_REPORT)
    launch = RecordingLaunch()
    sup = _supervisor(tmp_path, launch)
    assert sup.halted is True
    sup.tick()
    assert all('t0-paper' not in ' '.join(c) for c in launch.commands)
