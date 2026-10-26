"""Optional webhook notifications for unattended-run state changes.

Disabled unless ``TRADEMIND_NOTIFY_WEBHOOK`` is set (an HTTP(S) URL accepting
JSON POST). Payload uses the DingTalk custom-robot text format, the most
common setup for personal alerting; generic JSON receivers work too.

Hard rule: a broken or missing notification channel must never affect
trading — every failure is swallowed here and reported as ``False``.
Content is composed by the caller from whitelisted state fields only
(account alias, phase, Chinese status text); no credentials, no endpoints.
"""
import json
import os
import urllib.request


def notify_webhook(text, url=None, timeout=5.0, post=None):
    """POST ``text`` as a DingTalk-style text message. Returns True when a
    webhook is configured and the POST succeeded; False otherwise (including
    "not configured"). Never raises."""
    url = url or os.environ.get('TRADEMIND_NOTIFY_WEBHOOK')
    if not url:
        return False
    try:
        (post or _post_json)(url, {'msgtype': 'text', 'text': {'content': 'TradeMind：' + text}}, timeout)
        return True
    except Exception:  # noqa: BLE001 - notification must never break trading
        return False


def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout):
        pass  # HTTP errors raise here and are swallowed by notify_webhook
