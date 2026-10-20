"""Loopback-only GUI host with origin protection and durable local history."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from engines import (ROOT,SCRIPTS,HIDDEN,preferences,auth_status,catalogs,capture,
                     project_path,deepseek_session,read_response,environment)
from store import Store
from workflows import Workflows
import roster

STATE=Path(os.environ.get('TIERED_GUI_DATA_DIR',str(preferences.STATE/'gui')))
STORE=Store(STATE)
WORK=Workflows(STORE)
TOKEN=secrets.token_urlsafe(32)
PORT=int(os.environ.get('TIERED_GUI_PORT','49315'))
ORIGIN=f'http://127.0.0.1:{PORT}'
WEB=Path(__file__).parent/'web'
LOGIN={}
CATALOG=None


async def bootstrap(request):
    global CATALOG
    if CATALOG is None: CATALOG=await catalogs()
    return JSONResponse({'token':TOKEN,'preferences':preferences.load(),'catalogs':CATALOG,
        'connections':await roster.connections(preferences.load()),'rooms':STORE.rooms(),'version':'0.4.0'})


async def health(request):
    return JSONResponse({'service':'tiered-dev-gui','version':'0.4.0'})


async def models(request):
    global CATALOG
    CATALOG=await catalogs(refresh=True)
    return JSONResponse(CATALOG)


async def settings(request):
    body=await request.json()
    if body.get('deepseek_key'): preferences.save_key(body.pop('deepseek_key'))
    value=preferences.save(body)
    return JSONResponse({'preferences':value,'connections':await roster.connections(value)})


async def connections(request):
    return JSONResponse(await roster.connections(preferences.load()))


async def member_key(request):
    body=await request.json();mid=request.path_params['mid']
    roster.member(preferences.load(),mid)
    preferences.save_member_key(mid,body.get('key',''))
    return JSONResponse({'connections':await roster.connections(preferences.load())})


async def member_models(request):
    from openai import AsyncOpenAI
    m=roster.member(preferences.load(),request.path_params['mid'])
    if m['backend'] in ['chatgpt','claude']:
        value=await catalogs(refresh=True);return JSONResponse(value[m['backend']])
    key=preferences.get_member_key(m) or ('local-no-auth' if m.get('no_auth') else '')
    if not key:raise ValueError('请先保存此成员的 API 密钥。')
    try:
        async with AsyncOpenAI(api_key=key,base_url=m['base_url'],timeout=20,max_retries=0) as client:
            data=await client.models.list()
            return JSONResponse([{'id':x.id,'label':x.id} for x in data.data][:300])
    except Exception:raise ValueError('模型目录读取失败，请检查连接；也可以手动填写模型 ID。') from None


async def login(request):
    provider=request.path_params['provider']
    if provider not in ['chatgpt','claude']: raise ValueError('请选择有效提供方。')
    if provider in LOGIN and not LOGIN[provider].done():
        return JSONResponse({'status':'waiting','message':'登录窗口已经打开，请在浏览器中完成。'})
    async def perform():
        import shutil
        args=[shutil.which('codex'),'login'] if provider=='chatgpt' else [shutil.which('claude'),'auth','login','--claudeai']
        try: await capture(args,timeout=300)
        except Exception: pass
    LOGIN[provider]=asyncio.create_task(perform())
    return JSONResponse({'status':'waiting','message':'请在打开的登录页面中完成授权，然后点击“检查连接”。'})


async def folder(request):
    script='''Add-Type -AssemblyName System.Windows.Forms
$picker = New-Object System.Windows.Forms.FolderBrowserDialog
$picker.Description = '选择代码项目文件夹'
$picker.ShowNewFolderButton = $false
if ($picker.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
 [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
 [Console]::Write($picker.SelectedPath)
}
$picker.Dispose()
'''
    code,out,err=await capture(['powershell.exe','-NoProfile','-STA','-Command',script],timeout=180)
    return JSONResponse({'path':out.strip() if code==0 else ''})


async def create_room(request):
    body=await request.json()
    path=project_path(body.get('project',''))
    value=STORE.create(str(body.get('title','新的协作')),str(path))
    STORE.set_policy(value['id'],preferences.load()['cooperation_policy'])
    return JSONResponse(value)


async def room(request):
    rid=request.path_params['rid']
    return JSONResponse({'room':STORE.room(rid),'messages':STORE.messages(rid),
        'policy':STORE.policy(rid,preferences.load()['cooperation_policy'])})


async def policy(request):
    rid=request.path_params['rid'];STORE.room(rid);body=await request.json()
    value=STORE.set_policy(rid,body.get('text',''))
    STORE.message(rid,'system','合作方式已保存。'+('当前步骤结束后，监工会按新规则重新检查。' if rid in WORK.running else '下次监工协作将使用这些规则。'),phase='规则更新')
    return JSONResponse(value)


async def send(request):
    rid=request.path_params['rid'];body=await request.json()
    text=body.get('text','')
    if not isinstance(text,str) or not 1<=len(text.strip())<=24000: raise ValueError('请输入 1 至 24000 字符的需求。')
    mode=body.get('mode','discuss');target=body.get('target','all')
    if mode not in ['discuss','workflow']:
        raise ValueError('发送模式无效。')
    selected=preferences.save(body.get('preferences',{}))
    if target not in ['all','supervisor',*[m['id'] for m in selected['members']]]:raise ValueError('请选择当前团队中的成员。')
    if mode=='discuss' and target=='supervisor':
        STORE.room(rid)
        current=STORE.policy(rid,selected['cooperation_policy'])
        STORE.set_policy(rid,current['text']+'\n\n用户补充：'+text.strip())
        if rid in WORK.running:
            STORE.message(rid,'you',text.strip(),phase='补充合作方式')
            STORE.message(rid,'system','已收到新的合作要求，监工将在当前步骤结束后重新检查。',phase='规则更新')
            return JSONResponse({'status':'updated'})
        mode='policy'
    WORK.start(rid,text.strip(),mode,target,selected)
    return JSONResponse({'status':'started'})


async def stop(request):
    await WORK.stop(request.path_params['rid'])
    return JSONResponse({'status':'stopped'})


async def resume(request):
    event=WORK.resume.get(request.path_params['rid'])
    if not event: raise ValueError('此会话当前没有等待执行的计划。')
    event.set();return JSONResponse({'status':'resumed'})


async def events(request):
    rid=request.path_params['rid'];STORE.room(rid)
    after=max(int(request.query_params.get('after','0')),int(request.headers.get('last-event-id','0')))
    async def generate():
        nonlocal after
        while not await request.is_disconnected():
            batch=STORE.events(rid,after)
            for value in batch:
                after=value['seq']
                yield 'id: '+str(after)+'\ndata: '+json.dumps(value,ensure_ascii=False)+'\n\n'
            if not batch: yield ': heartbeat\n\n'
            await asyncio.sleep(0.45)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


async def export(request):
    rid=request.path_params['rid'];value=STORE.room(rid)
    text='# '+value['title']+'\n\n项目：'+value['project']+'\n'
    labels={**preferences.load()['member_names'],'you':'你','system':'系统','supervisor':'监工'}
    for message in STORE.messages(rid):
        text+='\n## '+labels.get(message['provider'],message['provider'])
        text+=' · '+message['model']+' · '+message['phase']+'\n\n'+message['text']+'\n'
    return Response(text,media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="tiered-chat.md"'})


async def artifacts(request):
    from engines import build_snapshot
    root=project_path(STORE.room(request.path_params['rid'])['project'])
    snapshot,paths,truncated=await asyncio.to_thread(build_snapshot,root)
    result={'diff':snapshot,'truncated':truncated,'plan':'尚未生成计划。','report':'尚未生成报告。'}
    for key,name in [('plan','PLAN.md'),('report','REPORT.md')]:
        path=(root/'.tiered'/name).resolve()
        if path.is_relative_to(root) and path.is_file():
            with path.open('r',encoding='utf-8') as stream: result[key]=stream.read(150000)
    latest=STORE.latest_event(request.path_params['rid'],'report')
    if latest:result['report']=latest['text']
    latest=STORE.latest_event(request.path_params['rid'],'plan')
    if latest:
        from workflows import contract
        result['plan']='\n\n'.join(contract(task) for task in latest['tasks'])
    return JSONResponse(result)


async def recovery(request):
    rid=request.path_params['rid'];room=STORE.room(rid)
    if rid in WORK.running: raise ValueError('请等待或停止当前执行后检查恢复记录。')
    root=project_path(room['project']);body=await request.json()
    selected=preferences.load()
    source=next((m for m in selected['members'] if m['backend'] in ['deepseek','openai-compatible'] and (preferences.get_member_key(m) or m.get('no_auth'))),None)
    if source is None:
        raise ValueError('当前没有配置可用的 API 执行连接。CLI 文件修改请从“变更与报告”核对。')
    async with deepseek_session(root,source['model'],member=source) as session:
        data=json.loads(read_response(await session.call_tool('get_deepseek_recovery',{})))
        if not data.get('ok'): raise ValueError(data.get('error','恢复记录读取失败。'))
        pending=data.get('pending',[])
        if body.get('acknowledge'):
            ids=[]
            for record in pending:
                path=(root/record['path']).resolve()
                if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record.get('sha256'):
                    raise ValueError('文件内容与记录不同，不能自动确认：'+record.get('path',''))
                ids.append(record['transaction_id'])
            if ids:
                result=json.loads(read_response(await session.call_tool('acknowledge_deepseek_mutations',{'transaction_ids':ids})))
                if not result.get('ok'): raise ValueError('恢复确认失败。')
            return JSONResponse({'pending':[],'acknowledged':len(ids)})
        return JSONResponse({'pending':pending})


@asynccontextmanager
async def lifespan(app):
    STATE.mkdir(parents=True,exist_ok=True)
    (STATE/'service.json').write_text(json.dumps({'pid':os.getpid(),'url':ORIGIN,'port':PORT}),encoding='utf-8')
    yield
    for rid in list(WORK.running): await WORK.stop(rid)
    for task in LOGIN.values(): task.cancel()


routes=[Route('/',lambda request:FileResponse(WEB/'index.html')),
    Route('/api/health',health),Route('/api/bootstrap',bootstrap),Route('/api/models',models,methods=['POST']),
    Route('/api/settings',settings,methods=['POST']),Route('/api/connections',connections),
    Route('/api/members/{mid}/key',member_key,methods=['POST']),Route('/api/members/{mid}/models',member_models,methods=['POST']),
    Route('/api/login/{provider}',login,methods=['POST']),Route('/api/folder',folder,methods=['POST']),
    Route('/api/rooms',create_room,methods=['POST']),Route('/api/rooms/{rid}',room),
    Route('/api/rooms/{rid}/policy',policy,methods=['POST']),
    Route('/api/rooms/{rid}/send',send,methods=['POST']),Route('/api/rooms/{rid}/stop',stop,methods=['POST']),
    Route('/api/rooms/{rid}/resume',resume,methods=['POST']),Route('/api/rooms/{rid}/events',events),
    Route('/api/rooms/{rid}/export',export),Route('/api/rooms/{rid}/recovery',recovery,methods=['POST']),
    Route('/api/rooms/{rid}/artifacts',artifacts),
    Mount('/assets',StaticFiles(directory=WEB),name='assets')]
app=Starlette(routes=routes,lifespan=lifespan)


async def protect(request,call_next):
    host=request.headers.get('host','')
    if host not in [f'127.0.0.1:{PORT}',f'localhost:{PORT}']:
        return JSONResponse({'error':'Invalid host'},status_code=403)
    origin=request.headers.get('origin')
    if origin and origin not in [ORIGIN,f'http://localhost:{PORT}']:
        return JSONResponse({'error':'Origin denied'},status_code=403)
    if request.method=='POST':
        if request.headers.get('x-tiered-token')!=TOKEN:
            return JSONResponse({'error':'请刷新页面后重试。'},status_code=403)
        if int(request.headers.get('content-length','0'))>100000:
            return JSONResponse({'error':'请求过大。'},status_code=413)
    try:
        response=await call_next(request)
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        return JSONResponse({'error':str(exc)[:1600]},status_code=400)
    except Exception:
        return JSONResponse({'error':'本机服务暂时无法完成操作，请检查连接或重新启动。'},status_code=500)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response

app.add_middleware(BaseHTTPMiddleware,dispatch=protect)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='warning',access_log=False)
