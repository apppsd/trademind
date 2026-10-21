"""Start/status/stop container auto monitoring without exposing proxy values."""
import argparse
import subprocess

from run_container import ROOT, container_environment, getproxies


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','stop','status'))
    parser.add_argument('--recreate',action='store_true')
    args=parser.parse_args(argv)
    vm_root='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    base=['rdctl','shell','docker-compose','--project-directory',vm_root,'--file',vm_root+'/compose.yaml']
    if args.action!='start':
        command=base+['--profile','auto', 'stop' if args.action=='stop' else 'ps','auto']
        return subprocess.run(command,cwd=ROOT).returncode
    try:
        env=container_environment(getproxies())
    except (ValueError,TypeError):
        print('Automatic start blocked: compatible existing host proxy required.')
        return 2
    forwards=['HTTP_PROXY/u','HTTPS_PROXY/u','NO_PROXY/u']
    existing=env.get('WSLENV','').split(':')
    env['WSLENV']=':'.join([v for v in existing if v and v.split('/')[0]
                          not in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY')]+forwards)
    active=subprocess.run(['rdctl','shell','docker','ps','--filter','label=com.docker.compose.service=paper',
                           '--filter','label=com.docker.compose.project=trademind','--format','{{.ID}}'],
                          cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if active.returncode or active.stdout.strip():
        print('Automatic start blocked: verify or stop the manual paper worker first.')
        return 2
    if subprocess.run(base+['config','--quiet'],cwd=ROOT,env=env).returncode:
        return 1
    if subprocess.run(base+['stop','workbench'],cwd=ROOT,env=env).returncode:
        return 1
    command=base+['--profile','auto','up','-d','--no-deps']
    if args.recreate: command.append('--force-recreate')
    return subprocess.run(command+['auto'],cwd=ROOT,env=env).returncode


if __name__=='__main__':
    raise SystemExit(main())
