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


def healthy(path):
    try:
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(state['heartbeat'])).total_seconds()
        if not 0 <= age < 30:
            return False
        if sys.platform != 'linux':
            # Signal zero is process termination on Windows, not a Unix liveness probe.
            return False
        os.kill(state['pid'], 0)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
        self.path = self.root/'auto-paper-state.json'
        self.account_id = account_id
        self.db_path = Path(db_path) if db_path else self.root/'trademind.db'
        self.t0_cfg = t0_cfg
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
            self.halted = bool(json.loads(self.path.read_text(encoding='utf-8')).get('halted'))
        except (OSError, ValueError):
            pass

    def stop_child(self):
        if self.child is None:
            return
        if self.child.poll() is None:
            self.child.send_signal(signal.SIGINT if self.kind == 'paper' else signal.SIGTERM)
            # Do not start another writer unless this child is conclusively gone.
            self.child.wait(timeout=100)
        self.child = None
        self.kind = None

    def start(self, kind, seconds=0):
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

    def tick(self):
        now = self.clock()
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
        desired = 'paper' if covered and seconds >= 5 and now >= self.retry_at and not self.halted else 'serve'
        if self.kind == 'paper':
            # Let the bounded runner drain itself, even when the session just ended.
            pass
        elif self.kind != desired:
            self.stop_child()
            self.start(desired, seconds)
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
            msg = '休市或午休等待，开市自动检查并启动'

        state = {'pid':os.getpid(), 'heartbeat':now.isoformat(), 'phase':self.kind,
                 'halted':self.halted, 'session_open':bool(seconds),
                 'calendar_covered':covered,
                 'retry_at':self.retry_at.isoformat(), 'report_path':str(self.report_path) if self.report_path else None,
                 'health_status':health_status, 'intraday_health':intraday,
                 'message':msg}
        atomic_json(self.path, state)
        return state

    def run(self):
        try:
            while not self.stopping:
                self.tick()
                time.sleep(5)
        finally:
            self.stop_child()


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
    # Linux container-only lock, never expose Docker socket or launch a second supervisor.
    import fcntl
    with (root/'auto-paper.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        supervisor = Supervisor(args.config, root, TradingCalendar.load())
        def stop(signum, frame):
            supervisor.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        supervisor.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
