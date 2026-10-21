import importlib.util
from pathlib import Path
from pathlib import PureWindowsPath

import pytest

spec = importlib.util.spec_from_file_location("container_run", Path(__file__).parents[1]/"scripts/run_container.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture(autouse=True)
def isolated_docker_inventory(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0,stdout=''))


def test_manual_paper_refuses_active_auto(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'getproxies', lambda: {'all':'http://localhost:8123'})
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0,stdout='auto-id'))
    monkeypatch.setattr(module, 'run', lambda *a: pytest.fail('no account writer launch'))
    assert module.main(['paper'])==2


def test_proxy_only_in_child_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:8123")
    env = module.container_environment({"http": "http://localhost:8123", "https": "http://localhost:8123"})
    assert env["HTTPS_PROXY"] == "http://host.docker.internal:8123"
    assert module.os.environ["HTTPS_PROXY"] == "http://localhost:8123"


@pytest.mark.parametrize("proxy", ["http://user:password@localhost:8123", "http://example.com:8123",
                                   "socks5://localhost:8123", "http://localhost", "http://localhost:8123/private"])
def test_unsupported_proxy_refused(proxy):
    with pytest.raises(ValueError): module.container_environment({"http": proxy, "https": proxy})


def test_paper_recovers_workbench_and_no_url_in_command(monkeypatch):
    monkeypatch.setattr(module, "getproxies", lambda: {"http": "http://localhost:8123", "https": "http://localhost:8123"})
    calls = []
    def run(command, environment):
        calls.append(command)
        return 1 if "run" in command else 0
    monkeypatch.setattr(module, "run", run)
    assert module.main(["paper", "--steps", "1"]) == 1
    assert calls[0][-2:] == ["stop", "workbench"]
    assert calls[-1][-3:] == ["up", "-d", "workbench"]
    assert "--probe-models" not in calls[1]
    assert all("http://" not in value for command in calls for value in command)


def test_check_does_not_stop_workbench(monkeypatch):
    monkeypatch.setattr(module, "getproxies", lambda: {"all": "http://localhost:8123"})
    commands = []
    monkeypatch.setattr(module, "run", lambda cmd, env: commands.append(cmd) or 2)
    assert module.main(["check"]) == 2
    assert len(commands) == 1


def test_explicit_rancher_vm_entry_keeps_proxy_in_child_environment(monkeypatch):
    monkeypatch.setattr(module, 'ROOT', PureWindowsPath('F:/Project/TradeMind'))
    monkeypatch.setattr(module, 'getproxies', lambda: {'all': 'http://localhost:8123'})
    monkeypatch.setenv('WSLENV', 'EXISTING/u:HTTPS_PROXY')
    calls = []
    monkeypatch.setattr(module, 'run', lambda cmd, env: calls.append((cmd, env)) or 0)
    assert module.main(['check', '--via-rancher-vm']) == 0
    command, env = calls[0]
    assert command[:3] == ['rdctl', 'shell', 'docker-compose']
    assert '/mnt/f/Project/TradeMind/compose.yaml' in command
    assert env['WSLENV'] == 'EXISTING/u:HTTP_PROXY/u:HTTPS_PROXY/u:NO_PROXY/u'
    assert module.os.environ['WSLENV'] == 'EXISTING/u:HTTPS_PROXY'
    assert all('http://' not in item for item in command)


def test_absent_proxy_prevents_container_launch(monkeypatch, capsys):
    monkeypatch.setattr(module, "getproxies", lambda: {})
    monkeypatch.setattr(module, "run", lambda *args: pytest.fail("no Docker calls allowed"))
    assert module.main(["paper"]) == 2
    assert "blocked" in capsys.readouterr().err


def test_recovery_failure_cannot_report_success(monkeypatch):
    monkeypatch.setattr(module, "getproxies", lambda: {"all": "http://localhost:8123"})
    monkeypatch.setattr(module, "run", lambda cmd, env: 1 if "up" in cmd else 0)
    assert module.main(["paper", "--steps", "1"]) == 1


@pytest.mark.parametrize('stop_code', [0, 1])
def test_timeout_stops_only_this_container_before_restore(monkeypatch, tmp_path, stop_code):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'getproxies', lambda: {'all': 'http://localhost:8123'})
    calls = []
    def run(command, environment):
        calls.append(command)
        if 'run' in command:
            raise module.subprocess.TimeoutExpired(command, 1)
        if command[:2] == ['docker', 'stop']:
            return stop_code
        return 0
    monkeypatch.setattr(module, 'run', run)
    assert module.main(['paper', '--steps', '1', '--max-seconds', '1']) == (2 if stop_code == 0 else 1)
    name = calls[1][calls[1].index('--name')+1]
    assert calls[2] == ['docker', 'stop', '--time', '10', name]
    assert name.startswith('trademind-paper-')
    assert any('up' in cmd for cmd in calls) == (stop_code == 0)
    assert list((tmp_path/'data/runtime/container').glob('*-stop.json'))
