"""Uniform bounded project reads; no execution and no additional write capability."""
from pathlib import Path
import json
import re
import subprocess

BLOCKED={'.git','.codex','.claude','.deepseek-mcp','credentials','node_modules','.venv','venv','__pycache__','.tiered'}
SECRET=re.compile(r'(?:^\.env(?:\.|$)|^(?:credentials?|secrets?|proxy)(?:\.(?:json|toml|ya?ml|ini|conf)|$)|^private[_.-]?key|^id_rsa|^id_ed25519|\.pem$|\.pfx$)',re.I)
PROJECT_SCHEMAS=[
    {'name':'project_read','description':'分页读取当前项目文本文件。路径必须在项目内；拒绝凭据、控制目录；offset/limit 为行。',
        'parameters':{'type':'object','properties':{'path':{'type':'string'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':400}},'required':['path'],'additionalProperties':False}},
    {'name':'project_search','description':'在项目文件中搜索字面文本，返回有界匹配列表；不运行 shell，不搜索凭据和控制目录。',
        'parameters':{'type':'object','properties':{'query':{'type':'string'},'path':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':100}},'required':['query'],'additionalProperties':False}},
]


def allowed(root,path, directory=False):
    root=Path(root).resolve()
    if not isinstance(path,str) or not path or '\x00' in path or ':' in path:raise ValueError('项目路径无效。')
    target=(root/path).resolve()
    if not target.is_relative_to(root):raise ValueError('路径越出当前项目。')
    relative=target.relative_to(root)
    if any(p.lower() in BLOCKED or SECRET.search(p) for p in relative.parts):raise ValueError('不能读取凭据、代理或控制目录。')
    if not target.exists() or not (target.is_dir() if directory else target.is_file()):raise ValueError('项目路径不存在或类型不符。')
    return target


def read(root,args):
    if not isinstance(args,dict) or set(args)-{'path','offset','limit'}:raise ValueError('项目读取参数无效。')
    offset=args.get('offset',0);limit=args.get('limit',200)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=400:raise ValueError('项目读取分页无效。')
    path=allowed(root,args.get('path'))
    if path.stat().st_size>2_000_000:raise ValueError('文件超过 2 MB，请缩小材料范围。')
    text=path.read_text(encoding='utf-8',errors='strict')
    if '\x00' in text:raise ValueError('不支持二进制文件。')
    lines=text.splitlines();selected=[];characters=0
    for index in range(offset,min(len(lines),offset+limit)):
        line=lines[index]
        if len(line)>8000:
            if selected:break
            raise ValueError('单行超过读取预算，请使用项目外的用户工具检查此数据文件。')
        if characters+len(line)+1>12000:break
        selected.append(line);characters+=len(line)+1
    end=offset+len(selected)
    return {'path':path.relative_to(Path(root).resolve()).as_posix(),'offset':offset,'text':'\n'.join(selected),
        'next_offset':end if end<len(lines) else None,'total_lines':len(lines)}


def search(root,args):
    if not isinstance(args,dict) or set(args)-{'query','path','limit'}:raise ValueError('项目搜索参数无效。')
    query=args.get('query');limit=args.get('limit',40)
    if not isinstance(query,str) or not 1<=len(query)<=500 or '\n' in query or '\x00' in query or type(limit) is not int or not 1<=limit<=100:raise ValueError('搜索文字或数量无效。')
    scope=allowed(root,args.get('path','.'),directory=True)
    # Git's tracked + nonignored list avoids crawling installed runtimes and generated trees.
    try:
        result=subprocess.run(['git','-c','core.fsmonitor=false','-c','safe.directory='+str(Path(root).resolve()),
            'ls-files','--cached','--others','--exclude-standard','-z'],cwd=root,capture_output=True,timeout=10,
            creationflags=0x08000000 if __import__('os').name=='nt' else 0)
    except (OSError,subprocess.TimeoutExpired):raise ValueError('项目索引不可用，请先读取已知文件。') from None
    if result.returncode:raise ValueError('项目搜索需要有效 Git 索引；可用 project_read 直接读取已知文件。')
    paths=list(dict.fromkeys(result.stdout.decode('utf-8',errors='strict').split('\x00')))
    matches=[];scanned=0;bytes_read=0;incomplete=False
    for relative in paths:
        if not relative:continue
        try:path=allowed(root,relative)
        except ValueError:continue
        if not path.is_relative_to(scope) or path.stat().st_size>512000:continue
        if scanned>=1000 or bytes_read+path.stat().st_size>8_000_000:incomplete=True;break
        scanned+=1;bytes_read+=path.stat().st_size
        try:text=path.read_text(encoding='utf-8')
        except (OSError,UnicodeError):continue
        if '\x00' in text:continue
        for line_number,line in enumerate(text.splitlines(),1):
            at=line.find(query)
            if at<0:continue
            match={'path':relative,'line':line_number,'text':line[max(0,at-100):at+300]}
            if len(json.dumps([*matches,match],ensure_ascii=False))>12000:incomplete=True;break
            matches.append(match)
            if len(matches)>=limit:incomplete=True;break
        if incomplete:break
    return {'matches':matches,'partial':incomplete,'files_scanned':scanned,'scope':scope.relative_to(Path(root).resolve()).as_posix()}


def execute(root,name,args):
    if name=='project_read':return read(root,args)
    if name=='project_search':return search(root,args)
    raise ValueError('未知只读项目工具。')
