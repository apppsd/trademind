"""Local Windows lifecycle for the auto paper supervisor: start/status/stop.

No Docker/Rancher/rdctl: launches the in-repo supervisor with the project venv
interpreter, detached, logging to the runtime root. Stop goes through a flag
file the supervisor polls, so draining works without console signals; only a
timed-out supervisor is terminated, and only after its image name is verified.
Credentials are never read, printed or logged. An existing host HTTP proxy is
required and forwarded (TASK-20261022-01): the external JEV endpoint refuses
direct connections (451), while DeepSeek and market sources stay domestic.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
GRACE_SECONDS = 150  # supervisor drains: 100s child wait + tick interval slack


def _load_runtime(config):
    from trademind.config import load_config
    cfg = load_config(config, load_credentials=False)
    if not cfg.storage.url.startswith('sqlite:///'):
        raise SystemExit(f'explicit sqlite storage required, got {cfg.storage.url!r}')
    db = Path(cfg.storage.url.removeprefix('sqlite:///'))
    if not db.is_absolute():
        db = (REPO / db).resolve()
    return cfg, db


def state_path(db):
    return db.parent/'auto-paper-state.json'


def log_path(db):
    return db.parent/'auto-paper.log'


def stop_flag(db):
    return db.parent/'auto-paper.stop'


def _pid_alive(pid):
    from trademind.execution.auto_paper import _pid_alive
    return _pid_alive(pid)


def _image_name(pid):
    """Executable basename of pid, or None when it is gone or unreadable."""
    if sys.platform != 'win32':
        return None
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(ctypes.sizeof(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return None
    finally:
        kernel32.CloseHandle(handle)


def _heartbeat_age(state, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = datetime.datetime.fromisoformat(state['heartbeat'])
    return (now-stamp).total_seconds()


def _running(db):
    """(live_pid, state) when a supervisor holds a fresh heartbeat."""
    try:
        state = json.loads(state_path(db).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, None
    pid = state.get('pid')
    try:
        fresh = 0 <= _heartbeat_age(state) < 30
    except (KeyError, ValueError, TypeError):
        return None, state
    if fresh and pid and _pid_alive(pid):
        return pid, state
    return None, state


def _port_free(host, port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(('127.0.0.1' if host in ('0.0.0.0', '') else host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _code_version():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO,
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace',
                              timeout=10).stdout.strip() or 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


def _redact_url(url):
    """Proxy URL with any userinfo replaced by *** — never print credentials."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url if '//' in url else '//'+url)
    host = parts.hostname
    if not host:
        return '<无法解析的代理地址>'
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    netloc = f'***@{host}:{port}' if parts.username else f'{host}:{port}'
    return urlunsplit((parts.scheme or 'http', netloc, '', '', ''))


def _proxy_alive(proxy_url, timeout=3.0, connect=None):
    """TCP-connect to the HTTPS_PROXY host:port. A configured-but-dead proxy
    otherwise only surfaces as JEV 451 at the next morning's double probe."""
    connect = connect or socket.create_connection
    from urllib.parse import urlsplit
    parts = urlsplit(proxy_url if '//' in proxy_url else '//'+proxy_url)
    host, port = parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)
    if not host or not port:
        raise ValueError('无法解析 HTTPS_PROXY 代理地址，请检查其格式（凭据不会回显）')
    try:
        with connect((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sqlite_backup(source, target):
    """Online consistent copy through SQLite's backup API (WAL content
    included) from a read-only connection — the same mechanism the storage
    layer's backup_to uses, without opening the app/migration stack. Writes a
    temp file first and renames, so ``target`` never exists partially."""
    import os
    import sqlite3
    from uuid import uuid4
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name+'.'+uuid4().hex+'.tmp')
    src = sqlite3.connect(f'file:{Path(source).as_posix()}?mode=ro', uri=True)
    try:
        dst = sqlite3.connect(str(temp))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    finally:
        src.close()


def _backup_if_due(db, account, today=None, backup=None):
    """One consistent daily backup per account+day under <runtime root's
    data>/backups/<account>/<date>/. Idempotent (skips when today's complete
    copy exists; cleans up and retries a 0-byte leftover from a failed
    attempt); never blocks startup — failures print a warning."""
    today = today or datetime.date.today()
    target = db.parents[2]/'backups'/account/today.isoformat()/db.name
    if target.exists():
        if target.stat().st_size > 0:
            return None
        try:
            target.unlink()  # 0-byte leftover of a failed attempt: retry now
        except OSError as exc:
            print(f'!! 无法清理损坏的 0 字节备份残留，今日跳过备份：{exc}')
            return None
    if not db.exists():
        print(f'账本文件尚不存在，跳过今日备份：{db}')
        return None
    try:
        (backup or _sqlite_backup)(db, target)
        print(f'今日账本备份完成：{target}')
        return target
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'!! 今日账本备份失败（不影响启动，请尽快手动备份）：{exc}')
        return None


def cmd_start(config):
    cfg, db = _load_runtime(config)
    root = db.parent
    root.mkdir(parents=True, exist_ok=True)
    pid, _state = _running(db)
    if pid:
        print(f'supervisor 已在运行（pid {pid}），不重复启动；status 查看状态')
        return 2
    if not _port_free(cfg.api.host, cfg.api.port):
        print(f'端口 {cfg.api.port} 已被占用：先停止占用它的旧服务，再启动')
        return 2
    proxy = os.environ.get('HTTPS_PROXY')
    if not proxy:
        print('未检测到 HTTPS_PROXY：外部 JEV 直连会被拒绝（451），'
              '请在设置了宿主代理的环境中启动（例如 HTTPS_PROXY=http://127.0.0.1:7897，'
              '150 主机当前为 Clash Verge 混合端口）')
        return 2
    try:
        alive = _proxy_alive(proxy)
    except ValueError as exc:
        print(str(exc))
        return 2
    if not alive:
        print(f'HTTPS_PROXY 指向的代理当前不可达：{_redact_url(proxy)}。'
              '请先启动宿主代理（如 Clash Verge，混合端口见其设置；150 主机当前为 7897），再启动监督器。'
              '凌晨或盘前可用 scripts\\check_morning_readiness.py 一键复检。')
        return 2
    _backup_if_due(db, cfg.account_id)
    # Inherit the full parent environment (SystemRoot/PATH are required for
    # Winsock); the proxy vars checked above ride along unchanged.
    env = os.environ.copy()
    env.setdefault('PYTHONUNBUFFERED', '1')
    env['PYTHONPATH'] = str(REPO/'src') + os.pathsep + env.get('PYTHONPATH', '')
    creation = 0
    if sys.platform == 'win32':
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB:
        # closing the launching console/job must not terminate trading (real
        # incident 2026-10-22 13:10: supervisor died with the launching job).
        creation = 0x00000008 | 0x00000200 | 0x01000000
    log = log_path(db).open('ab')
    try:
        try:
            proc = subprocess.Popen(
                [sys.executable, '-m', 'trademind.execution.auto_paper',
                 '--config', str(config)],
                cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, env=env, creationflags=creation)
        except OSError:
            if not creation:
                raise
            # Job forbids breakaway: launch without it rather than not at all.
            proc = subprocess.Popen(
                [sys.executable, '-m', 'trademind.execution.auto_paper',
                 '--config', str(config)],
                cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, env=env,
                creationflags=creation & ~0x01000000)
    finally:
        log.close()
    pid_file = root/'auto-paper.pid'
    pid_file.write_text(json.dumps({'pid': proc.pid, 'config': str(config),
                                    'account_id': cfg.account_id, 'db': str(db),
                                    'started_at': datetime.datetime.now(
                                        datetime.timezone.utc).isoformat()}),
                        encoding='utf-8')
    for _ in range(10):
        time.sleep(1)
        live, state = _running(db)
        if live:
            print(f'supervisor 已启动（pid {live}，账户 {cfg.account_id}）')
            print(f'状态: {state_path(db)}')
            print(f'日志: {log_path(db)}（工作台开市间隙 http://127.0.0.1:{cfg.api.port}）')
            return 0
        if proc.poll() is not None:
            break
    print('启动失败：supervisor 进程退出或心跳未出现，最近日志：')
    try:
        lines = log_path(db).read_text(encoding='utf-8', errors='replace').splitlines()
        for line in lines[-10:]:
            print('  ' + line)
    except OSError:
        pass
    return 1


def cmd_status(config):
    cfg, db = _load_runtime(config)
    pid, state = _running(db)
    report = {'running': bool(pid), 'pid': pid, 'account_id': cfg.account_id,
              'config': str(config), 'db': str(db),
              'code_version': _code_version(),
              'log': str(log_path(db)), 'state': str(state_path(db))}
    if state:
        report.update({k: state.get(k) for k in
                       ('heartbeat', 'phase', 'halted', 'health_status', 'message',
                        'calendar_covered', 'session_open', 'retry_at')})
        report['intraday_health'] = state.get('intraday_health')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if pid else 1


def cmd_stop(config):
    cfg, db = _load_runtime(config)
    pid, state = _running(db)
    if not pid:
        print('supervisor 未在运行（心跳缺失或进程已退出），无需停止')
        return 0
    stop_flag(db).write_text(datetime.datetime.now(
        datetime.timezone.utc).isoformat(), encoding='utf-8')
    print(f'停止请求已写入，等待排空（pid {pid}，最长 {GRACE_SECONDS}s）…')
    deadline = time.monotonic()+GRACE_SECONDS
    while time.monotonic() < deadline:
        time.sleep(2)
        if not _pid_alive(pid):
            print('supervisor 已退出，子进程已排空')
            return 0
    # Escalation: only this exact pid, only a python image, never a stale pid.
    image = _image_name(pid)
    if image and image.startswith('python'):
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, text=True)
        time.sleep(3)
        if _pid_alive(pid):
            print('强制结束失败：进程仍在运行，请人工检查')
            return 1
        try:
            state_path(db).write_text(json.dumps(
                {**(state or {}), 'halted': True,
                 'message': 'Windows强制结束，结果未知，需人工核对后才能恢复'},
                ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass
        print('排空超时，已强制结束进程树；结果未知，已标记 halted，'
              '人工核对账本（runner recover）并确认后，编辑 state 文件清除 halted 再启动')
        return 1
    print(f'pid {pid} 的进程映像为 {image!r}，不是本项目的 python 进程；不终止，请人工检查')
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop'))
    parser.add_argument('--config', default='config/t0-local.local.toml')
    args = parser.parse_args(argv)
    return {'start': cmd_start, 'status': cmd_status, 'stop': cmd_stop}[args.action](args.config)


if __name__ == '__main__':
    raise SystemExit(main())
