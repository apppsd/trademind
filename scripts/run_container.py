"""Bounded Compose runs using an existing host proxy; never persist or log it."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]


def container_environment(proxies):
    result = os.environ.copy()
    for name in ("http", "https"):
        value = proxies.get(name) or proxies.get("all")
        if not value:
            raise ValueError("existing host HTTP(S) proxy required")
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or not parsed.port
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("requires an existing unauthenticated loopback HTTP proxy")
        result[name.upper()+"_PROXY"] = urlunsplit(
            ("http", "host.docker.internal:"+str(parsed.port), "", "", ""))
    result["NO_PROXY"] = "localhost,127.0.0.1,::1"
    return result


def run(command, environment):
    timeout = None
    if 'run' in command and 'paper' in command:
        timeout = int(command[command.index('--max-seconds')+1]) + 75
    return subprocess.run(command, cwd=ROOT, env=environment, timeout=timeout).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("check", "paper"))
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--probe-models", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=3600)
    parser.add_argument('--via-rancher-vm', action='store_true',
                        help='Use rdctl shell when the Windows Docker pipe is unavailable')
    args = parser.parse_args(argv)
    if args.steps < 1 or args.steps > 720:
        parser.error("steps must be between 1 and 720")
    if not 1 <= args.max_seconds <= 3600:
        parser.error('max-seconds must be between 1 and 3600')
    try:
        environment = container_environment(getproxies())
    except (ValueError, TypeError):
        print("Container run blocked: compatible existing host proxy unavailable.", file=sys.stderr)
        return 2
    base = ["docker", "compose"]
    docker_base = ['docker']
    if args.via_rancher_vm:
        drive = ROOT.drive
        if len(drive) != 2 or drive[1] != ':':
            parser.error('Rancher VM entry requires a Windows drive workspace')
        vm_root = '/mnt/' + drive[0].lower() + ROOT.as_posix()[2:]
        base = ['rdctl', 'shell', 'docker-compose', '--project-directory', vm_root,
                '--file', vm_root + '/compose.yaml']
        docker_base = ['rdctl', 'shell', 'docker']
        # WSLENV forwards only these already validated, unauthenticated proxy
        # variables to the VM process. Never persist them in app settings.
        forwards = ['HTTP_PROXY/u', 'HTTPS_PROXY/u', 'NO_PROXY/u']
        existing = environment.get('WSLENV', '').split(':')
        environment['WSLENV'] = ':'.join([v for v in existing if v and v.split('/')[0]
                                        not in ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')] + forwards)
    command = base+["--profile", args.service, "run", "--rm", "-T",
                   "-e", "HTTP_PROXY", "-e", "HTTPS_PROXY", "-e", "NO_PROXY", args.service]
    if args.service == "paper":
        container_name = 'trademind-paper-' + uuid4().hex
        command[command.index('-e'):command.index('-e')] = ['--name', container_name]
        command += ["--config", "config/t0-container.local.toml", "t0-paper", "--steps", str(args.steps),
                    '--max-seconds', str(args.max_seconds)]
        if args.probe_models:
            command.append("--probe-models")
    try:
        if args.service == 'paper':
            active = subprocess.run(docker_base+['ps', '--filter', 'label=com.docker.compose.service=auto',
                                     '--filter', 'label=com.docker.compose.project=trademind', '--format', '{{.ID}}'],
                                    cwd=ROOT, env=environment, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace')
            if active.returncode or active.stdout.strip():
                print('Manual paper blocked: verify or stop the automatic supervisor first.', file=sys.stderr)
                return 2
        if args.service == "check":
            return run(command, environment)
        if run(base+["stop", "workbench"], environment):
            return 1
        result = 1
        recovered = False
        safe_to_restore = True
        try:
            result = run(command, environment)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            # Stop only the randomly named container created by this invocation.
            safe_to_restore = run(docker_base + ['stop', '--time', '10', container_name], environment) == 0
            from trademind.execution.paper_run import atomic_json
            atomic_json(ROOT/'data/runtime/container'/(container_name+'-stop.json'), {
                'status': 'incomplete', 'reason': type(exc).__name__,
                'container_name': container_name, 'container_stopped': safe_to_restore,
                'model_calls': None, 'orders': None, 'requires_account_reconciliation': True})
            result = 130 if isinstance(exc, KeyboardInterrupt) else 2
        finally:
            recovered = safe_to_restore and run(base+["up", "-d", "workbench"], environment) == 0
            if not recovered:
                print("Workbench not restored; verify this run's worker has stopped before restarting.", file=sys.stderr)
        return result if recovered else 1
    except OSError:
        print("Container run failed: Docker executable unavailable.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Container run interrupted; verify no paper worker remains before continuing.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
