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

from engines import (ROOT,SCRIPTS,HIDDEN,preferences,auth_status,capture,
                     project_path,deepseek_session,read_response,environment)
from store import Store
from workflows import Workflows
import roster
from debugger import AgentDebugger
from model_catalog import shared_catalog
from account_usage import UsageMonitor
from logins import Logins
from room_settings import RoomSettings
from avatars import Avatars

STATE=Path(os.environ.get('TIERED_GUI_DATA_DIR',str(preferences.STATE/'gui')))
STORE=Store(STATE)
ROOMS=RoomSettings(STORE)
WORK=Workflows(STORE)
TOKEN=secrets.token_urlsafe(32)
PORT=int(os.environ.get('TIERED_GUI_PORT','49315'))
ORIGIN=f'http://127.0.0.1:{PORT}'
WEB=Path(__file__).parent/'web'
MODELS=shared_catalog()
DEBUG=AgentDebugger()
USAGE=UsageMonitor()
AVATARS=Avatars(preferences.STATE/'avatars.json')
WORK.usage=USAGE  # supervisor and members see the same account meters as the UI


def login_complete():
    roster.invalidate_auth()
    MODELS.request_refresh(preferences.load())
    USAGE.request_refresh(preferences.load(),force=True)


LOGIN=Logins(login_complete)


async def bootstrap(request):
    selected=preferences.load();MODELS.request_refresh(selected);USAGE.request_refresh(selected)
    return JSONResponse({'token':TOKEN,'preferences':selected,'model_catalog':MODELS.snapshot(selected),
        'account_usage':USAGE.snapshot(selected),'connections':await roster.connections(selected),
        'logins':LOGIN.snapshot(),'rooms':STORE.rooms(),'avatars':AVATARS.snapshot(),'version':'0.11.0'})


async def health(request):
    return JSONResponse({'service':'tiered-dev-gui','version':'0.11.0'})


async def avatars(request):
    if request.method=='GET':return JSONResponse({'avatars':AVATARS.snapshot()})
    identity=request.path_params['mid'];selected=preferences.load()
    if identity not in ['you','supervisor',*[a['id'] for a in selected['agents']]]:raise ValueError('请选择已保存的身份。')
    body=await request.json()
    if not isinstance(body,dict) or set(body)!={'image'}:raise ValueError('头像设置格式无效。')
    return JSONResponse({'avatars':AVATARS.save(identity,body['image'])})


async def models(request):
    selected=preferences.load()
    if request.method=='POST':MODELS.request_refresh(selected)
    return JSONResponse(MODELS.snapshot(selected))


async def usage(request):
    selected=preferences.load()
    ids=None
    if 'mid' in request.path_params:
        mid=request.path_params['mid'];roster.agent(selected,mid);ids=[mid]
    USAGE.request_refresh(selected,ids,force=request.method=='POST')
    return JSONResponse(USAGE.snapshot(selected))


async def settings(request):
    body=await request.json()
    key_changed=bool(body.get('deepseek_key'))
    if key_changed: preferences.save_key(body.pop('deepseek_key'))
    value=preferences.save(body)
    if key_changed:MODELS.request_refresh(value);USAGE.request_refresh(value,force=True)
    return JSONResponse({'preferences':value,'connections':await roster.connections(value),'model_catalog':MODELS.snapshot(value),'account_usage':USAGE.snapshot(value)})


async def connections(request):
    return JSONResponse(await roster.connections(preferences.load(),refresh=True))


async def connection_state(request):
    selected=preferences.load();USAGE.request_refresh(selected)
    current=ROOMS.load(request.query_params['room_id'],selected) if request.query_params.get('room_id') else None
    return JSONResponse({'preferences':selected,'connections':await roster.connections(selected,
        refresh=request.query_params.get('refresh')=='1'),'logins':LOGIN.snapshot(),
        'model_catalog':MODELS.snapshot(selected),'account_usage':USAGE.snapshot(selected),'room_settings':current})


async def room_configuration(request):
    rid=request.path_params['rid']
    value=ROOMS.update(rid,await request.json()) if request.method=='POST' else ROOMS.load(rid)
    if request.method=='POST':WORK.budget_gate.update(rid,value['context'])
    return JSONResponse({'room_settings':value})


async def room_member(request):
    rid=request.path_params['rid'];aid=request.path_params['mid'];body=await request.json()
    if request.url.path.endswith('/membership'):
        if not isinstance(body,dict):raise ValueError('成员设置无效。')
        value=ROOMS.membership(rid,aid,body.get('enabled'))
    else:value=ROOMS.member(rid,aid,body)
    return JSONResponse({'room_settings':value})


async def save_member(request):
    mid=request.path_params['mid'];preferences.member_id(mid);body=await request.json()
    if not isinstance(body,dict):raise ValueError('成员设置格式无效。')
    with preferences.LOCK:
        selected=preferences.load();members=selected['members'];index=next((i for i,m in enumerate(members) if m['id']==mid),None)
        if body.pop('create',False):
            if index is not None:raise ValueError('此成员已存在，请刷新后重试。')
            members.append({**body,'id':mid})
        else:
            if index is None:raise ValueError('成员已被移除，请刷新团队。')
            if set(body)-{'model','effort'}:raise ValueError('连接与名称请在 Agent 管理中修改。')
            team=selected['team']
            for ref in team:
                if ref['agent_id']==mid:ref.update(body)
        value=preferences.save({'members':members} if index is None else {'team':team})
    return JSONResponse({'preferences':value,'connections':await roster.connections(value)})


async def save_agent(request):
    aid=request.path_params['mid'];preferences.member_id(aid);body=await request.json()
    if not isinstance(body,dict):raise ValueError('Agent 配置格式无效。')
    with preferences.LOCK:
        selected=preferences.load();agents=selected['agents']
        index=next((i for i,a in enumerate(agents) if a['id']==aid),None)
        if body.pop('create',False):
            if index is not None:raise ValueError('此 Agent 已存在，请刷新后重试。')
            agents.append({**body,'id':aid})
        else:
            if index is None:raise ValueError('此 Agent 不存在，请刷新 Agent 管理。')
            agents[index]={**agents[index],**body,'id':aid}
        value=preferences.save({'agents':agents})
    MODELS.request_refresh(value,[aid])
    USAGE.request_refresh(value,[aid],force=True)
    return JSONResponse({'preferences':value,'connections':await roster.connections(value),'model_catalog':MODELS.snapshot(value),'account_usage':USAGE.snapshot(value)})


async def team_membership(request):
    aid=request.path_params['mid'];body=await request.json()
    if not isinstance(body,dict) or type(body.get('enabled')) is not bool:raise ValueError('请选择是否加入当前团队。')
    with preferences.LOCK:
        selected=preferences.load();source=roster.agent(selected,aid);refs=selected['team']
        ref=next((r for r in refs if r['agent_id']==aid),None)
        if ref is None:
            ref=dict(agent_id=aid,model=source['model'],effort=source['effort']);refs.append(ref)
        ref['enabled']=body['enabled']
        value=preferences.save({'team':refs})
    return JSONResponse({'preferences':value,'connections':await roster.connections(value)})


async def debug_agent(request):
    agent=roster.agent(preferences.load(),request.path_params['mid'])
    if request.method=='GET':return JSONResponse({'jobs':DEBUG.history(agent['id'])})
    body=await request.json()
    if not isinstance(body,dict):raise ValueError('调试指令格式无效。')
    return JSONResponse(DEBUG.start(agent,body))


async def debug_cancel(request):
    return JSONResponse(await DEBUG.cancel(request.path_params['jid']))


async def debug_events(request):
    job=DEBUG.get(request.path_params['jid'])
    async def generate():
        version=-1
        while not await request.is_disconnected():
            if job.version!=version:
                version=job.version
                yield 'data: '+json.dumps(job.snapshot(),ensure_ascii=False)+'\n\n'
                if job.data['status']!='running':break
            else:yield ': heartbeat\n\n'
            await asyncio.sleep(0.2)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


async def remove_member(request):
    mid=request.path_params['mid']
    with preferences.LOCK:
        selected=preferences.load();roster.member(selected,mid)
        for ref in selected['team']:
            if ref['agent_id']==mid:ref['enabled']=False
        value=preferences.save({'team':selected['team']})
    return JSONResponse({'preferences':value,'connections':await roster.connections(value)})


async def member_key(request):
    body=await request.json();mid=request.path_params['mid']
    m=roster.agent(preferences.load(),mid)
    if m['backend'] in ['chatgpt','claude']:raise ValueError('此成员使用本机登录，不需要 API 密钥。')
    preferences.save_member_key(mid,body.get('key',''))
    selected=preferences.load();MODELS.request_refresh(selected,[mid]);USAGE.request_refresh(selected,[mid],force=True)
    return JSONResponse({'connections':await roster.connections(selected),'model_catalog':MODELS.snapshot(selected),'account_usage':USAGE.snapshot(selected)})


async def member_models(request):
    selected=preferences.load();mid=request.path_params['mid'];roster.agent(selected,mid)
    await asyncio.gather(*MODELS.request_refresh(selected,[mid]))
    return JSONResponse(MODELS.snapshot(selected)['agents'][mid]['models'])


async def login(request):
    return JSONResponse(await LOGIN.start(request.path_params['provider']))


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
    selected=preferences.load();ROOMS.seed(value['id'],selected)
    STORE.set_policy(value['id'],selected['cooperation_policy'])
    return JSONResponse(value)


async def room(request):
    rid=request.path_params['rid']
    return JSONResponse({'room':STORE.room(rid),'messages':STORE.messages(rid),
        'policy':STORE.policy(rid,preferences.load()['cooperation_policy']),'room_settings':ROOMS.load(rid),
        'context':STORE.deliveries(rid),'budget_wait':WORK.budget_gate.snapshot(rid)})


async def policy(request):
    rid=request.path_params['rid'];STORE.room(rid);body=await request.json()
    value=STORE.set_policy(rid,body.get('text',''))
    STORE.message(rid,'system','合作方式已保存。'+('当前步骤结束后，监工会按新规则重新检查。' if rid in WORK.running else '下次监工协作将使用这些规则。'),phase='规则更新')
    return JSONResponse(value)


async def send(request):
    rid=request.path_params['rid'];body=await request.json()
    return await send_body(rid,body)


async def force_budget(request):
    rid=request.path_params['rid'];STORE.room(rid)
    if WORK.budget_gate.snapshot(rid):
        WORK.budget_gate.force(rid);return JSONResponse({'status':'resumed'})
    body=await request.json()
    if not isinstance(body,dict):raise ValueError('请求格式无效。')
    if not isinstance(body.get('text',''),str):raise ValueError('任务文本无效。')
    if not body.get('text','').strip():
        if not any(m['provider']=='you' for m in STORE.messages(rid)):raise ValueError('请先输入需要执行的任务。')
        body['text']='继续上次未完成任务，沿用此前批准的方案与允许路径。先核对已有文件、执行结果及恢复记录，从未完成处继续，不重复已完成操作。'
        body['mode']='workflow'
    body['force_budget']=True
    return await send_body(rid,body)


async def send_body(rid,body):
    if not isinstance(body,dict):raise ValueError('请求格式无效。')
    text=body.get('text','')
    if not isinstance(text,str) or not 1<=len(text.strip())<=24000: raise ValueError('请输入 1 至 24000 字符的需求。')
    mode=body.get('mode','discuss');target=body.get('target','all')
    if mode not in ['discuss','workflow']:
        raise ValueError('发送模式无效。')
    selected=ROOMS.load(rid)
    forced=body.get('force_budget',False)
    if type(forced) is not bool:raise ValueError('预算覆盖设置无效。')
    if forced:selected['_force_context_budget']=True
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
    if forced:STORE.message(rid,'system','用户选择本次协作超预算继续，仅跳过本地上下文软上限。',phase='本次预算覆盖')
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
    source=next((m for m in selected['agents'] if m['backend'] in ['deepseek','openai-compatible'] and (preferences.get_member_key(m) or m.get('no_auth'))),None)
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
    ROOMS.migrate(preferences.load())
    MODELS.request_refresh(preferences.load())
    USAGE.request_refresh(preferences.load())
    yield
    for rid in list(WORK.running): await WORK.stop(rid)
    await DEBUG.close()
    await MODELS.close()
    await USAGE.close()
    await LOGIN.close()


routes=[Route('/',lambda request:FileResponse(WEB/'index.html')),
    Route('/api/avatars',avatars),Route('/api/avatars/{mid}',avatars,methods=['POST']),
    Route('/api/rooms/{rid}/force-budget',force_budget,methods=['POST']),
    Route('/api/health',health),Route('/api/bootstrap',bootstrap),Route('/api/models',models,methods=['GET','POST']),
    Route('/api/usage',usage,methods=['GET','POST']),Route('/api/agents/{mid}/usage',usage,methods=['POST']),
    Route('/api/settings',settings,methods=['POST']),Route('/api/connections',connections),
    Route('/api/connection-state',connection_state),
    Route('/api/agents/{mid}',save_agent,methods=['POST']),
    Route('/api/agents/{mid}/key',member_key,methods=['POST']),Route('/api/agents/{mid}/models',member_models,methods=['POST']),
    Route('/api/agents/{mid}/debug',debug_agent,methods=['GET','POST']),
    Route('/api/debug/{jid}/events',debug_events),Route('/api/debug/{jid}/cancel',debug_cancel,methods=['POST']),
    Route('/api/team/{mid}',team_membership,methods=['POST']),
    Route('/api/members/{mid}',save_member,methods=['POST']),Route('/api/members/{mid}/remove',remove_member,methods=['POST']),
    Route('/api/members/{mid}/key',member_key,methods=['POST']),Route('/api/members/{mid}/models',member_models,methods=['POST']),
    Route('/api/login/{provider}',login,methods=['POST']),Route('/api/folder',folder,methods=['POST']),
    Route('/api/rooms',create_room,methods=['POST']),Route('/api/rooms/{rid}',room),
    Route('/api/rooms/{rid}/configuration',room_configuration,methods=['GET','POST']),
    Route('/api/rooms/{rid}/members/{mid}/membership',room_member,methods=['POST']),
    Route('/api/rooms/{rid}/members/{mid}',room_member,methods=['POST']),
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
    if request.url.path=='/' or request.url.path.startswith('/assets/'):
        response.headers['Cache-Control']='no-cache'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response

app.add_middleware(BaseHTTPMiddleware,dispatch=protect)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=PORT,log_level='warning',access_log=False)
