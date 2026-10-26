"""Optional webhook alerts (Task-20261026-23): notify_webhook contract."""
import pytest
from trademind.observability.notify import notify_webhook


def test_not_configured_is_a_silent_noop(monkeypatch):
    monkeypatch.delenv('TRADEMIND_NOTIFY_WEBHOOK', raising=False)
    assert notify_webhook('任何文本') is False


def test_sends_dingtalk_text_payload(monkeypatch):
    calls = []

    def post(url, payload, timeout):
        calls.append((url, payload, timeout))
        return True

    assert notify_webhook('监督器已暂停', url='http://127.0.0.1:9/hook',
                          timeout=2.5, post=post) is True
    url, payload, timeout = calls[0]
    assert url == 'http://127.0.0.1:9/hook' and timeout == 2.5
    assert payload['msgtype'] == 'text'
    assert '监督器已暂停' in payload['text']['content']


def test_env_url_used_when_explicit_missing(monkeypatch):
    monkeypatch.setenv('TRADEMIND_NOTIFY_WEBHOOK', 'http://127.0.0.1:9/env-hook')
    seen = {}

    def post(url, payload, timeout):
        seen['url'] = url
        return True

    assert notify_webhook('x', post=post) is True
    assert seen['url'] == 'http://127.0.0.1:9/env-hook'


def test_failure_never_raises(monkeypatch):
    def boom(url, payload, timeout):
        raise OSError('network down')

    assert notify_webhook('x', url='http://127.0.0.1:9/hook', post=boom) is False


def test_supervisor_transition_alerts_fire_once_and_rearm(tmp_path, monkeypatch):
    """Drive the real tick(): halted False→True alerts once; clearance
    re-arms; calendar-coverage loss likewise. Environment has no webhook —
    the injected post records calls instead."""
    from datetime import datetime, timedelta, timezone
    import trademind.execution.auto_paper as ap
    from trademind.execution.auto_paper import Supervisor

    sent = []

    def post(url, payload, timeout):
        sent.append(payload['text']['content'])
        return True

    class Calendar:
        coverage_start = coverage_end = None

    now = datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc)  # 盘前 09:30+08
    sup = Supervisor(config='missing.local.toml', root=tmp_path, calendar=Calendar(),
                     clock=lambda: now, account_id='paper-test', db_path=tmp_path/'db.sqlite')
    sup.retry_at = now
    sup.observer_retry_at = now+timedelta(hours=1)  # keep ensure_observer inert
    covered = [True]
    monkeypatch.setattr(ap, 'calendar_covered', lambda calendar, now: covered[0])
    monkeypatch.setattr(ap, 'session_seconds', lambda calendar, now: 0)
    monkeypatch.setattr(ap, 'notify_webhook', lambda text, **kw: sent.append(text) or True)

    sup.halted = True
    sup.note = '测试暂停'
    sup.tick()
    sup.tick()
    assert sum('需要人工核对' in s for s in sent) == 1  # once, not per tick

    sup.halted = False
    sup.tick()
    sup.halted = True
    sup.note = '再次暂停'
    sup.tick()
    assert sum('需要人工核对' in s for s in sent) == 2  # re-armed after clearance

    sup.halted = False
    covered[0] = False
    sup.tick()
    sup.tick()
    assert sum('交易日历覆盖不足' in s for s in sent) == 1  # once
    covered[0] = True
    sup.tick()
    covered[0] = False
    sup.tick()
    assert sum('交易日历覆盖不足' in s for s in sent) == 2  # re-armed
