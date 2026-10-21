"""Free market discovery, observations and explicit account eligibility.

Snapshot data never authorizes orders or substitutes for minute fill bars.
Unknown exchange rules and account access stay unknown rather than inferred
from names, code prefixes or the ability to download a quote.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
import time

from ..config import MarketAccessConfig
from .profiles import _get_etf_map

MARKETS = ('etf', 'lof', 'convertible_bond', 'hk_stock', 'futures')
PERMISSIONS = {'etf': 'cn_fund', 'lof': 'cn_fund',
               'convertible_bond': 'convertible_bond', 'hk_stock': 'hk_stock', 'futures': 'futures'}
ENDPOINTS = {'etf': 'fund_etf_spot_em', 'lof': 'fund_lof_spot_em',
             'convertible_bond': 'bond_zh_hs_cov_spot', 'hk_stock': 'stock_hk_spot_em'}
CN_TIMEZONE = __import__('zoneinfo').ZoneInfo('Asia/Shanghai')
CB_SOURCE = 'https://www.sse.com.cn/lawandrules/sselawsrules2025/bond/trading/currency/c/c_20250606_10781040.shtml'


def number(value) -> Decimal | None:
    try:
        out = Decimal(str(value))
        return out if out.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def first(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value) not in ('', 'nan', 'None', '--'):
            return value
    return None


def instrument_id(value, kind, exchange=None):
    code = str(value).strip()
    if kind == 'futures':
        # Actual listed delivery contract only; continuous/main synthetic codes
        # are not buyable instruments.
        if not re.fullmatch(r'[A-Za-z]{1,3}\d{3,4}', code):
            raise ValueError('not an actual futures contract')
        if exchange not in ('SHFE', 'DCE', 'CZCE', 'CFFEX', 'GFEX', 'INE'):
            raise ValueError('unknown futures exchange')
        if exchange == 'SHFE' and re.match(r'^(SC|NR|LU|BC|EC)\d', code, re.I):
            exchange = 'INE'
        return f'{code.upper()}.{exchange}'
    if kind == 'hk_stock':
        if not re.fullmatch(r'\d{1,5}', code):
            raise ValueError('invalid Hong Kong symbol')
        return f'{code.zfill(5)}.HK'
    match = re.fullmatch(r'(?:(sh|sz))?(\d{6})(?:\.(SH|SZ))?', code, re.I)
    if not match:
        raise ValueError('invalid mainland symbol')
    prefix, code, suffix = match.groups()
    market = suffix or (prefix.upper() if prefix else 'SH' if code[0] in '56' or code.startswith('11') else 'SZ')
    return f'{code}.{market}'


def source_timestamp(row):
    # A clock without its date can be yesterday's closing quote. Never attach
    # today's date or observation time to make it appear fresh.
    value = first(row, 'source_time', '更新时间', '时间', 'datetime')
    if value is None:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp.replace(tzinfo=CN_TIMEZONE) if stamp.tzinfo is None else stamp
    except ValueError:
        return None


@dataclass
class AssetQuote:
    instrument_id: str
    kind: str
    name: str
    currency: str
    price: Decimal | None
    turnover: Decimal | None
    observed_at: datetime
    source_time: datetime | None
    endpoint: str

    def as_dict(self):
        return {'instrument_id': self.instrument_id, 'kind': self.kind, 'name': self.name,
                'currency': self.currency, 'price': str(self.price) if self.price is not None else None,
                'turnover': str(self.turnover) if self.turnover is not None else None,
                'observed_at': self.observed_at.isoformat(),
                'source_time': self.source_time.isoformat() if self.source_time else None,
                'provider': 'akshare', 'endpoint': self.endpoint,
                'data_type': 'quote_snapshot', 'replay_point_in_time': False}


def normalize(rows, kind, endpoint, observed, exchange=None):
    result, rejected = [], 0
    for row in rows:
        try:
            iid = instrument_id(first(row, '代码', 'code', 'symbol'), kind, exchange)
            price = number(first(row, '最新价', 'trade', 'price'))
            if price is not None and price <= 0:
                price = None
            result.append(AssetQuote(iid, kind, str(first(row, '名称', 'name') or ''),
                                     'HKD' if kind == 'hk_stock' else 'CNY', price,
                                     number(first(row, '成交额', 'amount')),
                                     observed, source_timestamp(row), endpoint))
        except (ValueError, TypeError):
            rejected += 1
    return result, rejected


def rules_for(quote, overrides):
    rule = {}
    if quote.kind == 'etf':
        info = _get_etf_map().get(quote.instrument_id)
        if info:
            rule = {'t0': info['settlement_rule'] == 'T+0', 'lot_size': info.get('lot_size', 100),
                    'source_url': info.get('source_url', ''), 'currency': 'CNY'}
    # Convertible bonds have a T0 category rule; other contract details still
    # require a per-instrument rule, not the stock profile inference.
    if quote.kind == 'convertible_bond':
        rule = {'t0': True, 'source_url': CB_SOURCE, 'currency': 'CNY'}
    extra = overrides.get(quote.instrument_id, {})
    if extra:
        if not isinstance(extra, dict) or not str(extra.get('source_url', '')).startswith('https://'):
            raise ValueError('rules require an official source reference')
        allowed = {'t0', 'lot_size', 'source_url', 'currency', 'contract_multiplier',
                   'margin_rate', 'fee_buffer', 'effective_until'}
        if set(extra) - allowed:
            raise ValueError('unknown market rule')
        rule.update(extra)
    return rule


def eligibility(quote, cfg, overrides, now=None):
    now = now or datetime.now(timezone.utc)
    rule = rules_for(quote, overrides)
    reasons = []
    permission = PERMISSIONS[quote.kind]
    if permission not in cfg.confirmed_permissions:
        reasons.append('account_permission_unconfirmed')
    if rule.get('t0') is not True:
        reasons.append('not_t0' if rule.get('t0') is False else 't0_rule_unverified')
    if rule.get('currency', quote.currency) != quote.currency:
        reasons.append('rule_currency_mismatch')
    if rule.get('effective_until') and str(rule['effective_until']) < now.date().isoformat():
        reasons.append('rules_expired')
    lot = number(rule.get('lot_size'))
    if lot is None or lot <= 0 or lot != lot.to_integral_value():
        reasons.append('lot_size_unverified')
        lot = None
    fee = number(rule.get('fee_buffer'))
    if fee is None or fee < 0:
        reasons.append('fees_unverified')
        fee = None
    minimum = quote.price * lot if quote.price is not None and lot is not None else None
    if quote.price is None:
        reasons.append('quote_unavailable')
    if quote.kind == 'futures':
        multiplier, margin = number(rule.get('contract_multiplier')), number(rule.get('margin_rate'))
        if multiplier is None or multiplier <= 0 or margin is None or not 0 < margin <= 1:
            reasons.append('margin_contract_unverified')
            minimum = None
        elif minimum is not None:
            minimum *= multiplier * margin
    total = minimum + fee if minimum is not None and fee is not None else None
    cash = cfg.cash_hkd - cfg.reserve_hkd if quote.currency == 'HKD' else cfg.cash_cny - cfg.reserve_cny
    if minimum is not None and (minimum > cash or total is not None and total > cash):
        reasons.append('insufficient_cash')
    age = (now - quote.source_time).total_seconds() if quote.source_time else None
    freshness = 'unknown' if age is None else 'fresh' if 0 <= age <= 180 else 'future' if age < 0 else 'stale'
    executable = (quote.kind == 'etf' and quote.instrument_id in _get_etf_map()
                  and _get_etf_map()[quote.instrument_id]['settlement_rule'] == 'T+0')
    return {'permission_required': permission, 't0': rule.get('t0'),
            'rule_source': rule.get('source_url'), 'lot_size': int(lot) if lot is not None else None,
            'minimum_principal_or_margin': str(minimum) if minimum is not None else None,
            'minimum_with_fee_buffer': str(total) if total is not None else None,
            'available_cash': str(max(Decimal('0'), cash)), 'freshness': freshness,
            'purchase_conditions_met': not reasons, 'blocking_reasons': reasons,
            'execution_supported': executable,
            'paper_candidate': not reasons and freshness == 'fresh' and executable,
            'execution_note': 'requires_existing_T0_Guard_OMS_and_dual_model_gate' if executable
                              else 'market_execution_adapter_not_implemented'}


@contextmanager
def bounded_requests(deadline, timeout):
    """Bound SDKs without timeout arguments for this sequential read-only CLI.

    Preserve proxy settings; errors are summarized by class only.
    """
    import requests
    original = requests.Session.request
    def request(session, *args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout('market scan deadline')
        limit = min(timeout, remaining)
        supplied = kwargs.get('timeout')
        if isinstance(supplied, (int, float)):
            limit = min(limit, supplied)
        kwargs['timeout'] = limit
        return original(session, *args, **kwargs)
    requests.Session.request = request
    try:
        yield
    finally:
        requests.Session.request = original


class MultiAssetMarket:
    def __init__(self, cfg, sdk=None):
        self.cfg = cfg
        if not cfg.markets or set(cfg.markets) - set(MARKETS):
            raise ValueError('unknown/empty market selection')
        if set(cfg.confirmed_permissions) - set(PERMISSIONS.values()):
            raise ValueError('unknown permission')
        if any(not value.is_finite() or value < 0 for value in
               (cfg.cash_cny, cfg.cash_hkd, cfg.reserve_cny, cfg.reserve_hkd)):
            raise ValueError('invalid account balances')
        if not 1 <= cfg.request_timeout_seconds <= 60 or not 1 <= cfg.scan_timeout_seconds <= 3600:
            raise ValueError('invalid scan/request timeout')
        self.sdk = sdk
        self.rules = {}
        if cfg.rules_file:
            self.rules = json.loads(Path(cfg.rules_file).read_text(encoding='utf-8'))
            if not isinstance(self.rules, dict):
                raise ValueError('rules must be a mapping')

    def scan(self):
        if self.sdk is None:
            from .adapters.live import _require
            self.sdk = _require('akshare')
        deadline = time.monotonic() + self.cfg.scan_timeout_seconds
        assets, sources = {}, []

        def fetch(kind, endpoint, kwargs=None, exchange=None, product=None):
            if time.monotonic() >= deadline:
                sources.append({'market': kind, 'endpoint': endpoint, 'product': product,
                                'status': 'not_scanned', 'error_type': 'DeadlineExceeded'})
                return
            try:
                frame = getattr(self.sdk, endpoint)(**(kwargs or {}))
                rows = frame.to_dict('records')
                quotes, rejected = normalize(rows, kind, endpoint, datetime.now(timezone.utc), exchange)
                for quote in quotes:
                    payload = quote.as_dict()
                    payload.update(eligibility(quote, self.cfg, self.rules))
                    assets[quote.instrument_id] = payload
                sources.append({'market': kind, 'endpoint': endpoint, 'product': product,
                                'status': 'ok' if quotes and not rejected else 'partial' if quotes else 'empty',
                                'count': len(quotes), 'rejected_rows': rejected})
            except Exception as exc:
                # No URLs, response bodies or SDK exception text.
                sources.append({'market': kind, 'endpoint': endpoint, 'product': product,
                                'status': 'error', 'error_type': type(exc).__name__})

        with bounded_requests(deadline, self.cfg.request_timeout_seconds):
            for kind in dict.fromkeys(self.cfg.markets):
                if kind != 'futures':
                    fetch(kind, ENDPOINTS[kind])
                    continue
                try:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('deadline')
                    products = self.sdk.futures_symbol_mark().to_dict('records')
                    if not products:
                        raise ValueError('empty futures product catalog')
                except Exception as exc:
                    sources.append({'market': kind, 'endpoint': 'futures_symbol_mark',
                                    'status': 'error', 'error_type': type(exc).__name__})
                    continue
                for row in products:
                    mark = str(row.get('mark', '')).lower()
                    exchange_name = str(row.get('exchange', ''))
                    names = {'郑州': 'CZCE', '大连': 'DCE', '上海期货': 'SHFE',
                             '金融期货': 'CFFEX', '广州': 'GFEX', '能源': 'INE'}
                    exchange = next((value for key, value in names.items() if key in exchange_name), None)
                    if exchange is None:
                        exchange = next((x.upper() for x in ('shfe', 'dce', 'czce', 'cffex', 'gfex', 'ine')
                                         if mark.startswith(x)), None)
                    if mark in ('shfe_sc', 'shfe_nr', 'shfe_lu', 'shfe_bc', 'shfe_ec'):
                        exchange = 'INE'
                    fetch(kind, 'futures_zh_realtime', {'symbol': row['symbol']}, exchange, str(row['symbol']))
        complete = all(s['status'] == 'ok' for s in sources)
        return {'status': 'success' if complete else 'incomplete', 'model_calls': 0,
                'mode': 'observation_only', 'markets_requested': self.cfg.markets,
                'sources': sources, 'instrument_count': len(assets),
                'purchase_conditions_met_count': sum(a['purchase_conditions_met'] for a in assets.values()),
                'paper_candidate_count': sum(a['paper_candidate'] for a in assets.values()),
                'assets': sorted(assets.values(), key=lambda a: (a['kind'], a['instrument_id']))}

    def minute_bars(self, iid, kind, period='1'):
        """On-demand observed bars; native units stay explicit and unmixed.

        No OHLC/turnover synthesis and no reinterpretation as the existing CNY
        MinuteBar contract. These are research observations, not OMS fills.
        """
        if kind not in MARKETS or period not in ('1', '5', '15', '30', '60'):
            raise ValueError('invalid minute request')
        code, _, suffix = iid.partition('.')
        expected = instrument_id(code, kind, suffix if kind == 'futures' else None)
        # Mainland suffix is authoritative; use it when normalizing.
        if kind in ('etf', 'lof', 'convertible_bond'):
            expected = instrument_id(iid, kind)
        if expected != iid:
            raise ValueError('noncanonical instrument identifier')
        if self.sdk is None:
            from .adapters.live import _require
            self.sdk = _require('akshare')
        endpoint = {'etf': 'fund_etf_hist_min_em', 'lof': 'fund_lof_hist_min_em',
                    'convertible_bond': 'bond_zh_hs_cov_min', 'hk_stock': 'stock_hk_hist_min_em',
                    'futures': 'futures_zh_minute_sina'}[kind]
        kwargs = {'symbol': suffix.lower() + code if kind == 'convertible_bond' else code, 'period': period}
        if kind != 'futures':
            kwargs['adjust'] = ''
        deadline = time.monotonic() + self.cfg.scan_timeout_seconds
        with bounded_requests(deadline, self.cfg.request_timeout_seconds):
            frame = getattr(self.sdk, endpoint)(**kwargs)
        observed = datetime.now(timezone.utc)
        bars, rejected = [], 0
        for row in frame.to_dict('records'):
            ts = source_timestamp(row)
            values = [number(first(row, cn, en)) for cn, en in
                      (('开盘', 'open'), ('最高', 'high'), ('最低', 'low'), ('收盘', 'close'))]
            o, h, low, close = values
            if (ts is None or ts > observed or any(v is None or v <= 0 for v in values)
                    or h < max(o, low, close) or low > min(o, h, close)):
                rejected += 1
                continue
            volume, amount = number(first(row, '成交量', 'volume')), number(first(row, '成交额', 'amount'))
            bars.append({'bar_time': ts.isoformat(), 'open': str(o), 'high': str(h),
                         'low': str(low), 'close': str(close),
                         'volume_native': str(volume) if volume is not None else None,
                         'amount_native': str(amount) if amount is not None else None,
                         'volume_unit': 'provider_native_unverified',
                         'amount_currency': 'HKD' if kind == 'hk_stock' else 'CNY',
                         'available_at': observed.isoformat()})
        bars.sort(key=lambda b: b['bar_time'])
        return {'status': 'success' if bars and not rejected else 'incomplete', 'model_calls': 0,
                'mode': 'observation_only', 'instrument_id': iid, 'kind': kind,
                'provider': 'akshare', 'endpoint': endpoint, 'period_minutes': int(period),
                'observed_at': observed.isoformat(), 'replay_point_in_time': False,
                'rejected_rows': rejected, 'bars': bars}
