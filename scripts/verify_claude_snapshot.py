"""Opt-in two-request native snapshot probe. Export metadata, never native histories."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'gui'))


async def run(output):
    from engines import preferences,environment,HIDDEN,kill_tree
    from cli_runtime import find_cli
    settings=preferences.load();source=next(a for a in settings['agents'] if a['backend']=='claude')
    binary=find_cli('claude');sid=str(uuid.uuid4());marker='SNAPSHOT_'+uuid.uuid4().hex[:12]
    report={'scope':'Two read-only native Claude launches; first has append flags, resumed launch omits them. Not a token savings benchmark.',
        'model':source['model'],'effort':source.get('effort'),'session_id':sid,'requests':[]}
    with tempfile.TemporaryDirectory(prefix='agent-room-snapshot-probe-') as folder:
        root=Path(folder);config=root/'mcp.json';config.write_text('{"mcpServers":{}}',encoding='utf-8')
        for index in range(2):
            args=[binary,'-p','--restricted','--model',source['model'],'--permission-mode','plan','--permission-prompts','none','--tools','',
                '--strict-mcp-config','--mcp-config',str(config),'--output-format','stream-json','--verbose','--system-prompt-snapshot','on',
                '--resume' if index else '--session-id',sid]
            if source.get('effort'):args+=['--effort',source['effort']]
            if index==0:args+=['--append-system-prompt','固定系统规则：每次可见回复只能包含 '+marker+'，不要调用工具或解释。']
            proc=await asyncio.create_subprocess_exec(*args,cwd=root,env=environment(),stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=HIDDEN)
            try:
                stdout,stderr=await asyncio.wait_for(proc.communicate(('第一轮，请确认。' if index==0 else '第二轮，请继续遵守已保存的系统规则。').encode()),180)
                events=[]
                for line in stdout.splitlines():
                    try:events.append(json.loads(line))
                    except ValueError:pass
                result=next((e for e in reversed(events) if e.get('type')=='result'),{})
                detail=(result.get('result','')+' '+stderr.decode(errors='replace')).lower()
                report['requests'].append({'index':index+1,'append_flag_present':index==0,'exit_code':proc.returncode,
                    'matches_system_rule':result.get('result','').strip()==marker,'usage':result.get('usage',{}),'error':bool(proc.returncode or result.get('is_error')),
                    'failure_category':'weekly_quota' if 'weekly limit' in detail else 'authentication' if 'log in' in detail or 'login' in detail else 'unclassified' if proc.returncode else None})
                if proc.returncode or result.get('is_error'):break
            finally:await kill_tree(proc)
    # Only the probe's freshly generated UUID is read; never scan/export other sessions.
    snapshots=[]
    for path in (Path.home()/'.claude/projects').glob('*/'+sid+'.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            kind=str(event.get('type',''))
            if 'system' in kind.lower() and ('snapshot' in kind.lower() or 'prompt' in kind.lower()):
                encoded=json.dumps(event,ensure_ascii=False)
                snapshots.append({'type':kind,'sha256':hashlib.sha256(encoded.encode()).hexdigest(),'marker_occurrences':encoded.count(marker)})
    report['native_snapshot_records']=snapshots
    report['behavior_passed']=len(report['requests'])==2 and all(r['matches_system_rule'] and not r['error'] for r in report['requests'])
    report['snapshot_inspected']=any(s['marker_occurrences']==1 for s in snapshots)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['behavior_passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--report-dir',default='test-reports/token-audit-fixes-2026-10-22/claude-snapshot');args=parser.parse_args()
    raise SystemExit(asyncio.run(run(args.report_dir)))
