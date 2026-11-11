"""Daily CNY paper reports from a read-only SQLite snapshot; no market/model calls."""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from uuid import uuid4
import os

from ..market.base import BEIJING
from .locale import (ACTIONS, STATUS, ROLES, PRE_DECISION_BUCKETS, BLOCKER_CLASS_TEXT,
                     EXCLUSION_CLASSES, FIELDS, OPERATORS, explanation, condition_text, time_text)


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite financial value')
    return result


def timestamp(value):
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError('timezone required')
    return at.astimezone(BEIJING)


def public_plan(plan):
    from ..execution.paper_run import sanitize_evidence
    keys = ('plan_id', 'instrument_id', 'model', 'prompt_version', 'created_at', 'available_at',
            'valid_until', 'trend', 'risk', 'critic', 'reason', 'allowed_actions',
            'entry_conditions', 'exit_conditions', 'invalidation_conditions', 'holding_horizon_seconds')
    def clean(value):
        if isinstance(value, str):
            return sanitize_evidence(value, 3000)
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value
    return {k: clean(plan[k]) for k in keys if k in plan}


def read_snapshot(database, account, window=None):
    """Read-only snapshot. With ``window=(start, stop)`` the model-call,
    context, intent and plan reads are bounded to rows near that Shanghai
    report day (TASK-20261013-16). Account events stay unbounded on purpose:
    opening books are replayed from the account's full event history and no
    verified boundary snapshot exists to truncate against."""
    path = Path(database).resolve()
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table, query, args=()):
            return [dict(r) for r in conn.execute(query, args)] if table in names else []
        def bounds(column, days_back, days_fwd):
            if window is None:
                return '', ()
            start, stop = window
            lo = (start - timedelta(days=days_back)).astimezone(timezone.utc).isoformat()
            hi = (stop + timedelta(days=days_fwd)).astimezone(timezone.utc).isoformat()
            return f' AND {column}>=? AND {column}<=?', (lo, hi)
        # ±2-day margins absorb every stored offset form ('Z', '+08:00', …);
        # the exact Shanghai-day filtering stays in generate_report below.
        # Contexts keep a 7-day lookback so a position's latest-mark display
        # still resolves after a weekend-plus of no snapshots.
        calls_w = bounds('created_at', 2, 2)
        ctx_w = bounds('as_of', 7, 2)
        intent_w = bounds('created_at', 2, 2)
        if window is not None and 'created_at' not in {
                r[1] for r in conn.execute('PRAGMA table_info(intents)')}:
            intent_w = ('', ())  # legacy v1 file never migrated: read unbounded
        plan_w = bounds('available_at', 2, 2)
        audit_w = bounds('at', 2, 2)
        return {
            'events': rows('account_events', 'SELECT payload_json FROM account_events WHERE account_id=? ORDER BY seq', (account,)),
            'calls': rows('model_calls', 'SELECT payload_json FROM model_calls WHERE 1=1'+calls_w[0], calls_w[1]),
            'budgets': rows('coalesce_state', 'SELECT key,meta_json FROM coalesce_state WHERE key=? OR substr(key,1,length(?))=?',
                            ('model-budget:'+account, 'model-budget:'+account+':history:', 'model-budget:'+account+':history:')),
            'contexts': rows('context_snapshots', 'SELECT payload_json FROM context_snapshots WHERE 1=1'+ctx_w[0], ctx_w[1]),
            'plans': rows('strategy_plan_history',
                          'SELECT payload_json FROM strategy_plan_history WHERE account_id=?'+plan_w[0]+' ORDER BY available_at',
                          (account,)+plan_w[1]),
            'cached': rows('coalesce_state', 'SELECT meta_json FROM coalesce_state WHERE substr(key,1,length(?))=?',
                           ('strategy-plan:'+account+':', 'strategy-plan:'+account+':')),
            'intents': rows('intents', 'SELECT payload_json FROM intents WHERE 1=1'+intent_w[0], intent_w[1]),
            'audit': rows('control_audit', "SELECT actor,action,target,at,payload_json FROM control_audit WHERE actor='decision'"+audit_w[0], audit_w[1]),
            'account_count': conn.execute('SELECT count(*) FROM account_state').fetchone()[0] if 'account_state' in names else 0,
        }
    finally:
        conn.close()


def generate_report(database, account, day=None, now=None, t0_cfg=None):
    import re
    from .daily_sections import (
        attach_plan_text, collect_decisions, collect_plans, decision_distribution,
        model_cost, pre_decision_rounds, replay_book, report_clock, value_book,
    )
    now, day, start, stop = report_clock(day, now)
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}', account) or account in ('.', '..'):
        raise ValueError('unsafe account ID')
    data = read_snapshot(database, account, window=(start, stop))
    events = [json.loads(r['payload_json']) for r in data['events']]
    book = replay_book(events, start, stop, day, now)
    warnings = book['warnings']
    contexts = [json.loads(r['payload_json']) for r in data['contexts']]
    beginning = value_book(book['opening'], start, contexts, day, book['events'], True)
    ending = value_book(book['state'], stop, contexts, day, book['events'])
    if not beginning['valuation_complete'] or not ending['valuation_complete']:
        warnings.append('缺少期初持仓估值或新鲜期末行情；总资产/当日总盈亏未确认，不能用成本价替代。')
    profit = (decimal(ending['equity']) - decimal(beginning['equity']) - book['funding']
              if beginning['equity'] is not None and ending['equity'] is not None else None)
    model, settled, pending, scoped = model_cost(data, data['calls'], start, stop, day, now, warnings)
    net = profit - settled if profit is not None and settled is not None else None
    if pending is None or pending > 0:
        warnings.append('模型费用仍有未知预留，扣模型费后的净盈亏仅为暂估。')
    plans = collect_plans(data, start, stop, day, now)
    decisions, action_gate_coverage_note = collect_decisions(
        data, contexts, account, start, stop, day, now, scoped)
    warnings.append('完整策略历史仅从新版接入后保留；旧缓存无法恢复被覆盖的计划。')
    attach_plan_text(plans, database)
    from ..execution.paper_run import read_intraday_health
    # t0_cfg (review R3): without the effective config the window interval and
    # remaining seconds stay unknown even when a claim row exists. Callers
    # without a config (plain generate_report) keep the unknown=None behavior.
    intraday_health = read_intraday_health(
        database, account, now=stop, session_open=(day == now.date() and 9 <= stop.hour < 15), t0_cfg=t0_cfg)
    return {'schema_version': 1, 'account_id': account, 'date': day.isoformat(), 'timezone': 'Asia/Shanghai',
            'generated_at': now.isoformat(), 'as_of': stop.isoformat(),
            'period_status': 'intraday' if day == now.date() and now.hour < 15 else 'end_of_day_snapshot',
            'intraday_health': intraday_health,
            'opening': beginning, 'ending': ending, 'net_external_cash_flow': str(book['funding']),
            'opening_includes_initial_deposit': book['initial_id'] is not None,
            'trading_pnl': str(profit) if profit is not None else None,
            'realized_pnl': str(book['realized']), 'trading_fees': str(book['fees']),
            'net_after_settled_model_estimate': str(net) if net is not None else None,
            'net_after_model_reservation_bound': str(net - pending) if net is not None and pending is not None else None,
            'model_cost': model, 'trades': book['trades'], 'strategies': plans, 'decisions': decisions,
            'decision_distribution': decision_distribution(decisions, stop),
            'pre_decision_rounds': pre_decision_rounds(data, account, start, stop, day, now),
            'action_gate_coverage_note': action_gate_coverage_note,
            'warnings': warnings,
            'scope': 'CNY_SQLite_paper_account_not_multiasset_inmemory_ledger'}


def render_markdown(report):
    def value(v):
        return '未确认' if v is None else escape(str(time_text(v))).replace('|', '&#124;').replace('\n', ' ')
    begin, end, model = report['opening'], report['ending'], report['model_cost']
    ih = report.get('intraday_health') or {}
    lines = [f"# {report['date']} 模拟交易日报", '', f"账户：{value(report['account_id'])}（上海日期）",
             f"统计截至：{value(report['as_of'])}；{'盘中暂报' if report['period_status']=='intraday' else '日终快照'}",
             f"盘中健康状态：{value(ih.get('health_status', '未评估'))}（{value(ih.get('status_message_zh', '无'))}）"]
    # Additive round/window evidence (rereview R2/R3): the last round's
    # blocker class and reason subtype, plus the persisted global-window
    # claim facts. All read-only; absent values stay absent.
    if ih.get('last_decision_reason'):
        blocker_txt = BLOCKER_CLASS_TEXT.get(ih.get('last_blocker_class'), ih.get('last_blocker_class') or '未知')
        lines.append(f"最近一轮：{value(blocker_txt)}——{value(explanation(ih.get('last_decision_reason')))}")
    window_lines = []
    if ih.get('fast_eval_seconds') is not None:
        window_lines.append(f"评估窗口间隔：{value(ih['fast_eval_seconds'])} 秒（key：{value(ih.get('global_cooldown_key'))}，scope：{value(ih.get('global_cooldown_scope'))}）")
    if ih.get('global_cooldown_last_claim_at'):
        claim = ih.get('global_cooldown_claim') or {}
        window_lines.append(f"最近认领：{value(time_text(ih['global_cooldown_last_claim_at']))}；角色：{value(ih.get('global_cooldown_claim_role') or '未知')}；"
                            f"trace：{value(claim.get('trace_id') or '未知')}")
        window_lines.append(f"下次可请求：{value(time_text(ih.get('global_cooldown_next_eligible_at')))}；剩余：{value(ih.get('global_cooldown_seconds_remaining'))} 秒")
    if ih.get('last_model_path_claimed') is not None:
        window_lines.append(f"最近一轮是否认领窗口：{'是' if ih['last_model_path_claimed'] else '否'}")
    if window_lines:
        lines += ['', '## 全局模型窗口（只读事实）', ''] + ['- '+l for l in window_lines]
    lines += ['', '| 项目 | 金额（元） |', '|---|---|']
    rows = [('开始现金', begin['cash']), ('开始总资产', begin['equity']),
            ('交易后现金', end['cash']), ('冻结资金（已包含在现金中）', end['reserved_cash']),
            ('可用现金', end['available_cash']), ('交易后总资产', end['equity']),
            ('净入金/出金（不算收益）', report['net_external_cash_flow']),
            ('今天交易赚/亏（已含交易手续费）', report['trading_pnl']),
            ('今天已实现收益（可能含历史持仓收益）', report['realized_pnl']),
            ('今天交易手续费（不再重复扣）', report['trading_fees']),
            ('模型已结算估算费用', model['settled_estimate']),
            ('模型未知费用预留', model['pending_reservation']),
            ('扣已结算模型估值后净盈亏', report['net_after_settled_model_estimate'])]
    lines += [f'| {name} | {value(amount)} |' for name, amount in rows]
    lines += ['', '## 买卖成交', '', '| 时间 | 标的 | 方向 | 单价 | 数量 | 成交金额 | 手续费 | 成交后现金 |',
              '|---|---|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ACTIONS.get(t.get(k),'其他') if k=='side' else t.get(k)) for k in ('time','instrument_id','side','price','quantity','notional','fee','cash_after_fill'))+' |' for t in report['trades']]
    if not report['trades']:
        lines += ['当天没有成交，不代表程序没有运行。']
    lines += ['', '## 模型调用', '', f"预算请求数：{value(model['requested_count'])}；审计行数：{model['audit_rows']}（累计尝试数：{value(model.get('audit_attempts', model['audit_rows']))}）；费用是配置估算，不是供应商账单。",
              '', '| 时间 | 模型 | 角色 | 状态 | 输入词元 | 输出词元 | 费用估值 |', '|---|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ROLES.get(c.get(k),'其他调用') if k=='role' else STATUS.get(c.get(k),'未确认') if k=='status' else c.get(k)) for k in ('created_at','model','role','status','input_tokens','output_tokens','cost_amount'))+' |' for c in model['calls']]
    lines += ['', '## 策略计划', '']
    for p in report['strategies']:
        lines += [f"### {value(p['instrument_id'])} / {value(p['plan_id'])}",
                  f"版本：{value(p.get('prompt_version'))}；可用时间：{value(p.get('available_at'))}",
                  f"允许动作：{value('、'.join(ACTIONS.get(a,'其他') for a in p.get('allowed_actions',[])))}"]
        lines += [f"- {label}：{value(p.get('display_zh',{}).get(key,explanation(p.get(key))))}" for key,label in
                  [('trend','趋势'),('risk','风险'),('critic','反证'),('reason','原因')]]
        lines += [f"- {label}：{value(condition_text(p.get(key,[])))}" for key,label in
                  [('entry_conditions','入场'),('exit_conditions','退出'),('invalidation_conditions','失效')]]
    if not report['strategies']:
        lines += ['未找到当天已留档的有效策略计划。']
    lines += ['', '## 已留档决策（不等于成交）', '', '| 时间 | 标的 | 动作 | 原因 | 提示版本 |', '|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(ACTIONS.get(d.get(k),'未确认') if k=='action' else explanation(d.get(k)) if k=='reason' else d.get(k)) for k in ('created_at','instrument_id','action','reason','prompt_id'))+' |' for d in report['decisions']]
    if not report['decisions']:
        lines += ['当天没有已留档的交易意图；预检拒绝或等待中的任务不一定产生意图记录。']
    gated = [d for d in report['decisions'] if d.get('action_gate')]
    if gated:
        lines += ['', '## 无可行动条件逐条件证据（附加，仅限本版本后留档的意图）', '']
        for d in gated:
            gate = d['action_gate']
            allowed_txt = '、'.join(ACTIONS.get(a, a) for a in gate.get('allowed_actions', []))
            excl = gate.get('exclusion') or {}
            excl_txt = '、'.join(a + '=' + EXCLUSION_CLASSES.get(c, c) for a, c in sorted(excl.items()))
            lines += [f"### {value(d.get('created_at'))} {value(d.get('instrument_id'))}（{value(gate.get('plan_id'))}）",
                      f"允许动作：{value(allowed_txt)}；排除：{value(excl_txt)}"]
            for key, label in (('entry_conditions', '入场'), ('exit_conditions', '退出')):
                rows = gate.get(key) or []
                if not rows:
                    lines += [f"- {label}条件：无"]
                    continue
                rows_txt = '；'.join(FIELDS.get(r['field'], '其他特征') + OPERATORS.get(r['operator'], '满足条件') + str(r['threshold'])
                                    for r in rows)
                lines += [f"- {label}条件：{value(rows_txt)}"]
                lines += ['', '| 特征 | 判断 | 阈值 | 实际值 | 满足 | 缺失 |', '|---|---|---|---|---|---|']
                lines += ['| '+' | '.join(value(
                        FIELDS.get(r.get(k), r.get(k)) if k == 'field' else
                        OPERATORS.get(r.get(k), r.get(k)) if k == 'operator' else
                        ('是' if r.get(k) else '否') if k in ('matched', 'missing') else r.get(k))
                    for k in ('field', 'operator', 'threshold', 'actual', 'matched', 'missing'))+' |' for r in rows]
    if report.get('action_gate_coverage_note'):
        lines += ['', '- '+value(report['action_gate_coverage_note'])]
    dist = report.get('decision_distribution') or {}
    lines += ['', '## 已留档决策原因分布', '']
    if dist.get('denominator') is not None:
        lines += [f"统计截至：{value(dist.get('cutoff'))}；分母：{value(dist.get('denominator'))} 条（去重规则：{value(dist.get('dedupe_rule'))}）；前置拒绝与异步等待轮次不在此分母内。"]
        lines += ['', '| 动作 | 轮数 |', '|---|---|']
        lines += [f"| {value(ACTIONS.get(k, k))} | {v} |" for k, v in sorted((dist.get('by_action') or {}).items())]
        lines += ['', '| 原因 | 轮数 |', '|---|---|']
        lines += [f"| {value(explanation(k))} | {v} |" for k, v in sorted((dist.get('by_reason') or {}).items())]
    pre = report.get('pre_decision_rounds') or {}
    lines += ['', '## 前置数据拒绝与异步等待轮次（独立分母，不计入已留档决策）', '']
    lines += [f"来源：{value(pre.get('source'))}；统计截至：{value(pre.get('cutoff'))}"]
    buckets = pre.get('buckets') or {}
    if buckets:
        lines += ['', '| 类别 | 轮数 | 最常见原因（次数） |', '|---|---|---|']
        for slug in sorted(buckets):
            bucket = buckets[slug] or {}
            reasons = bucket.get('reasons') or {}
            top = max(reasons.items(), key=lambda kv: kv[1]) if reasons else ('unknown', 0)
            lines += [f"| {value(PRE_DECISION_BUCKETS.get(slug, slug))} | {value(bucket.get('count'))} | {value(explanation(top[0]))}（{value(top[1])}） |"]
    else:
        lines += ['当日没有已留档的前置拒绝或异步等待轮次。']
    if pre.get('coverage_note'):
        lines += [f"- {value(pre['coverage_note'])}"]
    lines += ['', '## 持仓', '', '| 标的 | 数量 | 成本 | 最新价 | 市值 | 浮动盈亏 |', '|---|---|---|---|---|---|']
    lines += ['| '+' | '.join(value(p.get(k)) for k in ('instrument_id','quantity','avg_cost','last_price','market_value','unrealized_pnl'))+' |' for p in end['positions']]
    lines += ['', '## 口径与未确认内容', ''] + ['- '+value(w) for w in report['warnings']]
    return '\n'.join(lines)+'\n'


def write_report(report, directory):
    root = Path(directory)/report['account_id']
    root.mkdir(parents=True, exist_ok=True)
    base = root/report['date']
    markdown = render_markdown(report)
    sections, in_table = [], False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if line.startswith('|---'):
                continue
            if not in_table:
                sections.append('<table>')
                in_table = True
            sections.append('<tr>'+''.join('<td>'+cell.strip()+'</td>' for cell in line.strip('|').split('|'))+'</tr>')
            continue
        if in_table:
            sections.append('</table>')
            in_table = False
        if line.startswith('# '):
            sections.append('<h1>'+line[2:]+'</h1>')
        elif line.startswith('## '):
            sections.append('<h2>'+line[3:]+'</h2>')
        elif line.startswith('### '):
            sections.append('<h3>'+line[4:]+'</h3>')
        elif line:
            sections.append('<p>'+line+'</p>')
    if in_table:
        sections.append('</table>')
    # Every dynamic string has already been escaped by render_markdown.
    html = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TradeMind 每日交易报告</title><style>body{max-width:1200px;margin:24px auto;padding:24px;font:15px/1.7 sans-serif;background:#f6f8fa;color:#172033}h1{font-size:26px}h2{margin-top:32px;border-bottom:2px solid #d8e1ee}table{background:white;width:100%;border-collapse:collapse;display:block;overflow:auto}td{padding:10px;border:1px solid #d8e1ee;white-space:normal}tr:first-child{background:#e9f0fb;font-weight:bold}p{overflow-wrap:anywhere}</style>'+''.join(sections)+'</html>'
    for suffix, content in (('.json', json.dumps(report,ensure_ascii=False,indent=2)), ('.md', markdown), ('.html', html)):
        path = base.with_suffix(suffix)
        temp = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        try:
            temp.write_text(content,encoding='utf-8')
            os.replace(temp,path)
        finally:
            temp.unlink(missing_ok=True)
    return str(base.with_suffix('.html'))


def generate_for_config(cfg, day=None):
    if not cfg.storage.url.startswith('sqlite:///'):
        raise ValueError('SQLite report only')
    db = Path(cfg.storage.url.removeprefix('sqlite:///'))
    report = generate_report(db, cfg.account_id, day, t0_cfg=cfg.t0)
    output = write_report(report, db.parent/'daily-reports')
    return report, output

