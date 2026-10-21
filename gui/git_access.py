"""Git access for the one project the user selected in the host.

Git refuses repositories whose folder owner differs from the current account
("dubious ownership"). OneDrive folders and folders created by an elevated or
sandboxed process often hit this. Instead of asking the user to edit their
global Git config (which host tools and executor shells do not even read,
because they run with HOME redirected to the workspace), the host writes a
small Git config of its own that trusts exactly the selected project root, and
points GIT_CONFIG_GLOBAL at it. Global scope is honoured by the ownership check
since Git 2.35.2, and GIT_CONFIG_GLOBAL exists since Git 2.32. No wildcard is
ever written, and repository config cannot widen the trust.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import subprocess

HIDDEN=0x08000000 if os.name=='nt' else 0
OUTPUT_LIMIT=40000
READ_ONLY={'status','diff','log','show','rev-parse','ls-files','blame','branch','describe'}
BRANCH_FLAGS={'--show-current','-a','--all','-r','--remotes','-v','-vv','--list','--no-color'}
BLOCKED_PREFIXES=('--output','--no-index','--ext-diff','--textconv','--contents','--open-files-in-pager',
    '--exec','--upload-pack','--git-dir','--work-tree','--namespace','--super-prefix','-O')
SAFE_OPTIONS=['-c','core.quotepath=false','-c','core.fsmonitor=false','-c','core.pager=cat',
    '-c','diff.external=','-c','color.ui=false']

TOOL_SCHEMA={'name':'Git',
    'description':('只读 Git 查询（宿主执行，已信任当前项目目录的所有权检查）。'
        'args[0] 为子命令：status、diff、log、show、rev-parse、ls-files、blame、branch、describe；'
        '其余为参数，例如 ["status","--short"]、["diff","--","src/a.py"]、["log","--oneline","-10"]。'
        '不能提交、切换、重置或写入文件。Read-only git for the current project.'),
    'parameters':{'type':'object','properties':{'args':{'type':'array','items':{'type':'string'},
        'minItems':1,'maxItems':40}},'required':['args'],'additionalProperties':False}}


class GitError(RuntimeError):
    def __init__(self,message,code=None,stderr=''):
        super().__init__(message);self.code=code;self.stderr=stderr


def trusted_path(root):
    """The spelling Git uses for safe.directory and in its ownership error."""
    return Path(root).resolve().as_posix()


def _quote(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'


def user_global_configs():
    """Configs the user's own Git would read globally, so host status matches theirs."""
    configured=os.environ.get('TIERED_USER_GIT_CONFIG_GLOBAL') or os.environ.get('GIT_CONFIG_GLOBAL')
    if configured:return [Path(configured)]
    home=Path(os.environ.get('HOME') or Path.home())
    xdg=Path(os.environ['XDG_CONFIG_HOME']) if os.environ.get('XDG_CONFIG_HOME') else home/'.config'
    return [xdg/'git'/'config',home/'.gitconfig']


def ensure_config(root,state_dir,include_user=False):
    """Write (only when changed) the host-managed global config trusting `root`."""
    trusted=trusted_path(root)
    folder=Path(state_dir)/'git';folder.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256((trusted.casefold() if os.name=='nt' else trusted).encode()).hexdigest()[:16]
    path=folder/(digest+('-host' if include_user else '-tool')+'.gitconfig')
    lines=['# Managed by Agent Group Room. Trusts only the project selected in the host.']
    if include_user:
        for config in user_global_configs():
            lines+=['[include]','\tpath = '+_quote(Path(config).as_posix())]
    lines+=['[safe]','\tdirectory = '+_quote(trusted),'']
    text='\n'.join(lines)
    try:
        if path.read_text(encoding='utf-8')==text:return path
    except OSError:pass
    temp=path.with_suffix('.tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,path)
    return path


def environment(config_path,base=None):
    """Environment for a Git child: our trust file, no prompts, no inherited repo redirection."""
    env={k:v for k,v in (os.environ if base is None else base).items()
         if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=str(config_path),GIT_TERMINAL_PROMPT='0')
    return env


def explain(stderr,root=None):
    text=(stderr or '').strip()
    low=text.lower()
    if 'dubious ownership' in low:
        return ('Git 拒绝了此目录的所有权（dubious ownership）。宿主已为该项目单独设置信任，但当前 Git 版本'
            '可能低于 2.35.2 而不支持；请升级 Git for Windows。原始错误：'+text[:600])
    if 'not a git repository' in low:
        return '所选目录不是 Git 仓库（或位于仓库之外）。原始错误：'+text[:400]
    if 'is not recognized' in low:
        return '未找到 git 程序；请安装 Git for Windows 并确保在 PATH 中。'
    return 'Git 命令失败：'+(text[:800] or '没有错误输出。')


async def run_host(root,*args,state_dir,timeout=20):
    """Host-side Git call (status, rev-parse…) that reports Git's actual error."""
    config=ensure_config(root,state_dir,include_user=True)
    try:
        proc=await asyncio.create_subprocess_exec('git','--no-pager','-c','core.quotepath=false',*args,cwd=str(root),
            env=environment(config),stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,creationflags=HIDDEN)
    except FileNotFoundError:
        raise GitError('未找到 git 程序；请安装 Git for Windows 并确保在 PATH 中。') from None
    try:out,err=await asyncio.wait_for(proc.communicate(),timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    err=err.decode('utf-8',errors='replace')
    if proc.returncode:raise GitError(explain(err,root),proc.returncode,err)
    return out.decode('utf-8',errors='replace')


def validate_args(args):
    if not isinstance(args,list) or not 1<=len(args)<=40 or not all(isinstance(a,str) and a and '\x00' not in a and len(a)<=1000 for a in args):
        raise ValueError('args 必须是 1 至 40 个非空字符串。')
    command,rest=args[0],args[1:]
    if command not in READ_ONLY:
        raise ValueError('只允许只读子命令：'+'、'.join(sorted(READ_ONLY)))
    paths=False
    for arg in rest:
        if arg=='--':paths=True;continue
        if paths or not arg.startswith('-'):
            if command=='branch':raise ValueError('branch 只能列出分支，不能创建、删除或重命名。')
            continue
        name=arg.split('=',1)[0]
        if command=='branch' and name not in BRANCH_FLAGS:
            raise ValueError('branch 只允许：'+' '.join(sorted(BRANCH_FLAGS)))
        if any(name==p or name.startswith(p) for p in BLOCKED_PREFIXES):
            raise ValueError('参数 '+name+' 可能写文件、读取项目外内容或运行外部程序，已拒绝。')
    return command,rest


def run_readonly(root,args,config_path,timeout=30):
    """Executor-facing Git tool. Returns text with the real exit code; never raises for Git failures."""
    try:command,rest=validate_args(args)
    except ValueError as exc:return 'ERROR: '+str(exc)
    extra=['--no-ext-diff','--no-textconv'] if command in ['diff','show','log'] else ['--no-textconv'] if command=='blame' else []
    argv=['git','--no-pager',*SAFE_OPTIONS,command,*extra,*rest]
    env=environment(config_path);env['GIT_OPTIONAL_LOCKS']='0'
    try:
        result=subprocess.run(argv,cwd=str(root),env=env,stdin=subprocess.DEVNULL,capture_output=True,
            timeout=timeout,creationflags=HIDDEN)
    except FileNotFoundError:return 'ERROR: 未找到 git 程序。'
    except subprocess.TimeoutExpired:return f'ERROR: git {command} 超过 {timeout} 秒未完成。'
    out=result.stdout.decode('utf-8',errors='replace');err=result.stderr.decode('utf-8',errors='replace')
    text=f'$ git {command} '+' '.join(rest)+f'\nexit_code: {result.returncode}\n'+out
    if err.strip():text+='\n--- stderr ---\n'+err
    if result.returncode:text+='\n--- 说明 ---\n'+explain(err,root)
    if len(text)>OUTPUT_LIMIT:text=text[:OUTPUT_LIMIT]+'\n... [输出已截断]'
    return text
