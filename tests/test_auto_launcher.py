import importlib.util
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace


def module(monkeypatch):
    scripts=Path(__file__).parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec=importlib.util.spec_from_file_location('auto_launcher',scripts/'run_auto.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    monkeypatch.setattr(m,'ROOT',PureWindowsPath('F:/Project/TradeMind'))
    monkeypatch.setattr(m,'getproxies',lambda:{'all':'http://localhost:8123'})
    return m


def test_auto_refuses_manual_writer(monkeypatch):
    m=module(monkeypatch)
    calls=[]
    monkeypatch.setattr(m.subprocess,'run',lambda cmd,**kwargs:calls.append(cmd) or SimpleNamespace(returncode=0,stdout='paper-id'))
    assert m.main(['start'])==2 and len(calls)==1


def test_auto_start_stops_dashboard_and_forwards_proxy_only_in_environment(monkeypatch):
    m=module(monkeypatch)
    calls=[]
    monkeypatch.setattr(m.subprocess,'run',lambda cmd,**kwargs:calls.append((cmd,kwargs)) or SimpleNamespace(returncode=0,stdout=''))
    assert m.main(['start'])==0
    assert calls[-2][0][-2:]==['stop','workbench']
    assert calls[-1][0][-4:]==['up','-d','--no-deps','auto']
    assert calls[-1][1]['env']['HTTPS_PROXY']=='http://host.docker.internal:8123'
    assert all('http://' not in v for cmd,_ in calls for v in cmd)
