"""Container supervisor: one account writer, market-hours paper, waiting serve."""
import argparse
from datetime import datetime, time as daytime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from ..config import load_config
from ..errors import CalendarCoverageError
from ..market.base import BEIJING
from ..market.calendar import TradingCalendar
from ..observability.notify import notify_webhook
from .paper_run import atomic_json, read_intraday_health


def calendar_covered(calendar, now):
    """True when the calendar's verified coverage includes this date."""
    try:
        return calendar.covers(now.astimezone(BEIJING).date())
    except Exception:  # noqa: BLE001 - test doubles may not implement covers()
        return True


def session_seconds(calendar, now):
    now = now.astimezone(BEIJING)
    if not calendar.is_session_open(now):
        return 0
    end = daytime(11, 30) if now.hour < 12 else daytime(14, 57)
    return max(0, min(3600, int((datetime.combine(now.date(), end, BEIJING)-now).total_seconds())))


def result_policy(report):
    """Unknown worker/account outcome latches a halt rather than retrying."""
    shutdown = report.get('shutdown', {})
    if not report or shutdown.get('worker_active', True) or report.get('error'):
        return True, 0, '运行结果需人工核对，暂停自动交易'
    if report.get('status') == 'blocked':
        if (report.get('preflight') or {}).get('probe_budget_exhausted'):
            # Daily probe pair budget is spent: retrying every 5 minutes cannot
            # succeed before the next Beijing day and burns nothing but noise.
            # Hourly bounded re-check; positions are not at risk (nothing ran).
            return False, 3600, '探针额度今日已耗尽（非模型故障），每小时检查一次，次日额度重置后自动恢复'
        return False, 300, '启动门禁未通过，五分钟后重新检查'
    if report.get('status') == 'failed':
        return False, 300, '运行出现数据或模型错误，五分钟后重新检查'
    if report.get('model_pending'):
        return True, 0, '仍有未消费的模型结果，暂停自动交易'
    return False, 15, '本轮结束，稍后继续监控'


def _pid_alive(pid):
    """Cross-platform liveness: never signal zero on Windows (it can terminate)."""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, no access
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def healthy(path):
    try:
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(state['heartbeat'])).total_seconds()
        if not 0 <= age < 30:
            return False
        return _pid_alive(state['pid'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


class AdoptedObserver:
    """An adopted observer is stopped by an instance-scoped file request.

    Never send an OS signal to a PID recovered from disk: it may be reused.
    Only the observer with the matching startup nonce accepts this request.
    """
    def __init__(self, pid, instance_id, root, alive):
        self.pid, self.instance_id, self.root, self.alive = pid, instance_id, root, alive

    def poll(self):
        return None if self.alive() else 0

    def kill(self):
        atomic_json(self.root/'observer-stop.json', {'instance_id': self.instance_id})

    def wait(self, timeout=30):
        deadline = time.monotonic()+timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('adopted observer', timeout)
            time.sleep(0.1)
        return 0


class Supervisor:
    def __init__(self, config, root, calendar, launch=subprocess.Popen, clock=None, account_id=None, db_path=None, t0_cfg=None):
        self.config, self.root, self.calendar = config, Path(root), calendar
        self.launch = launch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.child = None
        self.kind = None
        self.report_path = None
        self.stopping = False
        self.retry_at = self.clock()
        self.halted = False
        self.note = '等待连续交易时段'
        # Optional unattended alerting: one webhook per transition into a
        # needs-human state, re-armed when the condition clears (26-23).
        self._alerted = {'halted': False, 'uncovered': False}
        self._alert_attempts = {'halted': 0, 'uncovered': 0}
        self.path = self.root/'auto-paper-state.json'
        # Resident read-only observer (TASK-20261026-07): independent child,
        # never a writer. The paper writer slot above stays single-account.
        self.observer = None
        self.observer_retry_at = self.clock()
        self.observer_state_path = self.root/'observer-state.json'
        self.account_id = account_id
        self.db_path = Path(db_path) if db_path else self.root/'trademind.db'
        self.t0_cfg = t0_cfg
        self.observer_config_identity = None
        if Path(self.config).exists():
            try:
                from .observer_identity import observer_identity
                self.observer_config_identity = observer_identity(load_config(self.config, load_credentials=False))
            except Exception:
                pass  # no identity means an existing process cannot be adopted
        if self.account_id is None:
            try:
                cfg = load_config(self.config, load_credentials=False)
                self.account_id = cfg.account_id
                if cfg.storage.url.startswith('sqlite:///'):
                    self.db_path = Path(cfg.storage.url.removeprefix('sqlite:///'))
                self.t0_cfg = cfg.t0
            except Exception:
                pass
        try:
            prev_state = json.loads(self.path.read_text(encoding='utf-8'))
            self.halted = bool(prev_state.get('halted'))
        except (OSError, ValueError):
            prev_state = None
        self._recover_interrupted_paper(prev_state)

    def _recover_interrupted_paper(self, prev):
        """A hard kill skips shutdown(), so classify the interrupted paper run
        at startup: missing/corrupt/unknown outcomes keep the manual-review
        gate instead of silently starting a new paid run (ISSUE-20261022-02)."""
        if not prev or prev.get('terminal') or prev.get('phase') != 'paper' or self.halted:
            return
        pid = prev.get('child_pid')
        if pid and _pid_alive(pid):
            self.halted = True
            self.note = '上一轮paper子进程仍存活，结果未知，需人工核对'
            self.persist_final_state()
            return
        report_path = prev.get('report_path')
        report = {}
        if report_path:
            try:
                report = json.loads(Path(report_path).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                report = {}
        halt, _delay, reason = result_policy(report)
        if not halt:
            return  # the interrupted run had already finished cleanly
        self.halted = True
        self.note = '上一轮模拟进程被外部终止，' + reason
        self.persist_final_state()

    def stop_child(self):
        if self.child is None:
            return
        if self.child.poll() is None:
            if sys.platform == 'win32':
                # SIGTERM means TerminateProcess on Windows: no drain, no report.
                # The bounded paper runner exits on its own deadline; wait for it,
                # and only force-stop when it overruns. A forced paper stop leaves
                # the outcome unknown, so trading stays halted for manual review.
                if self.kind == 'paper':
                    try:
                        self.child.wait(timeout=100)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        try:
                            self.child.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            pass  # exit unconfirmed: keep the unknown-result halt
                        self.halted = True
                        self.note = 'Windows停止超时，模拟进程被强制结束，结果未知，需人工核对'
                        self.persist_halt()
                else:
                    self.child.kill()
                    self.child.wait(timeout=100)
            else:
                self.child.send_signal(signal.SIGINT if self.kind == 'paper' else signal.SIGTERM)
                # Do not start another writer unless this child is conclusively gone.
                self.child.wait(timeout=100)
        self.child = None
        self.kind = None

    def start(self, kind, seconds=0):
        if self.stopping:
            raise RuntimeError('supervisor is stopping; no new writer may start')
        if kind not in ('serve', 'paper'):
            raise ValueError(f'unknown writer kind {kind!r}')
        if self.child is not None:
            raise RuntimeError('writer already active')
        command = [sys.executable, '-m', 'trademind.runner', '--config', self.config]
        if kind == 'serve':
            command += ['serve']
        else:
            from uuid import uuid4
            self.report_path = self.root/'paper-runs'/('auto-'+uuid4().hex+'.json')
            command += ['t0-paper', '--steps', '720', '--max-seconds', str(seconds),
                        '--probe-models', '--report-output', str(self.report_path)]
        # Output contains only existing application evidence; credentials never logged here.
        self.child = self.launch(command)
        self.kind = kind
        self.run_deadline = self.clock()+timedelta(seconds=seconds+75)

    def request_stop(self):
        """run_local.py writes this flag; polling keeps stop console-independent."""
        flag = self.root/'auto-paper.stop'
        if not flag.exists():
            return
        try:
            flag.unlink()
        except OSError:
            pass
        self.stopping = True

    def observer_alive(self):
        return self.observer is not None and self.observer.poll() is None

    def _observer_pid_from_state(self):
        """Liveness of an observer launched by a previous supervisor run."""
        try:
            state = json.loads(self.observer_state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        pid = state.get('pid')
        try:
            age = (self.clock()-datetime.fromisoformat(state['heartbeat'])).total_seconds()
            belongs = (state.get('account_id') == self.account_id
                       and state.get('database') == str(self.root.resolve())
                       and self.observer_config_identity is not None
                       and state.get('config_identity') == self.observer_config_identity
                       and bool(state.get('instance_id')))
            if belongs and state.get('running') and pid and 0 <= age < 30 and _pid_alive(int(pid)):
                return int(pid)
        except (KeyError, TypeError, ValueError):
            pass
        return None

    def ensure_observer(self, now):
        """Keep exactly one resident read-only observer child (ARCH-025).

        An observer crash only affects the web view; paper keeps running and
        halts are never cleared by a respawn. A live observer from a previous
        supervisor run is adopted instead of a port-conflicting second one.
        """
        if self.stopping:
            return
        if self.observer is not None and self.observer.poll() is not None:
            self.observer = None
            self.observer_retry_at = now+timedelta(seconds=60)  # bounded respawn backoff
        if self.observer is None and now >= self.observer_retry_at:
            pid = self._observer_pid_from_state()
            if pid is not None:
                try:
                    state = json.loads(self.observer_state_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    return
                if state.get('pid') != pid:
                    return  # state replaced between validation and adoption
                instance = state['instance_id']
                def alive():
                    try:
                        current = json.loads(self.observer_state_path.read_text(encoding='utf-8'))
                        return (current.get('instance_id') == instance
                                and current.get('running') and _pid_alive(pid))
                    except (OSError, ValueError):
                        return False
                self.observer = AdoptedObserver(pid, instance, self.root, alive)
                return
            command = [sys.executable, '-m', 'trademind.runner', '--config', self.config, 'observer']
            self.observer_retry_at = now+timedelta(seconds=60)
            try:
                self.observer = self.launch(command)
            except OSError:
                self.observer = None  # a web startup failure cannot interrupt paper

    def stop_observer(self):
        if self.observer is None:
            return
        if self.observer.poll() is None:
            # Windows venv python may be a launcher with a real Python child.
            # Ask the service itself to exit before considering its owned tree.
            pid = self._observer_pid_from_state()
            if pid is not None:
                try:
                    state = json.loads(self.observer_state_path.read_text(encoding='utf-8'))
                    atomic_json(self.root/'observer-stop.json', {'instance_id': state['instance_id']})
                    self.observer.wait(timeout=10)
                except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                    pass
            if self.observer.poll() is None:
                if isinstance(self.observer, AdoptedObserver):
                    self.observer.kill()  # instance request, never signal a disk PID
                elif sys.platform == 'win32' and isinstance(self.observer, subprocess.Popen):
                    # Popen still owns a live handle; this is our launcher's tree.
                    try:
                        subprocess.run(['taskkill', '/PID', str(self.observer.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                else:
                    self.observer.kill()
            try:
                self.observer.wait(timeout=30)
            except subprocess.TimeoutExpired:
                return  # retain an unconfirmed handle rather than claiming exit
        self.observer = None

    def tick(self):
        now = self.clock()
        self.request_stop()
        self.ensure_observer(now)
        if self.stopping:
            self.note = '收到停止请求，等待子进程排空后退出'
        if self.child is not None and self.child.poll() is not None:
            previous = self.kind
            self.child = None
            self.kind = None
            if previous == 'paper':
                try:
                    report = json.loads(self.report_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    report = {}
                self.halted, delay, self.note = result_policy(report)
                self.retry_at = now+timedelta(seconds=delay)
            else:
                self.retry_at = now+timedelta(seconds=60)
        covered = calendar_covered(self.calendar, now)
        seconds = session_seconds(self.calendar, now)
        if self.kind == 'paper' and now >= self.run_deadline:
            self.halted = True
            self.note = '模拟进程超过期限，停止并等待人工核对'
            self.stop_child()
        # Calendar coverage gates every paid start: beyond verified data the
        # session state is unknown, so paper never launches and no model
        # request is spent on a possibly-holiday day (ISSUE-20261013-07).
        # Waiting phases no longer launch a serve child: the resident
        # read-only observer owns the web port (TASK-20261026-07/ARCH-025).
        desired = 'paper' if covered and seconds >= 5 and now >= self.retry_at and not self.halted else None
        if self.kind == 'paper' or self.stopping:
            # Let the bounded paper runner drain itself; while stopping no new
            # writer may start at all (ISSUE-20261022-01).
            pass
        elif desired == 'paper' and self.kind != 'paper':
            self.stop_child()
            self.start('paper', seconds)
        elif desired is None and self.kind == 'paper':
            # Session over: the bounded runner is closing out; drain it the
            # same way the old serve-switch did (Windows waits, then force).
            self.stop_child()
        if not covered and not self.halted and self.kind != 'paper':
            self.note = '交易日历覆盖不足，已停止模拟与付费请求，等待人工更新日历'

        intraday = None
        if self.account_id and (self.db_path.exists() or (self.root/'intraday-health.json').exists()):
            try:
                intraday = read_intraday_health(
                    self.db_path,
                    self.account_id,
                    now=now,
                    session_open=bool(seconds),
                    halted=self.halted,
                    t0_cfg=self.t0_cfg,
                )
            except Exception:
                intraday = None

        # Halt and calendar coverage outrank strategy health: with unknown
        # coverage nothing downstream may look like an ordinary closed day.
        if self.halted:
            health_status = '需要人工核对'
        elif not covered:
            health_status = '日历覆盖不足'
        elif intraday is not None:
            health_status = intraday['health_status']
        elif not seconds:
            health_status = '等待开市'
        else:
            health_status = '有效策略观望'

        if self.kind == 'paper':
            if intraday and health_status != '有效策略观望':
                msg = f"盘中模拟监控运行中（{intraday['status_message_zh']}）"
            else:
                msg = '盘中模拟监控运行中'
        elif not covered:
            msg = self.note
        elif self.halted or now < self.retry_at:
            msg = self.note
        else:
            msg = '休市或午休等待，开市自动检查并启动（网页由只读观察服务持续提供）'

        if self.stopping:
            msg = self.note  # draining must not look like ordinary idle waiting

        state = {'account_id':self.account_id, 'pid':os.getpid(), 'heartbeat':now.isoformat(), 'phase':self.kind,
                 'child_pid': self.child.pid if self.child else None,
                 'observer_pid': self.observer.pid if self.observer_alive() else None,
                 'halted':self.halted, 'session_open':bool(seconds),
                 'calendar_covered':covered,
                 'retry_at':self.retry_at.isoformat(), 'report_path':str(self.report_path) if self.report_path else None,
                 'health_status':health_status, 'intraday_health':intraday,
                 'message':msg}
        # Heartbeat first (alert I/O must never delay it — 2026-10-27 review),
        # then the unattended alerting hook below.
        atomic_json(self.path, state)
        self._maybe_alert(covered, health_status, msg, now)
        return state

    _ALERT_MAX_ATTEMPTS = 3

    def _maybe_alert(self, covered, health_status, msg, now):
        """Unattended alerting (Task-20261026-23; hardened 2026-10-27): one
        webhook per transition into a needs-human state, re-armed after
        clearance. A failing webhook retries on later ticks at most
        _ALERT_MAX_ATTEMPTS times per incident, then is dropped quietly —
        trading is never affected. No-op unless TRADEMIND_NOTIFY_WEBHOOK is
        set; never raises."""
        if self.halted and not self._alerted['halted']:
            if self._alert_attempts['halted'] < self._ALERT_MAX_ATTEMPTS:
                self._alert_attempts['halted'] += 1
                if notify_webhook(
                        f'监督器已暂停，需要人工核对：账户 {self.account_id}，'
                        f'状态 {health_status}，原因 {msg}（北京 '
                        f'{now.astimezone(BEIJING).strftime("%m-%d %H:%M")}）'):
                    self._alerted['halted'] = True
            else:
                self._alerted['halted'] = True  # give up quietly, stop retrying
        elif not self.halted:
            self._alerted['halted'] = False
            self._alert_attempts['halted'] = 0
        if not covered and not self._alerted['uncovered']:
            if self._alert_attempts['uncovered'] < self._ALERT_MAX_ATTEMPTS:
                self._alert_attempts['uncovered'] += 1
                if notify_webhook(
                        f'交易日历覆盖不足：账户 {self.account_id} 已停止模拟与付费请求，'
                        '需人工更新日历后才恢复'):
                    self._alerted['uncovered'] = True
            else:
                self._alerted['uncovered'] = True
        elif covered:
            self._alerted['uncovered'] = False
            self._alert_attempts['uncovered'] = 0

    def persist_halt(self):
        """Atomically persist the halt right away: even a later crash must not
        lose the manual-review gate on restart (ISSUE-20261022-01)."""
        self.persist_final_state()

    def persist_final_state(self):
        """Write the terminal state; a restart must inherit any halt."""
        state = {'account_id':getattr(self, 'account_id', None), 'pid': os.getpid(), 'heartbeat': self.clock().isoformat(),
                 'phase': None, 'halted': self.halted, 'session_open': False,
                 'calendar_covered': True, 'retry_at': self.retry_at.isoformat(),
                 'report_path': str(self.report_path) if self.report_path else None,
                 'health_status': '需要人工核对' if self.halted else '已停止',
                 'intraday_health': None,
                 'message': self.note or 'supervisor已停止', 'terminal': True}
        try:
            atomic_json(self.path, state)
        except OSError:
            self.note = (self.note or '') + '；终态持久化失败，重启可能丢失人工核对门禁，需人工核对账本'
            raise
        # Terminal states must alert too (2026-10-27 review: the tick hook
        # never runs once the supervisor exits). covered=True matches the
        # terminal state above; clearance resets are harmless on restart.
        self._maybe_alert(True, state['health_status'], state['message'], self.clock())

    def shutdown(self):
        """Terminal path for run(): drain the paper child, stop the observer,
        classify a paper run's final report, then persist final state. Stop
        errors must not report a clean drain nor clear an unknown outcome."""
        was_paper, report_path = (self.kind == 'paper'), self.report_path
        try:
            self.stop_child()
        except Exception as exc:  # noqa: BLE001 - terminal path must persist state
            self.halted = True
            self.note = f'停止过程异常({type(exc).__name__})，子进程结果未知，需人工核对'
        # The observer is read-only: kill is always safe and never latches a halt.
        self.stop_observer()
        if was_paper and report_path is not None:
            # A paper run that exits on its own during the stop wait still has
            # a final drain report; missing/corrupt/unknown outcomes must keep
            # the manual-review gate across restart (ISSUE-20261022-01).
            try:
                report = json.loads(Path(report_path).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                report = {}
            halt, _delay, reason = result_policy(report)
            if halt:
                self.halted = True
                self.note = reason
            elif not self.halted:
                self.note = reason
        self.persist_final_state()

    def run(self):
        try:
            while not self.stopping:
                self.tick()
                time.sleep(5)
        finally:
            self.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/t0-container.local.toml')
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args(argv)
    cfg = load_config(args.config, load_credentials=False)
    root = Path(cfg.storage.url.removeprefix('sqlite:///')).parent
    if args.health:
        return 0 if healthy(root/'auto-paper-state.json') else 1
    if not cfg.storage.url.startswith('sqlite:///') or cfg.mode != 'paper' or not cfg.t0.enabled or cfg.t0.profile != 'jev_fast_decision':
        raise ValueError('explicit SQLite fast paper required')
    root.mkdir(parents=True, exist_ok=True)
    # One supervisor per runtime root on every platform; second writer refused.
    # The lock file is never deleted (ARCH-017): the kernel releases it on death.
    lock = (root/'auto-paper.lock').open('a')
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        print('another supervisor already holds the writer lock for this runtime root')
        return 2
    try:
        supervisor = Supervisor(args.config, root, TradingCalendar.load())
        def stop(signum, frame):
            supervisor.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if sys.platform == 'win32' and hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, stop)
        supervisor.run()
    finally:
        lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
