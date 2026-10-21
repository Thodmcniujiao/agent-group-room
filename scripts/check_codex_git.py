"""Run git inside the real Codex sandbox (no model call) with and without the host's Git trust.

Usage: python scripts/check_codex_git.py <project> <report.json>
Writes the exact commands, exit codes and output; never changes the project.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'gui'),str(ROOT/'plugins/tiered-dev/scripts')]
from cli_runtime import find_cli
from engines import environment,member_git_environment


def run(args,env,cwd):
    try:
        p=subprocess.run(args,cwd=cwd,env=env,capture_output=True,timeout=120,stdin=subprocess.DEVNULL)
        return {'args':args,'exit_code':p.returncode,'stdout':p.stdout.decode('utf-8','replace')[-3000:],
                'stderr':p.stderr.decode('utf-8','replace')[-3000:]}
    except Exception as exc:return {'args':args,'error':repr(exc)}


def main():
    project=Path(sys.argv[1]).resolve();report=Path(sys.argv[2])
    codex=find_cli('codex');git=['git','rev-parse','--show-toplevel']
    trust=member_git_environment(project);base=environment()
    table='{'+', '.join(n+' = '+json.dumps(v) for n,v in trust.items())+'}'
    results={'codex':codex,'project':str(project),'trust':trust,'cases':{}}
    if codex:
        results['cases']['codex_version']=run([codex,'--version'],base,project)
        sandbox=[codex,'sandbox','windows','--']
        results['cases']['sandbox_without_trust']=run(sandbox+git,base,project)
        results['cases']['sandbox_with_env_trust']=run(sandbox+git,{**base,**trust},project)
        results['cases']['sandbox_with_policy_set']=run([codex,'-c','shell_environment_policy.set='+table,'sandbox','windows','--']+git,base,project)
    results['cases']['plain_git']=run(git,base,project)
    report.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
