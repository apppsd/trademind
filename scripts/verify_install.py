"""Disposable clean-environment installation verification; no provider calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import json

root = Path(__file__).resolve().parents[1]
def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        # Do not echo pip's environment URLs or external configuration.
        print(json.dumps({'step': args[1:4], 'exit_code': result.returncode, 'failed': True}))
        raise SystemExit(result.returncode)
    return result.stdout

with TemporaryDirectory(prefix='trademind_clean_') as folder:
    temp = Path(folder)
    run([sys.executable, '-m', 'venv', str(temp / 'venv')], root)
    py = str(temp / 'venv' / 'Scripts' / 'python.exe')
    run([py, '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], root)
    print('CLEAN_REQUIREMENTS_INSTALL: PASS', flush=True)
    result = run([py, '-m', 'pytest'], root)
    print(result[-1600:], flush=True)
    run([py, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(temp / 'wheels'), str(root)], root)
    wheel = next((temp / 'wheels').glob('trademind-*.whl'))
    run([py, '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(wheel)], temp)
    smoke = ("from pathlib import Path; from trademind.storage import Database, verify_database; "
             "from trademind.market.calendar import TradingCalendar; from trademind.market.adapters.fixture import FixtureAdapter; "
             "import trademind, tempfile, os; db=Database('sqlite:///:memory:'); "
             "assert db.applied_migrations() and max(db.applied_migrations()) <= 2; "
             "target=os.path.join(tempfile.mkdtemp(), 'smoke.db'); db.backup_to(target); "
             "assert verify_database(target)['ok']; db.close(); "
             "assert TradingCalendar.load()._days; assert FixtureAdapter().root.exists(); "
             "assert (Path(trademind.__file__).parent/'api/static/index.html').exists(); "
             "print('WHEEL_RESOURCES: PASS')")
    print(run([py, '-c', smoke], temp), flush=True)
    print(run([py, '-m', 'trademind.runner', 'replay-demo', '--days', '5'], temp), flush=True)
    print('CLEAN_WHEEL_OFFLINE_CLI: PASS')
