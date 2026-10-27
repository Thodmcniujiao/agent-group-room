"""Deterministic checks in host/tool environments, without inference."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import roster
from engines import deepseek_session,read_response,tool_environment,preferences,HIDDEN
import git_access

VERSION=2
RESUME_BUTTON='__host_recheck__'


def check(name,ok,detail,env):
    return dict(name=name,ok=bool(ok),detail=detail,env={'name':env,'version':VERSION})


def fingerprint(failed):
    def stable(detail):
        # Preserve error classes/codes; process-local addresses, paths and clocks are not fault identities.
        codes=[]
        def keep(match):
            codes.append(match.group(0).lower());return ' ERRORCODE '
        detail=re.sub(r'\b(?:HTTP(?:\s+status)?|WinError|errno|exit(?:_code| code)?)\s*[:=\[]?\s*(?:0x[0-9a-f]+|\d+)',keep,detail,flags=re.I)
        detail=re.sub(r'(["\'])[A-Za-z]:[\\/].*?\1',' PATH ',detail)
        detail=re.sub(r'[A-Za-z]:[\\/][^\s,;\)\]]+',' PATH ',detail)
        detail=re.sub(r'(?<!\w)/(?:tmp|var/tmp)/[^\s,;\)\]]+',' PATH ',detail)
        detail=re.sub(r'\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?',' TIME ',detail)
        detail=re.sub(r'\b0x[0-9a-f]+\b|\b\d+\b','#',detail,flags=re.I)
        return [re.sub(r'\s+',' ',detail).strip()[:300],codes]
    rows=sorted((c['name'],stable(c['detail']),c['env']) for c in failed)
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def render(checks):
    return '\n'.join(('✓ ' if c['ok'] else '✗ ')+c['name']+'：'+c['detail'] for c in checks)


def is_resume_only(text,settings):
    return text==RESUME_BUTTON or text.strip() in settings.get('resume_words',['继续'])


async def git_check(root,executor=False):
    name='git_executor' if executor else 'git_host'
    try:
        initialized=False
        if executor:
            from tool_child_env import host_environment
            tools=tool_environment(root);old={k:os.environ.get(k) for k in tools}
            try:os.environ.update(tools);env=host_environment(root)
            finally:
                for k,v in old.items():
                    if v is None:os.environ.pop(k,None)
                    else:os.environ[k]=v
            proc=await asyncio.create_subprocess_exec('git','rev-parse','--show-toplevel',cwd=root,env=env,
                stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=HIDDEN)
            try:out,err=await asyncio.wait_for(proc.communicate(),15)
            except BaseException:
                from engines import kill_tree
                await kill_tree(proc);raise
            if proc.returncode:raise ValueError(err.decode(errors='replace').strip()[:1200])
            top=out.decode(errors='replace').strip()
        else:
            prepared=await git_access.ensure_repository(root,state_dir=preferences.STATE)
            top=prepared['root'];initialized=prepared['initialized']
        ok=Path(top).resolve()==Path(root).resolve()
        row=check(name,ok,('宿主已自动执行 git init：' if initialized else '')+top if ok else '请选择 Git 仓库根目录；实际根目录：'+top,name)
        if initialized:row['initialized']=True
        return row
    except Exception as exc:return check(name,False,str(exc)[:1600],name)


async def executor_checks(root,source,settings):
    if source['backend'] not in ['deepseek','openai-compatible']:
        from cli_runtime import find_cli
        available=bool(find_cli('codex' if source['backend']=='chatgpt' else 'claude'))
        return [check('executor_start',available,'CLI 已安装' if available else 'CLI 未安装',source['backend']),
                check('recovery',True,'此连接不使用 MCP 写入恢复记录',source['backend'])]
    try:
        async with asyncio.timeout(20):
            from context_settings import context_settings
            async with deepseek_session(root,source['model'],member=source,execution_budget=context_settings(settings.get('context'))) as session:
                health=json.loads(read_response(await session.call_tool('executor_health',{})))
                if not health.get('ok'):return [check('executor_start',False,health.get('error','配置加载失败'),'mcp')]
                recovery=json.loads(read_response(await session.call_tool('get_deepseek_recovery',{})))
                confirmed=[]
                if recovery.get('ok') and recovery.get('pending') and settings.get('_recovery_verified_hashes'):
                    from recovery_verification import reconcile
                    pending,confirmed=await reconcile(session,root,recovery['pending'],settings.get('_recovery_allowed_paths',[]),settings['_recovery_verified_hashes'],read_response)
                    recovery={**recovery,'pending':pending}
                row=check('recovery',bool(recovery.get('ok')) and not recovery.get('pending'),
                    ('有待核实写入，请先核对恢复记录' if recovery.get('pending') else '无待核实写入') if recovery.get('ok') else recovery.get('error','恢复记录读取失败'),'mcp')
                row['records']=recovery.get('pending',[])
                if confirmed:
                    row['acknowledged_records']=confirmed
                    row['detail']='宿主已按批准范围及执行证据确认 '+str(len(confirmed))+' 条成功落盘记录；'+row['detail']
                return [check('executor_start',True,'实际执行配置加载成功；output='+str(health['max_output_tokens'])+
                    '；tool_rounds='+str(health.get('max_turns','未知')),'mcp'),row]
    except Exception as exc:
        from orchestrator import describe_error
        return [check('executor_start',False,describe_error(exc)[:1800],'mcp')]


async def preflight(root,settings,quota=None,agent=None):
    aid=agent or settings.get('executor');source=roster.member(settings,aid)
    rows=[await git_check(root),await git_check(root,True)]
    account=next((a for a in (quota or {}).get('participants',[]) if a.get('agent_id')==aid or a.get('id')==aid),None)
    rows.append(check('quota',not account or account.get('level')!='exhausted',
        account.get('summary','无可用读数，额度未知') if account else '无可用读数，额度未知','account'))
    rows.extend(await executor_checks(root,source,settings));return rows


async def code_hash(root):
    """Read-only identity of tracked and untracked project content; no write-tree."""
    try:
        names=await git_access.run_host(root,'ls-files','-z','--cached','--others','--exclude-standard',state_dir=preferences.STATE)
        digest=hashlib.sha256()
        for name in sorted(set(names.split('\0'))-{''}):
            if name.startswith('.tiered/'):continue
            path=Path(root)/name;digest.update(name.encode());digest.update(b'\0')
            if path.is_symlink():digest.update(('link:'+os.readlink(path)).encode())
            elif path.is_file():
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
            else:digest.update(b'<deleted>')
        return digest.hexdigest()
    except (ValueError,OSError,git_access.GitError):return None
