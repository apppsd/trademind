"""Workbench read-only snapshot models (TASK-20261026-05, ARCH-025).

Pure read layer for the Chinese large-screen workbench. Design invariants:

- SQLite connections open ``mode=ro`` with ``PRAGMA query_only=ON``; a missing
  or unreadable database yields an honest ``unavailable`` envelope, never a
  created file, migration or account.
- The trading account is fixed from configuration; no endpoint accepts an
  ``account_id`` from the client.
- Money/price/quantity values cross the boundary as strings (Decimal round
  trip); ``null`` means "not computable", never ``0``.
- No provider access, no pipeline init, no writer lease, no daily-report
  generation: everything reads artifacts that already exist on disk.

The envelope shape is frozen in docs/requirements-workbench-v2.md §8.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ENVELOPE_SCHEMA_VERSION = 1

# 账本已知事件类型（与 reports/daily.py 口径一致）；未知类型不猜测含义。
_KNOWN_EVENT_KINDS = {'deposit', 'withdraw', 'reserve', 'release', 'fill_buy', 'fill_sell', 'rollover', 'fee'}


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return at.astimezone(timezone.utc) if at.utcoffset() is not None else None
    except ValueError:
        return None


def _shanghai_day_bounds(day: date) -> tuple[datetime, datetime]:
    from ..market.base import BEIJING
    start = datetime.combine(day, datetime.min.time(), BEIJING)
    return start, start + timedelta(days=1)


class WorkbenchReadStore:
    """Read-only access to one configured account's local facts."""

    def __init__(self, db_path: Path, account_id: str, account_alias: str | None = None) -> None:
        # resolve(): downstream as_uri() (read-only sqlite) rejects relative
        # paths, and config may carry a relative sqlite URL.
        self.db_path = Path(db_path).resolve()
        self.account_id = account_id
        self.account_alias = account_alias or account_id
        self._state_file = self.db_path.parent / 'auto-paper-state.json'
        self._intraday_file = self.db_path.parent / 'intraday-health.json'
        self._reports_dir = self.db_path.parent / 'daily-reports' / account_id

    # ------------------------------------------------------------------ base

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection | None]:
        """Short read-only transaction; yields None when the store is absent."""
        if not self.db_path.exists():
            yield None
            return
        conn = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            yield conn
        finally:
            conn.close()

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _envelope(self, *, source: str, data: Any, status: str = 'ok',
                  missing: list[str] | None = None, as_of: datetime | None = None,
                  stale_after: int | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            'schema_version': ENVELOPE_SCHEMA_VERSION,
            'account_alias': self.account_alias,
            'as_of': _iso(as_of or now),
            'generated_at': _iso(now),
            'data_status': status,
            'source': source,
            'age_seconds': max(0, int((now - as_of).total_seconds())) if as_of else None,
            'stale_after_seconds': stale_after,
            'partial': bool(missing) or status == 'partial',
            'missing_reasons': missing or [],
            'data': data,
        }

    # -------------------------------------------------------------- artifacts

    def _read_json(self, path: Path) -> tuple[Any, float] | None:
        """Atomic-read a status/report JSON; returns (payload, age_seconds)."""
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            return payload, max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
        except (OSError, ValueError, TypeError):
            return None

    def _read_report(self, day: date) -> tuple[dict[str, Any], float] | None:
        """Existing daily-report JSON only; never generates one."""
        path = self._reports_dir / f'{day.isoformat()}.json'
        got = self._read_json(path)
        if got is None or not isinstance(got[0], dict) or got[0].get('account_id') != self.account_id:
            return None
        return got

    def _auto_state(self) -> tuple[dict[str, Any], float] | None:
        got = self._read_json(self._state_file)
        if got is None or not isinstance(got[0], dict) or got[0].get('account_id') != self.account_id:
            return None
        return got

    # -------------------------------------------------------------- overview

    def account_block(self, conn: sqlite3.Connection | None) -> tuple[dict[str, Any] | None, datetime | None, list[str]]:
        """Latest persisted ledger snapshot; cash includes reserved."""
        if conn is None:
            return None, None, ['本地数据库不存在']
        if 'account_state' not in self._tables(conn):
            return None, None, ['账户尚未初始化（无账户状态记录）']
        row = conn.execute(
            'SELECT cash, reserved_cash, updated_at, payload_json FROM account_state WHERE account_id=?',
            (self.account_id,)).fetchone()
        if row is None:
            return None, None, [f'配置账户 {self.account_id} 在本地库中不存在']
        snap = json.loads(row['payload_json'])
        positions = snap.get('positions') or []
        valuation_complete = all(p.get('last_price') is not None for p in positions)
        cash = row['cash']
        reserved = row['reserved_cash']
        missing: list[str] = []
        if not valuation_complete:
            missing.append('部分持仓缺少最新可见价格，市值与权益未完整估值')
        data = {
            'cash': str(cash),
            'reserved_cash': str(reserved),
            'available_cash': str(self._dec(cash) - self._dec(reserved)),
            'market_value': str(snap['market_value']) if valuation_complete and snap.get('market_value') is not None else None,
            'equity': str(snap['equity']) if valuation_complete and snap.get('equity') is not None else None,
            'valuation_complete': valuation_complete,
            'positions_count': len(positions),
            'account_version': snap.get('version'),
        }
        return data, _parse(row['updated_at']), missing

    @staticmethod
    def _dec(value: Any):
        from decimal import Decimal
        return Decimal(str(value))

    def has_minute_contexts(self, instrument_id: str) -> bool:
        """Bounded check: any stored context carrying minute bars? (no scan-all)"""
        if not self.db_path.exists():
            return False
        try:
            with self._connect() as conn:
                if conn is None or 'context_snapshots' not in self._tables(conn):
                    return False
                row = conn.execute(
                    "SELECT 1 FROM context_snapshots WHERE instrument_id=? AND "
                    "json_extract(payload_json,'$.portfolio.account_id')=? AND "
                    "json_array_length(payload_json,'$.aux.minute_bars')>0 LIMIT 1", (instrument_id, self.account_id)).fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    def today_report_block(self, now: datetime) -> tuple[dict[str, Any] | None, datetime | None, list[str]]:
        """今日交易盈亏/模型费口径复用日报产物；缺产物则如实标记。"""
        from ..market.base import BEIJING
        today = now.astimezone(BEIJING).date()
        got = self._read_report(today)
        if got is None:
            return None, None, ['今日日报尚未生成，交易盈亏与模型费暂不可计算']
        report, age = got
        model = report.get('model_cost') or {}
        data = {
            'trading_pnl': report.get('trading_pnl'),
            'realized_pnl': report.get('realized_pnl'),
            'trading_fees': report.get('trading_fees'),
            'net_external_cash_flow': report.get('net_external_cash_flow'),
            'net_after_settled_model_estimate': report.get('net_after_settled_model_estimate'),
            'model_cost_settled': model.get('settled_estimate') if isinstance(model, dict) else None,
            'model_cost_pending': model.get('pending_reservation') if isinstance(model, dict) else None,
            'report_period_status': report.get('period_status'),
            'report_generated_at': report.get('generated_at'),
        }
        return data, _parse(report.get('as_of')), []

    def overview(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            account, account_as_of, missing = self.account_block(conn)
        report, report_as_of, report_missing = self.today_report_block(now)
        missing = missing + report_missing
        state = self._auto_state()
        runtime_brief = None
        if state:
            payload = state[0]
            if not isinstance(payload, dict):
                missing.append('自动监督状态文件存在但内容异常')
                runtime_brief = {'phase': None, 'halted': None, 'heartbeat_age_seconds': None}
            else:
                runtime_brief = {
                    'phase': payload.get('phase'),
                    'halted': payload.get('halted'),
                    'heartbeat_age_seconds': self._heartbeat_age(payload, now),
                }
        else:
            runtime_brief = {'phase': None, 'halted': None, 'heartbeat_age_seconds': None}
            missing.append('自动监督状态文件不存在（服务未运行或未启动过模拟）')
        status = 'ok' if not missing else ('partial' if account is not None else 'unavailable')
        data = {
            'account': account,
            'today': report,
            'runtime_brief': runtime_brief,
        }
        return self._envelope(source='account_state+daily_report_file+auto_paper_state',
                              data=data, status=status, missing=missing,
                              as_of=account_as_of, stale_after=10)

    # -------------------------------------------------------------- positions

    def positions(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            if conn is None:
                return self._envelope(source='account_state', data=None, status='unavailable',
                                      missing=['本地数据库不存在'])
            block, as_of, missing = self.account_block(conn)
            if block is None:
                return self._envelope(source='account_state', data=None, status='unavailable',
                                      missing=missing, as_of=as_of)
            snap = json.loads(conn.execute(
                'SELECT payload_json FROM account_state WHERE account_id=?',
                (self.account_id,)).fetchone()['payload_json'])
            out = []
            for p in snap.get('positions') or []:
                out.append({
                    'instrument_id': p.get('instrument_id'),
                    'quantity': str(p.get('quantity')),
                    'available_quantity': str(p.get('available_quantity')),
                    'avg_cost': str(p.get('avg_cost')),
                    'last_price': str(p['last_price']) if p.get('last_price') is not None else None,
                    'market_value': str(p.get('market_value')),
                    'unrealized_pnl': str(p['unrealized_pnl']) if p.get('unrealized_pnl') is not None else None,
                    'currency': p.get('currency'),
                })
            data = {'positions': out, 'valuation_complete': block['valuation_complete']}
            return self._envelope(source='account_state', data=data,
                                  status='partial' if missing else 'ok',
                                  missing=missing, as_of=as_of, stale_after=10)

    # -------------------------------------------------------------- strategies

    def strategies(self, now: datetime | None = None, limit: int = 20, cfg=None) -> dict[str, Any]:
        """有效/历史 StrategyPlan（账户过滤，真实计划 history）；缺历史如实说明。"""
        from ..reports.daily import public_plan
        now = now or datetime.now(timezone.utc)
        limit = max(1, min(int(limit), 100))
        with self._connect() as conn:
            if conn is None:
                return self._envelope(source='strategy_plan_history', data=None,
                                      status='unavailable', missing=['本地数据库不存在'])
            if 'strategy_plan_history' not in self._tables(conn):
                return self._envelope(source='strategy_plan_history', data={'plans': []},
                                      status='ok', missing=['尚无策略计划历史'], as_of=now)
            rows = conn.execute(
                'SELECT payload_json, available_at FROM strategy_plan_history WHERE account_id=? '
                'AND julianday(available_at)<=julianday(?) ORDER BY julianday(available_at) DESC LIMIT ?', (self.account_id, _iso(now), limit)).fetchall()
        plans = []
        with self._connect() as conn:
            names = self._tables(conn)
            for r in rows:
                try:
                    plan = json.loads(r['payload_json'])
                    item = public_plan(plan)
                    iid = item.get('instrument_id')
                    def context(at):
                        if 'context_snapshots' not in names or at is None: return None
                        row = conn.execute("SELECT payload_json FROM context_snapshots WHERE instrument_id=? AND json_extract(payload_json,'$.portfolio.account_id')=? AND julianday(as_of)<=julianday(?) ORDER BY julianday(as_of) DESC LIMIT 1",(iid,self.account_id,_iso(at))).fetchone()
                        return json.loads(row[0]) if row else None
                    original = context(_parse(item.get('created_at')))
                    latest = context(now)
                    features = (original or {}).get('features',{}).get('values',{})
                    item['condition_as_of'] = (original or {}).get('as_of')
                    item['condition_source'] = '生成前最近留存Context；不冒充确证模型输入'
                    from ..domain.strategy import Condition
                    def condition(c, values):
                        try:
                            parsed = Condition.model_validate(c)
                            actual = values.get(parsed.field)
                            matched = parsed.matches(values) if actual is not None else None
                        except (ValueError, ArithmeticError, TypeError):
                            actual = None; matched = None
                        return {**c, 'actual': actual, 'matched': matched}
                    for key in ('entry_conditions','exit_conditions','invalidation_conditions'):
                        item[key] = [condition(c,features) for c in item.get(key,[]) if isinstance(c,dict)]
                    status = '状态未知'
                    until = _parse(item.get('valid_until'))
                    if until and now >= until: status = '已过期'
                    elif until and 'coalesce_state' in names:
                        cache = conn.execute('SELECT meta_json FROM coalesce_state WHERE key=?',('strategy:'+self.account_id+':'+iid,)).fetchone()
                        meta = json.loads(cache[0]) if cache else {}
                        current = meta.get('plan') or {}
                        if current and current.get('plan_id') != item.get('plan_id'): status = '已替换'
                        elif current.get('plan_id') == item.get('plan_id') and cfg is not None:
                            from ..domain.strategy import StrategyPlan, open_without_close_violation
                            from ..agents.fast_prompts import STRATEGY_PROMPT_VERSION
                            from ..market.profiles import get_profile
                            candidate = StrategyPlan.model_validate(current)
                            fingerprint = {k:str(v) for k,v in cfg.t0.__dict__.items()}
                            if meta.get('config') != fingerprint or candidate.prompt_version != STRATEGY_PROMPT_VERSION or open_without_close_violation(candidate):
                                status = '已失效'
                            elif not candidate.valid_at(now,iid,get_profile(iid).profile_id): status = '已失效'
                            elif latest and _parse(latest.get('as_of')) and 0 <= (now-_parse(latest['as_of'])).total_seconds() <= 90:
                                values = latest.get('features',{}).get('values',{})
                                checks = [condition(c.model_dump(mode='json'),values)['matched'] for c in candidate.invalidation_conditions]
                                status = '已失效' if any(v is True for v in checks) else '状态未知' if any(v is None for v in checks) else '有效'
                    item['display_status'] = status
                    plans.append(item)
                except (ValueError, TypeError, KeyError):
                    continue
        missing = [] if plans else ['配置账户尚无策略计划记录']
        return self._envelope(source='strategy_plan_history', data={'plans': plans},
                              status='ok' if plans else 'ok', missing=missing,
                              as_of=_parse(rows[0]['available_at']) if rows else now,
                              stale_after=None)

    # -------------------------------------------------------------- activity

    def activity(self, now: datetime | None = None, kind: str = 'fill',
                 instrument_id: str | None = None, day: str | None = None,
                 limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        """决策/订单/成交/资金变动 分页只读流。cursor 为不透明游标。"""
        now = now or datetime.now(timezone.utc)
        limit = max(1, min(int(limit), 200))
        kinds = {'decision', 'order', 'fill', 'cash'}
        if kind not in kinds:
            return self._envelope(source='activity', data=None, status='error',
                                  missing=[f'未知类型 {kind}，可用：decision/order/fill/cash'])
        bounds = None
        if day:
            try:
                bounds = _shanghai_day_bounds(date.fromisoformat(day))
            except ValueError:
                return self._envelope(source='activity', data=None, status='error', missing=['日期格式无效'])
        cur = None
        if cursor:
            try:
                cur = json.loads(cursor)
                if cur['kind'] != kind or cur['account'] != self.account_id:
                    raise ValueError('cursor scope mismatch')
                int(cur['rid']); float(cur['time'])
            except (ValueError, TypeError, KeyError):
                return self._envelope(source='activity', data=None, status='error', missing=['游标无效'])
        rows = []
        with self._connect() as conn:
            if conn is None:
                return self._envelope(source='activity', data=None, status='unavailable', missing=['本地数据库不存在'])
            names = self._tables(conn)
            specs = {
                'decision': ('intents i', 'i', "i.decision_id || ':' || i.intent_version", "json_extract(i.payload_json,'$.account_id')", "json_extract(i.payload_json,'$.created_at')", 'i.instrument_id'),
                'order': ('orders i', 'i', 'i.order_id', 'i.account_id', 'i.created_at', 'i.instrument_id'),
                'fill': ('fills i JOIN orders o ON o.order_id=i.order_id', 'i', 'i.fill_id', 'o.account_id', 'i.executed_at', 'o.instrument_id'),
                'cash': ('account_events i', 'i', 'i.seq', 'i.account_id', 'i.effective_at', 'i.instrument_id'),
            }
            needed = {'decision': {'intents'}, 'order': {'orders'}, 'fill': {'fills','orders'}, 'cash': {'account_events'}}
            if needed[kind] <= names:
                table, alias, identity, account, clock, instrument = specs[kind]
                key = f'COALESCE(julianday({clock}),0)'
                extra = ', o.instrument_id AS fill_instrument, o.side AS fill_side' if kind == 'fill' else ''
                sql = f'SELECT i.payload_json, i.rowid AS rid, {identity} AS identity_value, {key} AS clock_value{extra} FROM {table} WHERE {account}=?'
                args = [self.account_id]
                if instrument_id:
                    sql += f' AND {instrument}=?'; args.append(instrument_id)
                if bounds:
                    sql += f' AND julianday({clock})>=julianday(?) AND julianday({clock})<julianday(?)'
                    args.extend([_iso(bounds[0]), _iso(bounds[1])])
                sql += f' AND {key}<=julianday(?)'; args.append(_iso(now))
                if cur:
                    sql += f' AND ({key}<? OR ({key}=? AND i.rowid<?))'
                    args.extend([float(cur['time']), float(cur['time']), int(cur['rid'])])
                sql += f' ORDER BY {key} DESC, i.rowid DESC LIMIT ?'; args.append(limit+1)
                for r in conn.execute(sql,args):
                    item = self._item(r['payload_json'], str(r['identity_value']))
                    if kind == 'fill':
                        item.update(instrument_id=r['fill_instrument'], side=r['fill_side'])
                    item['_cursor'] = {'kind':kind, 'account':self.account_id, 'rid':r['rid'], 'time':r['clock_value']}
                    rows.append(item)
        has_more = len(rows)>limit
        rows = rows[:limit]
        next_cursor = json.dumps(rows[-1]['_cursor']) if rows and has_more else None
        for item in rows:
            item.pop('_cursor',None)
        return self._envelope(source='activity:'+kind, data={'kind':kind, 'items':rows, 'next_cursor':next_cursor, 'has_more':has_more}, as_of=now)

    @staticmethod
    def _public(payload: dict[str, Any]) -> dict[str, Any]:
        """字段白名单脱敏：只暴露展示所需键，不带原始供应商响应。"""
        keep = ('decision_id', 'intent_version', 'order_id', 'client_order_id', 'fill_id',
                'instrument_id', 'account_id', 'action', 'side', 'status', 'quantity',
                'filled_quantity', 'price', 'limit_price', 'avg_fill_price', 'fee',
                'created_at', 'executed_at', 'valid_until', 'reason', 'trace_id', 'kind',
                'amount', 'effective_at', 'seq', 'intent_id', 'plan_id', 'context_id', 'verdict', 'reasons', 'reason_codes')
        return {k: payload[k] for k in keep if k in payload}

    def _item(self, payload_json: str, rid: Any) -> dict[str, Any]:
        try:
            payload = json.loads(payload_json)
        except (ValueError, TypeError):
            return {'id': str(rid), 'raw_available': False}
        item = self._public(payload if isinstance(payload, dict) else {})
        item['id'] = str(rid)
        return item

    def activity_detail(self, now: datetime | None = None, kind: str = 'fill',
                        item_id: str = '') -> dict[str, Any]:
        """按 id 详情；先验归属再返回，跨账户 id 一律 404 语义（unavailable）。"""
        now = now or datetime.now(timezone.utc)
        if kind not in {'decision', 'order', 'fill', 'cash'} or not item_id:
            return self._envelope(source='activity_detail', data=None, status='error',
                                  missing=['类型或 id 无效'])
        with self._connect() as conn:
            if conn is None:
                return self._envelope(source='activity_detail', data=None, status='unavailable',
                                      missing=['本地数据库不存在'])
            names = self._tables(conn)
            row = None
            if kind == 'decision' and 'intents' in names:
                row = conn.execute(
                    "SELECT payload_json FROM intents WHERE decision_id=? AND json_extract(payload_json,'$.account_id')=? ORDER BY intent_version DESC LIMIT 1",
                    (item_id, self.account_id)).fetchone()
                if row is None and ':' in item_id:
                    decision, _, version = item_id.rpartition(':')
                    if version.isdigit():
                        row=conn.execute("SELECT payload_json FROM intents WHERE decision_id=? AND intent_version=? AND json_extract(payload_json,'$.account_id')=?",
                                         (decision,int(version),self.account_id)).fetchone()
            elif kind == 'order' and 'orders' in names:
                row = conn.execute(
                    'SELECT payload_json FROM orders WHERE order_id=? AND account_id=?',
                    (item_id, self.account_id)).fetchone()
            elif kind == 'fill' and 'fills' in names and 'orders' in names:
                row = conn.execute(
                    'SELECT f.payload_json AS payload_json FROM fills f JOIN orders o ON '
                    'o.order_id=f.order_id WHERE f.fill_id=? AND o.account_id=?',
                    (item_id, self.account_id)).fetchone()
            elif kind == 'cash' and 'account_events' in names:
                row = conn.execute(
                    'SELECT payload_json FROM account_events WHERE account_id=? AND seq=?',
                    (self.account_id, item_id)).fetchone()
        if row is None:
            return self._envelope(source='activity_detail', data=None, status='unavailable',
                                  missing=['记录不存在或不属于当前账户'])
        item = self._item(row['payload_json'], item_id)
        if kind == 'fill':
            with self._connect() as conn:
                order = conn.execute('SELECT instrument_id,side FROM orders WHERE order_id=? AND account_id=?', (item.get('order_id'), self.account_id)).fetchone()
                if order:
                    item.update(instrument_id=order['instrument_id'], side=order['side'])
        item['timeline'] = self._timeline(kind, json.loads(row['payload_json']))
        item['timeline_note'] = '仅列已保存且可关联的节点；缺失节点不表示通过；模型原始响应不展示'
        return self._envelope(source='activity_detail', data=item, as_of=now)

    def _timeline(self, kind, payload):
        nodes=[]
        with self._connect() as conn:
            names=self._tables(conn)
            intent=None
            order=None
            if kind in ('order','fill') and 'orders' in names:
                row=conn.execute('SELECT payload_json FROM orders WHERE order_id=? AND account_id=?',(payload.get('order_id'),self.account_id)).fetchone()
                order=json.loads(row[0]) if row else None
            if kind=='decision': intent=payload
            elif order and 'intents' in names:
                row=conn.execute("SELECT payload_json FROM intents WHERE json_extract(payload_json,'$.id')=? AND json_extract(payload_json,'$.account_id')=?",(order.get('intent_id'),self.account_id)).fetchone()
                intent=json.loads(row[0]) if row else None
            def add(label, record, time_key='created_at'):
                nodes.append({'label':label,'time':record.get(time_key),'record':self._public(record)})
            if intent:
                add('交易意图',intent)
                if intent.get('context_id') and 'context_snapshots' in names:
                    context = conn.execute("SELECT payload_json FROM context_snapshots WHERE context_id=? AND json_extract(payload_json,'$.portfolio.account_id')=?", (intent['context_id'], self.account_id)).fetchone()
                    if context:
                        snap=json.loads(context[0])
                        nodes.append({'label':'行情与账户快照','time':snap.get('as_of'),'record':{'context_id':intent['context_id'], 'instrument_id':snap.get('instrument_id')}})
                if intent.get('thesis_id') and 'strategy_plan_history' in names:
                    plan=conn.execute('SELECT payload_json FROM strategy_plan_history WHERE account_id=? AND plan_id=?',(self.account_id,intent['thesis_id'])).fetchone()
                    if plan: add('策略计划',json.loads(plan[0]),'available_at')
                trace=intent.get('trace_id')
                if trace and 'model_calls' in names:
                    for row in conn.execute('SELECT role,status,created_at FROM model_calls WHERE trace_id=? ORDER BY created_at',(trace,)):
                        nodes.append({'label':'模型调用','time':row['created_at'],'record':{'role':row['role'],'status':row['status']}})
                for table,key,label in [('risk_reviews','decision_id','风险复核'),('guard_results','intent_id','程序硬风控')]:
                    if table in names:
                        ident=intent.get('decision_id') if key=='decision_id' else intent.get('id')
                        for row in conn.execute(f'SELECT payload_json FROM {table} WHERE {key}=?',(ident,)):
                            add(label,json.loads(row[0]))
                if 'orders' in names:
                    orders=conn.execute('SELECT payload_json FROM orders WHERE account_id=? AND intent_id=?',(self.account_id,intent.get('id'))).fetchall()
                else: orders=[]
            else: orders=[]
            if order and not orders: orders=[{'payload_json':json.dumps(order)}]
            for row in orders:
                record=json.loads(row['payload_json']); add('模拟订单',record)
                if 'fills' in names:
                    for fill in conn.execute('SELECT payload_json FROM fills WHERE order_id=?',(record.get('order_id'),)):
                        add('实际模拟成交',json.loads(fill[0]),'executed_at')
            if kind=='cash': add('账本资金事件',payload,'effective_at')
        nodes.sort(key=lambda n: _parse(n['time']) or datetime.min.replace(tzinfo=timezone.utc))
        return nodes

    # -------------------------------------------------------------- runtime

    @staticmethod
    def _heartbeat_age(payload, now):
        at = _parse(payload.get('heartbeat'))
        return max(0, int((now-at).total_seconds())) if at and at <= now else None

    @classmethod
    def _process_active(cls, payload, now):
        from ..execution.auto_paper import _pid_alive
        age = cls._heartbeat_age(payload, now)
        try:
            return age is not None and age < 30 and _pid_alive(int(payload.get('pid')))
        except (ValueError, TypeError, OSError):
            return False

    def runtime(self, now: datetime | None = None) -> dict[str, Any]:
        """只读运行状态；controls_available 恒为 False（观察服务不做控制）。"""
        now = now or datetime.now(timezone.utc)
        missing: list[str] = []
        state = self._auto_state()
        supervisor = None
        if state:
            payload = state[0]
            supervisor = {k: payload.get(k) for k in
                          ('phase', 'halted', 'message', 'heartbeat', 'retry_at',
                           'health_status', 'intraday_health', 'pid', 'child_pid')}
            supervisor['state_age_seconds'] = self._heartbeat_age(payload, now)
            supervisor['active'] = self._process_active(payload, now)
            if not supervisor['active']: missing.append('监督进程心跳过期或进程不存在')
        else:
            missing.append('自动监督状态文件不存在（自动模拟未运行）')
        intraday = None
        got = self._read_json(self._intraday_file)
        if got and isinstance(got[0], dict) and got[0].get('account_id') == self.account_id:
            intraday = {k: got[0].get(k) for k in
                        ('date', 'health_status', 'blockers', 'open_positions_count',
                         'open_orders_count', 'budget_meta')}
        else:
            missing.append('今日盘中健康文件不存在或不属于当前账户')
        observer = None
        observer_file = self.db_path.parent / 'observer-state.json'
        got_obs = self._read_json(observer_file)
        if got_obs and isinstance(got_obs[0], dict) and got_obs[0].get('account_id') == self.account_id:
            payload_obs = got_obs[0]
            observer = {'running': bool(payload_obs.get('running')) and self._process_active(payload_obs, now),
                        'pid': payload_obs.get('pid'),
                        'heartbeat_age_seconds': self._heartbeat_age(payload_obs, now)}
        else:
            missing.append('观察服务状态文件不存在（observer 未运行）')
        kill_switch = None
        with self._connect() as conn:
            if conn is not None and 'kill_switch' in self._tables(conn):
                r = conn.execute('SELECT engaged, reason, updated_at FROM kill_switch WHERE id=1').fetchone()
                if r:
                    kill_switch = {'engaged': bool(r['engaged']), 'reason': r['reason'],
                                   'updated_at': r['updated_at']}
        today_report, _, _ = self.today_report_block(now)
        budget = None
        if today_report:
            model = today_report.get('model_cost_settled')
            budget = {'settled_today': model, 'pending_today': today_report.get('model_cost_pending')}
        else:
            missing.append('今日日报缺失，预算已用暂不可计算')
        data = {
            'supervisor': supervisor,
            'intraday_health': intraday,
            'observer': observer,
            'kill_switch': kill_switch,
            'model_budget_today': budget,
            'capabilities': {'controls_available': False, 'observer_read_only': True},
        }
        return self._envelope(source='auto_paper_state+intraday_health+kill_switch+daily_report',
                              data=data, status='partial' if missing else 'ok',
                              missing=missing, as_of=now, stale_after=10)

    def alerts(self, now: datetime | None = None) -> dict[str, Any]:
        """页面内告警条目（type+instrument+reason 去重键由前端继续聚合）。"""
        now = now or datetime.now(timezone.utc)
        items: list[dict[str, Any]] = []
        runtime = self.runtime(now)
        data = runtime.get('data') or {}
        supervisor = data.get('supervisor') or {}
        if supervisor.get('halted'):
            items.append({'type': 'trading_halted', 'instrument_id': None,
                          'reason': supervisor.get('message') or '自动模拟处于锁定状态，待核对',
                          'severity': 'danger'})
        kill = data.get('kill_switch') or {}
        if kill.get('engaged'):
            items.append({'type': 'kill_switch', 'instrument_id': None,
                          'reason': kill.get('reason') or '手动停止开关已生效', 'severity': 'danger'})
        intraday = data.get('intraday_health') or {}
        status = intraday.get('health_status')
        if status and status not in ('healthy', 'ok', None):
            items.append({'type': 'intraday_health', 'instrument_id': None,
                          'reason': f'盘中健康状态：{status}', 'severity': 'warn'})
        for blocker in (intraday.get('blockers') or []):
            text = blocker if isinstance(blocker, str) else json.dumps(blocker, ensure_ascii=False)
            items.append({'type': 'intraday_blocker', 'instrument_id': None,
                          'reason': text[:160], 'severity': 'warn'})
        if runtime.get('data_status') == 'partial':
            for reason in runtime.get('missing_reasons') or []:
                items.append({'type': 'data_gap', 'instrument_id': None,
                              'reason': reason[:160], 'severity': 'warn'})
        return self._envelope(source='runtime_derived', data={'alerts': items},
                              status='ok', as_of=now, stale_after=10)
