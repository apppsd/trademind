"""Chinese instrument catalog for the workbench (TASK-20261026-04, ARCH-025).

Display-only identity layer on top of the verified ETF whitelist:

- ``instrument_id`` stays the internal key for URLs, requests, orders;
  Chinese names are presentation aliases and never feed Guard/OMS.
- ``display_name`` short labels are an explicit, recorded mapping so the UI
  can lead with readable Chinese while details keep the full fund name.
- Unknown instruments degrade to ``名称待补充`` + code; nothing is invented.
- Zero network, zero provider, zero account writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

# 展示简称映射（需求第6.5节，用户确认口径；全称始终保留在 full_name）。
# 只影响显示，不改交易白名单与标的身份。
DISPLAY_ALIASES: dict[str, str] = {
    '513050.SH': '中概互联ETF',
    '513100.SH': '纳指100ETF',
    '518880.SH': '黄金ETF',
    '510050.SH': '上证50ETF',
}


def _whitelist_path() -> Path:
    source = Path(__file__).resolve().parents[3] / 'data' / 'etf_whitelist.json'
    return source if source.exists() else Path(sys.prefix) / 'share' / 'trademind' / 'etf_whitelist.json'


def _market_label(market: str | None) -> str | None:
    return {'XSHG': '上交所', 'XSHE': '深交所'}.get(market or '', market)


def _kind_label(kind: str | None) -> str | None:
    return {'etf': 'ETF'}.get(kind or '', kind)


def load_catalog(db_path: Path | None = None) -> dict[str, Any]:
    """Build the instrument directory DTO. Never touches providers or accounts."""
    now = datetime.now(timezone.utc).isoformat()
    entries: list[dict[str, Any]] = []
    source_counts: dict[str, str] = {}
    path = _whitelist_path()
    whitelist: list[dict[str, Any]] = []
    if path.exists():
        try:
            whitelist = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(whitelist, list):
                whitelist = []
        except (OSError, ValueError):
            whitelist = []
    for info in whitelist:
        symbol = str(info.get('symbol') or '')
        if not symbol:
            continue
        full_name = info.get('name') or None
        entries.append({
            'instrument_id': symbol,
            'display_name': DISPLAY_ALIASES.get(symbol),
            'full_name': full_name,
            'market': info.get('market'),
            'kind': info.get('security_type'),
            'currency': 'CNY',
            'name_source': 'whitelist',
            'aliases': ([DISPLAY_ALIASES[symbol]] if symbol in DISPLAY_ALIASES else []),
            'capabilities': {
                'tradable': False,  # 显式应用配置绑定后才声明T+0白名单资格
                'settlement_rule': info.get('settlement_rule'),
                'lot_size': info.get('lot_size'),
                'intraday_bars': None,  # 由曲线端点按本地存量声明，目录不臆造
                'daily_bars': False,
            },
        })
    if not whitelist:
        source_counts['whitelist'] = '白名单文件缺失或为空，目录仅含未知占位'
    return {'schema_version': 1, 'generated_at': now,
            'instruments': entries, 'source_notes': source_counts}


def entry_for(instruments: list[dict[str, Any]], instrument_id: str) -> dict[str, Any] | None:
    for e in instruments:
        if e['instrument_id'] == instrument_id:
            return e
    return None


def attach_context_capabilities(instruments: list[dict[str, Any]], store: Any) -> None:
    """Fill intraday_bars capability from local Context coverage (bounded read)."""
    for e in instruments:
        e['capabilities']['intraday_bars'] = store.has_minute_contexts(e['instrument_id'])


def attach_purchase_checks(instruments, store, cfg, now):
    """Reuse the offline frozen-limit diagnostic; never infer order authority."""
    from datetime import timedelta
    from .workbench_charts import collect_minute_bars
    from .read_models import _parse
    from ..evaluation.tradability import evaluate_from_raw
    from ..market.profiles import get_profile

    for item in instruments:
        cap = item['capabilities']
        cap['tradable'] = (cfg.t0.enabled and item['instrument_id'] in cfg.t0.whitelist
                           and str(cap.get('settlement_rule')).upper() in ('T0', 'T+0'))
        item['purchase_check'] = {'status': '数据不足', 'reasons': ['尚无新鲜完整账户与行情快照']}
    with store._connect() as conn:
        account, at, _ = store.account_block(conn)
        if account is None or at is None or not 0 <= (now-at).total_seconds() <= 30 or not cfg.t0.enabled:
            return
        snap = json.loads(conn.execute('SELECT payload_json FROM account_state WHERE account_id=?', (store.account_id,)).fetchone()[0])
        orders = [json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM orders WHERE account_id=?', (store.account_id,))] if 'orders' in store._tables(conn) else []
    ids = {e['instrument_id'] for e in instruments} | {p['instrument_id'] for p in snap.get('positions', [])}
    prices = []
    for iid in sorted(ids):
        bars, _ = collect_minute_bars(store, iid, now-timedelta(days=1), now)
        bar = bars[-1] if bars else {}
        prices.append({'instrument_id': iid, 'price': bar.get('close'), 'price_time': bar.get('bar_time'), 'price_source': 'local_context'})
    raw = {'snapshot_version': 1, 'account_id': store.account_id, 'as_of': now.isoformat(),
           'cash': account['cash'], 'reserved_cash': account['reserved_cash'], 'positions': snap.get('positions', []),
           'open_orders': orders, 'instruments': prices,
           'limits': {key: str(getattr(cfg.t0, key)) for key in ('max_single_exposure', 'max_total_exposure', 'min_cash_reserve')}}
    def profile(iid):
        try:
            return get_profile(iid)
        except ValueError:
            return None
    report = evaluate_from_raw(raw, profile, 90)
    by_id = {e['instrument_id']: e for e in report['instruments']}
    for item in instruments:
        check = by_id[item['instrument_id']]
        if not item['capabilities']['tradable']:
            check = {'status': '仅观察', 'reasons': ['不在本账户T+0配置白名单内']}
        item['purchase_check'] = check


def search_instruments(instruments: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """中文/全称/代码子串匹配；空查询返回全部。大小写不敏感。"""
    q = (query or '').strip().lower()
    if not q:
        return instruments
    out = []
    for e in instruments:
        haystack = ' '.join(filter(None, [
            e.get('display_name'), e.get('full_name'), e.get('instrument_id'),
            *(e.get('aliases') or []),
        ])).lower()
        if q in haystack:
            out.append(e)
    return out


def entry_payload(entry: dict[str, Any] | None, instrument_id: str) -> dict[str, Any]:
    """Unknown instrument degrades to 名称待补充 + code (no invented company)."""
    if entry is not None:
        return entry
    return {
        'instrument_id': instrument_id,
        'display_name': None,
        'full_name': None,
        'market': None,
        'kind': None,
        'currency': None,
        'name_source': 'unknown',
        'aliases': [],
        'capabilities': {'tradable': False, 'settlement_rule': None, 'lot_size': None,
                         'intraday_bars': None, 'daily_bars': False},
    }
