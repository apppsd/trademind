"""Bounded credential-free startup evidence; never constructs model providers."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config/t0-fast.local.toml')
    parser.add_argument('--output',default='data/runtime/fast-startup-check.json')
    parser.add_argument('--timeout',type=int,default=45)
    args=parser.parse_args()
    from trademind.config import load_config
    from trademind.market.calendar import TradingCalendar
    from trademind.market.router import MINUTE_SOURCE_PROTOCOL_STATUS
    cfg=load_config(args.config,load_credentials=False)
    rates=(cfg.t0.model_input_cny_per_million,cfg.t0.model_output_cny_per_million,
           cfg.t0.jev_input_cny_per_million,cfg.t0.jev_output_cny_per_million)
    valid_prices = (all(v is not None and Decimal(str(v)).is_finite() for v in rates)
                    and all(Decimal(str(v)) > 0 for v in rates[:3])
                    and Decimal(str(rates[3])) >= 0)
    pref=list(cfg.data.provider_pref)
    # Three distinct states, never collapsed into one "ready":
    # 1) configuration exists; 2) source protocol qualified (engineering
    # evidence only); 3) live intraday quality verified by the probe below.
    protocol={name: MINUTE_SOURCE_PROTOCOL_STATUS.get(name,'unknown_protocol_status') for name in pref}
    report={'at':datetime.now(timezone.utc).isoformat(),'config':args.config,'profile':cfg.t0.profile,
            'mode':cfg.mode,'account_id':cfg.account_id,'initial_cash':str(cfg.t0.initial_cash),
            'universe':cfg.universe,'prices_present':[v is not None for v in rates],
            'session_open':TradingCalendar.load().is_session_open(datetime.now(timezone.utc)),
            'credentials_loaded':False,'model_calls':0,'orders':0,'persistent_service_started':False,
            'status':'not_started',
            'source_config':{'present':bool(pref),'pref':pref},
            'source_protocol':protocol,
            'intraday_quality':{'verified':False,'detail':'probe not run yet'}}
    code="""import requests,sys
from trademind.runner import main
original=requests.Session.request
def bounded(self,method,url,**kw):
 kw['timeout']=min(float(kw.get('timeout') or 8),8)
 return original(self,method,url,**kw)
requests.Session.request=bounded
raise SystemExit(main(['--config',sys.argv[1],'t0-data']))
"""
    try:
        proc=subprocess.run([sys.executable,'-c',code,args.config],capture_output=True,text=True,
                            encoding='utf-8',errors='replace',timeout=max(1,min(args.timeout,60)))
        report['data_exit_code']=proc.returncode
        report['data']=json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        report['data']={'status':'bounded_timeout'}
    except (ValueError,OSError):
        report['data']={'status':'probe_output_unavailable'}
    intraday_ok=report['data'].get('status')=='success'
    report['intraday_quality']={'verified':intraday_ok,
                                'detail':'all instruments fresh and quality-ok' if intraday_ok
                                else f"probe status {report['data'].get('status')!r}"}
    report['blockers']=[]
    if not valid_prices:report['blockers'].append('verified_prices_invalid')
    if cfg.t0.profile != 'jev_fast_decision' or not cfg.t0.enabled or cfg.mode != 'paper':
        report['blockers'].append('fast_paper_configuration_required')
    if not report['session_open']:report['blockers'].append('not_continuous_session')
    if not pref:report['blockers'].append('no_minute_source_configured')
    if 'fixture' in pref:report['blockers'].append('fixture_in_paper_minute_pref')
    if any(status!='protocol_qualified' for status in protocol.values()):
        report['blockers'].append('minute_source_protocol_unverified')
    if not intraday_ok:report['blockers'].append('fresh_minute_data_unverified')
    report['blockers'].append('real_models_not_probed_in_this_check')
    report['status']='blocked'
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 2

if __name__=='__main__':raise SystemExit(main())
