"""One-shot morning readiness check for the unattended N150 paper setup.

Read-only: no model calls, no ledger writes, no credentials read (the .env is
only stat-ed). Exit codes: 0 = ready for today's session; 1 = at least one
blocking failure (proxy dead / supervisor dead / halted / wrong account /
missing key file); 2 = not blocking but needs attention or data missing.
Usage:
    .venv\\Scripts\\python.exe scripts\\check_morning_readiness.py [--config config/t0-local.local.toml]
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[1]
RESULTS = []


def _load_scripts_module(name):
    scripts = REPO/'scripts'
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(name, scripts/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(level, title, detail):
    mark = {'ok': '[OK]', 'warn': '[!!]', 'fail': '[XX]', 'info': '[--]'}[level]
    RESULTS.append((level, title, detail))
    print(f'{mark} {title}：{detail}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='晨间就绪体检（只读，零调用）')
    parser.add_argument('--config', default='config/t0-local.local.toml')
    parser.add_argument('--timeout', type=float, default=5.0)
    args = parser.parse_args(argv)
    os.chdir(REPO)
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.timezone.utc)

    run_local = _load_scripts_module('run_local')

    # 0. explicit config file guard: load_config silently falls back to
    # defaults for a missing path, which would check the WRONG account.
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = REPO/config_path
    if not config_path.exists():
        record('fail', '配置文件', f'{args.config} 不存在——请在 150 主机的 D:\\trademind 仓库内运行，'
                                 '或用 --config 指定 t0-local 配置')
        return _finish()

    # 1. proxy liveness (blocks the JEV probes at open)
    proxy = os.environ.get('HTTPS_PROXY')
    if not proxy:
        record('fail', '代理环境变量', '当前 shell 未设置 HTTPS_PROXY（启动监督器必须携带）')
    else:
        try:
            alive = run_local._proxy_alive(proxy, timeout=args.timeout)
            record('ok' if alive else 'fail', '代理连通',
                   (f'{proxy} 可达，JEV 通路就绪' if alive else
                    f'{proxy} 不可达——请先启动宿主代理（150 主机为 Clash Verge，混合端口 7897）'))
        except ValueError as exc:
            record('fail', '代理连通', str(exc))

    # 2. config + ledger paths
    try:
        cfg, db = run_local._load_runtime(args.config)
        record('ok', '配置加载', f'{args.config} → 账户 {cfg.account_id}，库 {db}')
    except SystemExit as exc:
        record('fail', '配置加载', str(exc))
        return _finish()
    except (OSError, ValueError) as exc:
        record('fail', '配置加载', f'{args.config} 无法加载：{exc}')
        return _finish()

    # 3. supervisor state: alive / halted / heartbeat / calendar coverage
    pid, state = run_local._running(db)
    if pid:
        age = int(run_local._heartbeat_age(state))
        halted = bool(state.get('halted'))
        record('fail' if halted else 'ok', '监督器',
               f'运行中 pid {pid}，心跳 {age}s 前，halted={halted}'
               + ('——处于人工核对暂停，不能自动交易' if halted else ''))
    else:
        record('fail', '监督器', '没有存活的心跳——请先 run_local.py start（启动会先做代理预检与当日备份）')
    if state:
        record('ok' if state.get('calendar_covered') else 'fail', '交易日历覆盖',
               '已覆盖' if state.get('calendar_covered') else '状态显示覆盖不足（超过覆盖范围会拒绝开市）')
    else:
        record('warn', '交易日历覆盖', '无状态文件可读，无法确认')

    # 4. key file presence (never read)
    env_file = REPO/'.env'
    if env_file.exists() and env_file.stat().st_size > 0:
        record('ok', '密钥文件', '.env 存在（未读取内容）')
    else:
        record('fail', '密钥文件', '.env 缺失或为空——双模型探针将无法通过')

    # 5. account identity through the read-only workbench endpoint
    try:
        with urllib.request.urlopen(f'http://{cfg.api.host}:{cfg.api.port}/api/workbench/overview',
                                    timeout=args.timeout) as resp:
            overview = json.loads(resp.read().decode('utf-8'))
        alias = overview.get('account_alias')
        if alias == cfg.account_id:
            record('ok', '账户身份', f'工作台信封账户 {alias} 与配置一致')
        else:
            record('fail', '账户身份', f'工作台信封账户是 {alias!r}，不是配置的 {cfg.account_id!r}——存在旧实例占用端口')
    except Exception:  # noqa: BLE001 - any failure here is non-blocking (serve may be swapped by paper)
        record('warn', '账户身份', f'{cfg.api.host}:{cfg.api.port} 不可达（盘中 paper 运行属正常；休市时应可访问）')

    # 6. today is a trading day + calendar coverage from the calendar itself
    try:
        from trademind.market.calendar import TradingCalendar
        calendar = TradingCalendar.load()
        if today > calendar.coverage_end:
            record('fail', '交易日历', f'今日 {today} 超出日历覆盖（{calendar.coverage_end}）——需更新日历')
        elif not calendar.is_trading_day(today):
            record('info', '交易日历', f'今日 {today} 非交易日，监督器将休市等待')
        else:
            record('ok', '交易日历', f'今日 {today} 为交易日，日历覆盖至 {calendar.coverage_end}')
    except Exception as exc:  # noqa: BLE001 - calendar problems must be visible, not fatal to the check
        record('warn', '交易日历', f'无法加载日历：{exc}')

    # 7. freshest backup
    backups = db.parents[2]/'backups'/cfg.account_id
    if backups.exists():
        days = sorted((d.name for d in backups.iterdir() if d.is_dir()), reverse=True)
        if days:
            age_days = (today - datetime.date.fromisoformat(days[0])).days
            record('ok' if age_days <= 3 else 'warn', '账本备份',
                   f'最近备份 {days[0]}（{age_days} 天前）；启动时自动备份当日')
        else:
            record('warn', '账本备份', '备份目录为空——下次 start 会生成当日备份')
    else:
        record('warn', '账本备份', '尚无备份目录——下次 start 会生成当日备份')

    # 8. disk space
    free_gb = shutil.disk_usage(str(REPO)).free/2**30
    record('ok' if free_gb > 1 else 'warn', '磁盘空间', f'仓库所在盘剩余 {free_gb:.1f} GB')

    return _finish()


def _finish():
    levels = [level for level, _, _ in RESULTS]
    print('')
    if 'fail' in levels:
        print('结论：存在阻断项，今天无法正常交易——按上方 [XX] 逐项处理后重跑本脚本。')
        return 1
    if 'warn' in levels:
        print('结论：无阻断项，但需注意 [!!] 提示。')
        return 2
    print('结论：就绪——监督器会在开市后自动预检并进入模拟交易。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
