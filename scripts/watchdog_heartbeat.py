"""Dead-man heartbeat watchdog: the supervisor cannot report its own death.

Standalone small step meant for a Windows scheduled task (registration is a
user decision — see operations.md). Reads the auto-paper state file; if the
heartbeat is stale beyond the threshold it writes a local alert file and,
when configured, fires the same webhook as the supervisor (TRADEMIND_NOTIFY_WEBHOOK).
Read-only with respect to the ledger; never starts or stops anything.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STALE_SECONDS = 120  # supervisor heartbeats every ~5s; 2min means it is gone


def check(root, now=None, stale_seconds=STALE_SECONDS, notify=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state_path = Path(root)/'auto-paper-state.json'
    alert_path = Path(root)/'watchdog-alert.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'no-state', '状态文件不可读或不存在（监督器可能从未启动）'
    try:
        stamp = datetime.datetime.fromisoformat(state['heartbeat'])
        age = (now-stamp).total_seconds()
    except (KeyError, ValueError, TypeError):
        return 'no-state', '状态文件缺心跳字段'
    if state.get('terminal'):
        return 'halted', '监督器处于终态（已停止或待人工核对）'
    if age > stale_seconds:
        account = state.get('account_id', '未知账户')
        message = (f'看门狗：监督器心跳停止（{account}，最后心跳 {int(age)} 秒前）。'
                   '模拟交易未在运行，请检查主机/代理并重新 run_local start。')
        if notify:
            notify(message)
        alert_path.write_text(json.dumps({'alerted_at': now.isoformat(), 'age_seconds': age,
                                          'account': account}, ensure_ascii=False),
                              encoding='utf-8')
        return 'stale', message
    return 'ok', f'心跳正常（{int(age)} 秒前）'


def main(argv=None):
    parser = argparse.ArgumentParser(description='监督器心跳看门狗（只读+告警）')
    parser.add_argument('--root', default=str(REPO/'data'/'runtime'/'local-1k'))
    args = parser.parse_args(argv)

    def notify(text):
        from trademind.observability.notify import notify_webhook
        notify_webhook(text)

    status, message = check(args.root, notify=notify)
    print(message)
    return 0 if status in ('ok', 'halted') else 1


if __name__ == '__main__':
    sys.exit(main())
