"""Provider-owned conversations with host-owned room delivery and read-only tools."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
import tomllib

import httpx
from openai import AsyncOpenAI
from api_protocol import is_gemini,gemini_request,gemini_response,GEMINI_URL
from vertex_protocol import is_vertex,endpoint as vertex_endpoint,request as vertex_request,response as vertex_response
from context import SYSTEM,EXECUTOR_SYSTEM,TOOL_SCHEMAS
from usage import record as record_usage
from collaboration_log import call_for,emit


def tools_for(turn):
    return [{'type':'function','function':t} for t in turn.schemas()]


async def api_conversation(turn,prompt,key,base_url,on_text,effort=None):
    call_for(turn,getattr(turn,'source',{}).get('backend','openai-compatible'))
    native=turn.state['native'];messages=native.setdefault('messages',[
        {'role':'system','content':turn.instructions()}])
    messages.append({'role':'user','content':prompt});turn.save()
    tools=tools_for(turn);usage={}
    from transport_log import async_hooks
    def trace(kind,data):
        if kind=='http_request':turn.audit_request_ref=data['request_ref']
        emit(turn,kind,data)
    async with AsyncOpenAI(api_key=key,base_url=base_url,timeout=180,max_retries=0) as client, httpx.AsyncClient(timeout=180) as http:
        for transport in [getattr(client,'_client',None),http]:
            hooks=getattr(transport,'event_hooks',None)
            if isinstance(hooks,dict):
                for kind,callbacks in async_hooks(trace).items():hooks.setdefault(kind,[]).extend(callbacks)
        for _ in range(turn.budget.get('tool_rounds',16)):
            from context import estimate
            from calibration import estimate_v2
            turn.last_full_estimate=estimate_v2({'messages':messages,'tools':tools})
            estimated=turn.estimate({'messages':messages,'tools':tools}) if hasattr(turn,'estimate') else estimate({'messages':messages,'tools':tools})
            turn.last_request_estimate=estimated
            if hasattr(turn,'check_budget'):await turn.check_budget(estimated,'发送模型请求')
            elif estimated>turn.input_budget:raise RuntimeError('本轮上下文超过本地预算。')
            from prefix_observer import observe
            observe(turn,messages,tools)
            turn.audit_request_ref=None
            if is_gemini(base_url) or is_vertex(base_url):
                # Keep every model-generated step, including opaque signatures, unchanged.
                raw_usage={};status='failed'
                try:
                    vertex=is_vertex(base_url)
                    url=vertex_endpoint(base_url,turn.model) if vertex else GEMINI_URL
                    from vertex_protocol import headers as vertex_headers
                    headers=vertex_headers(base_url,key) if vertex else {'x-goog-api-key':key}
                    payload=vertex_request(turn.model,messages,tools,turn.budget['output'],effort) if vertex else gemini_request(turn.model,messages,tools,turn.budget['output'])
                    response=await http.post(url,headers=headers,json=payload)
                    if response.is_error:raise RuntimeError('Gemini 原生 API 请求失败（HTTP '+str(response.status_code)+'）。')
                    data=response.json()
                    emit(turn,'api_response',{'request_ref':turn.audit_request_ref,'body':data})
                    raw=vertex_response(data) if vertex else gemini_response(data)
                    raw_usage=raw.get('gemini_usage',{});message=raw['choices'][0]['message'];usage=raw.get('usage',{});status='complete'
                except asyncio.CancelledError:status='interrupted';raise
                finally:record_usage(turn,'gemini',raw_usage,status,request_ref=turn.audit_request_ref)
                on_text(message.get('content',''))
            else:
                text='';reasoning='';calls={};finish=None
                kwargs={'model':turn.model,'messages':messages,'stream':True,'max_tokens':turn.budget['output'],
                    'stream_options':{'include_usage':True}}
                if tools:kwargs['tools']=tools
                if effort is not None:kwargs['reasoning_effort']=effort
                usage={};status='failed'
                try:
                    stream=await client.chat.completions.create(**kwargs)
                    async for chunk in stream:
                        if chunk.usage:usage=chunk.usage.model_dump()
                        if not chunk.choices:continue
                        choice=chunk.choices[0];delta=choice.delta
                        if choice.finish_reason:finish=choice.finish_reason
                        text+=delta.content or '';reasoning+=getattr(delta,'reasoning_content','') or ''
                        for c in delta.tool_calls or []:
                            current=calls.setdefault(c.index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                            if c.id:current['id']+=c.id
                            if c.function:
                                current['function']['name']+=c.function.name or ''
                                current['function']['arguments']+=c.function.arguments or ''
                        if delta.content:on_text(text)
                    if finish not in ['stop','tool_calls']:raise RuntimeError('模型输出未完整结束：'+str(finish))
                    status='complete'
                except asyncio.CancelledError:status='interrupted';raise
                finally:
                    emit(turn,'api_output',{'request_ref':turn.audit_request_ref,'status':status,'content':text,'reasoning_content':reasoning,'tool_calls':calls,'finish_reason':finish})
                    record_usage(turn,'openai_compatible',usage,status,request_ref=turn.audit_request_ref)
                message={'role':'assistant','content':text}
                if reasoning:message['reasoning_content']=reasoning
                if calls:message['tool_calls']=[calls[i] for i in sorted(calls)]
            messages.append(message);turn.save()
            if not message.get('tool_calls'):
                text=message.get('content','')
                if not text.strip():raise RuntimeError('模型没有返回可见答复。')
                return text,usage
            for c in message['tool_calls']:
                try:
                    args=json.loads(c['function']['arguments'])
                    result=turn.read(c['function']['name'],args)
                    turn.store.event(turn.rid,{'type':'activity','provider':turn.identity,'text':'只读工具 · '+c['function']['name']})
                except (ValueError,TypeError,KeyError) as e:result={'error':str(e)}
                messages.append({'role':'tool','tool_call_id':c['id'],'content':json.dumps(result,ensure_ascii=False)})
            turn.save()
    raise RuntimeError('已达到本轮群消息读取上限，未自动宣称完成。')


class CodexRPC:
    def __init__(self,proc,turn,on_text):
        self.proc,self.turn,self.on_text=proc,turn,on_text;self.identifier=0;self.text='';self.done=None;self.usage={};self.futures={};self.compacted=asyncio.Event()
        self.request_active=False;self.usage_bases={};self.usage_measured=False

    async def send(self,data):
        emit(self.turn,'cli_stdin',data)
        self.proc.stdin.write((json.dumps(data,ensure_ascii=False)+'\n').encode());await self.proc.stdin.drain()

    async def request(self,method,params=None):
        self.identifier+=1;identifier=self.identifier
        future=asyncio.get_running_loop().create_future();self.futures[identifier]=future
        await self.send({'id':identifier,'method':method,'params':params})
        return await asyncio.wait_for(future,60)

    async def consume(self):
        try:
            while line:=await self.proc.stdout.readline():
                emit(self.turn,'cli_stdout',{'line':line.decode('utf-8',errors='replace')})
                try:value=json.loads(line)
                except ValueError:continue
                method=value.get('method');p=value.get('params',{})
                if method and 'id' in value:
                    if method=='item/tool/call':
                        try:
                            if p['tool'].startswith('project_') and self.turn.project_tools is not None:
                                operation=asyncio.create_task(asyncio.to_thread(self.turn.project_tools.execute,p['tool'],p['arguments']))
                                try:result=await asyncio.shield(operation)
                                except asyncio.CancelledError:
                                    await operation;raise
                                self.turn.store.event(self.turn.rid,{'type':'activity','provider':self.turn.identity,'text':'契约文件工具 · '+p['tool']})
                            else:result=self.turn.read(p['tool'],p['arguments'])
                            success=True
                        except (ValueError,TypeError,KeyError) as e:result={'error':str(e)};success=False
                        await self.send({'id':value['id'],'result':{'success':success,
                            'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)}]}})
                    else:
                        # No interactive permission escalation and no extra apps/user input.
                        await self.send({'id':value['id'],'error':{'code':-32601,'message':'Host did not authorize this request'}})
                elif 'id' in value and value['id'] in self.futures:
                    f=self.futures.pop(value['id'])
                    if f.done():continue
                    if 'error' in value:f.set_exception(RuntimeError('ChatGPT 会话请求失败：'+str(value['error'].get('message',''))[:250]))
                    else:f.set_result(value.get('result',{}))
                elif method=='item/agentMessage/delta':
                    self.text+=p.get('delta','');self.on_text(self.text)
                elif method=='item/completed':
                    item=p.get('item',{})
                    if item.get('type')=='agentMessage' and item.get('text'):
                        self.text=item['text'];self.on_text(self.text)
                    elif item.get('type') in ['contextCompaction','compaction']:
                        self.compacted.set()
                        self.turn.store.event(self.turn.rid,{'type':'activity','provider':self.turn.identity,'text':'原生上下文压缩已完成'})
                elif method=='thread/tokenUsage/updated':
                    self.usage=p.get('tokenUsage',{})
                    total=self.usage.get('total',{})
                    if self.request_active and isinstance(total.get('inputTokens'),int):
                        previous=self.turn.state['native'].get('usage_total') or {}
                        ref=str(p.get('threadId') or self.turn.state['native'].get('thread_id',''))+':input-total:'+str(total['inputTokens'])
                        if ref not in self.usage_bases and previous.get('inputTokens')==total['inputTokens']:
                            # Resume may replay the last counter before the new request starts.
                            continue
                        if ref not in self.usage_bases:self.usage_bases[ref]=self.turn.state['native'].get('usage_total')
                        self.turn.state['native']['usage_total']=self.usage_bases[ref]
                        record_usage(self.turn,'codex',self.usage,request_ref=ref)
                        self.usage_measured=True
                elif method=='turn/completed':
                    self.done=p.get('turn',{})
            if self.done is None:raise RuntimeError('ChatGPT 会话服务提前退出。')
        except BaseException as e:
            for f in self.futures.values():
                if not f.done():f.set_exception(e if isinstance(e,Exception) else RuntimeError('会话已停止'))
            raise


async def codex_conversation(turn,prompt,root,effort,on_text):
    call_for(turn,'chatgpt')
    from engines import find_cli,environment,HIDDEN,kill_tree,member_git_environment
    binary=find_cli('codex')
    if not binary:raise RuntimeError('未安装 ChatGPT CLI。')
    trust=member_git_environment(root)
    proc=await asyncio.create_subprocess_exec(binary,'app-server','--listen','stdio://',
        '-c','mcp_servers={}',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,env={**environment(),**trust},cwd=root,creationflags=HIDDEN,limit=8_000_000)
    emit(turn,'cli_process',{'program':binary,'arguments':['app-server','--listen','stdio://','-c','mcp_servers={}'],'cwd':str(root)})
    stderr_reader=asyncio.create_task(consume_stderr(proc,turn))
    rpc=CodexRPC(proc,turn,on_text);reader=asyncio.create_task(rpc.consume())
    try:
        await rpc.request('initialize',{'clientInfo':{'name':'agent_group_room','version':'0.10.0'},'capabilities':{'experimentalApi':True}})
        await rpc.send({'method':'initialized'})
        config={'mcp_servers':{},
            'model_auto_compact_token_limit':max(1024,int(turn.budget['soft']*turn.settings.get('context',{}).get('maintain_at',0.6)))}
        if effort is not None:config['model_reasoning_effort']=effort
        # Disable inherited MCP connections explicitly; an empty merged map is insufficient.
        for path in [Path.home()/'.codex/config.toml',*[p/'.codex/config.toml' for p in [root,*root.parents]]]:
            try:
                for name in tomllib.loads(path.read_text(encoding='utf-8')).get('mcp_servers',{}):
                    config['mcp_servers'][name]={'enabled':False}
            except (OSError,ValueError):pass
        if turn.coding:config['windows']={'sandbox':'unelevated'}
        # Codex shells (incl. its Windows sandbox) trust exactly this project for git.
        if trust:config['shell_environment_policy']={'set':trust}
        params={'model':turn.model,'cwd':str(root),'approvalPolicy':'never',
            'sandbox':'read-only','config':config}
        native=turn.state['native']
        if native.get('thread_id'):
            await rpc.request('thread/resume',{**params,'threadId':native['thread_id']})
        else:
            specs=[{'type':'function','name':t['name'],'description':t['description'],'inputSchema':t['parameters']} for t in turn.schemas()]
            if turn.project_tools:specs+=turn.project_tools.specs()
            data=await rpc.request('thread/start',{**params,'ephemeral':False,'dynamicTools':specs,
                'selectedCapabilityRoots':[],
                'developerInstructions':turn.instructions()+
                    (' 编码必须使用 project_Read/Glob/Grep/Write/Edit；宿主逐次核验契约允许路径；查看仓库状态、差异和历史用只读 project_Git。不要用内置 apply_patch、shell 或 exec 写文件。' if turn.project_tools else '')})
            native['thread_id']=data['thread']['id'];turn.save()
        if turn.state.get('needs_compaction'):
            await rpc.request('thread/compact/start',{'threadId':native['thread_id']})
            await asyncio.wait_for(rpc.compacted.wait(),120)
            rpc.done=None;turn.state['needs_compaction']=False;turn.save()
            turn.state['compacted']=turn.state['delivered'].copy();turn.save()
        # Original peer text is user-supplied group data, never forged assistant history.
        rpc.usage={};rpc.request_active=True;request_started=True
        await rpc.request('turn/start',{'threadId':native['thread_id'],
            'input':[{'type':'text','text':prompt+('\n本轮写入使用 project_Write / project_Edit，读取用 project_Read。禁止内置 apply_patch 或命令写入。' if turn.project_tools else ''),'text_elements':[]}],**({'effort':effort} if effort is not None else {}),
            'cwd':str(root),'approvalPolicy':'never',
            'sandboxPolicy':{'type':'readOnly','networkAccess':False}})
        deadline=time.monotonic()+900
        while rpc.done is None:
            if reader.done():await reader
            if time.monotonic()>deadline:raise TimeoutError('ChatGPT 本轮超时。')
            await asyncio.sleep(0.05)
        if rpc.done.get('status')!='completed':raise RuntimeError('ChatGPT 本轮未完成：'+str(rpc.done.get('status')))
        if not rpc.text.strip():raise RuntimeError('ChatGPT 未返回可见答复。')
        native['usage']=rpc.usage;turn.save()
        return rpc.text,rpc.usage
    finally:
        if locals().get('request_started'):
            status='complete' if rpc.done and rpc.done.get('status')=='completed' else 'interrupted' if asyncio.current_task().cancelling() else 'failed'
            if not getattr(rpc,'usage_measured',False):
                raw=rpc.usage
                if raw.get('total')==turn.state['native'].get('usage_total'):raw={}
                record_usage(turn,'codex',raw,status)
            elif status!='complete':record_usage(turn,'codex',{},status)
        await kill_tree(proc);reader.cancel()
        await asyncio.gather(reader,return_exceptions=True)
        await asyncio.gather(stderr_reader,return_exceptions=True)


async def claude_conversation(turn,prompt,root,effort,on_text):
    call_for(turn,'claude')
    from engines import find_cli,environment,HIDDEN,kill_tree,member_git_environment
    binary=find_cli('claude')
    if not binary:raise RuntimeError('未安装 Claude CLI。')
    native=turn.state['native'];existing=native.get('session_id');sid=existing or str(uuid.uuid4())
    native['session_id']=sid;turn.save()
    with tempfile.TemporaryDirectory(prefix='agent-room-session-') as folder:
        mcp={}
        if turn.schemas():
            database=str(Path(turn.store.db.execute('PRAGMA database_list').fetchone()[2]).resolve())
            mcp={'room':{'command':sys.executable,'args':[str(Path(__file__).with_name('room_tools.py'))],
                'env':{'ROOM_DATABASE':database,'ROOM_ID':turn.rid,'ROOM_SESSION':turn.key}}}
        path=Path(folder)/'mcp.json';path.write_text(json.dumps({'mcpServers':mcp}),encoding='utf-8')
        args=[binary,'-p','--model',turn.model,'--restricted',
            '--autocompact',str(max(100000,min(1000000,round(turn.budget['soft']*turn.settings.get('context',{}).get('maintain_at',0.6))))),
            '--permission-mode','acceptEdits' if turn.coding else 'plan','--permission-prompts','none',
            '--tools','Read,Glob,Grep,Write,Edit' if turn.coding else '',
            '--strict-mcp-config','--mcp-config',str(path),'--output-format','stream-json','--verbose','--include-partial-messages',
            '--system-prompt-snapshot','on','--append-system-prompt',turn.instructions()]
        if effort is not None:args+=['--effort',effort]
        if turn.schemas():args+=['--allowedTools',','.join('mcp__room__'+t['name'] for t in turn.schemas())]
        args+=['--resume' if existing else '--session-id',sid]
        env={**environment(),**member_git_environment(root)};env['CLAUDE_CODE_AUTO_COMPACT_WINDOW']=str(turn.budget['soft'])
        env['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE']=str(round(turn.settings.get('context',{}).get('maintain_at',0.6)*100))
        proc=await asyncio.create_subprocess_exec(*args,cwd=root,env=env,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=HIDDEN,limit=8_000_000)
        emit(turn,'cli_process',{'arguments':args,'cwd':str(root),'mcp_configuration':{'mcpServers':mcp}})
        stderr_reader=asyncio.create_task(consume_stderr(proc,turn))
        text='';usage={};result=None;request_usage={};closed_requests=set();active_request=None
        async def consume():
            nonlocal text,usage,result,active_request
            proc.stdin.write(prompt.encode());await proc.stdin.drain();proc.stdin.close()
            emit(turn,'cli_stdin',{'text':prompt})
            while line:=await proc.stdout.readline():
                emit(turn,'cli_stdout',{'line':line.decode('utf-8',errors='replace')})
                try:event=json.loads(line)
                except ValueError:continue
                kind=event.get('type')
                if kind=='stream_event':
                    wire=event.get('event',{});delta=wire.get('delta',{})
                    if wire.get('type')=='message_start':
                        message=wire.get('message',{});active_request=message.get('id')
                        if active_request:request_usage.setdefault(active_request,{}).update(message.get('usage',{}))
                    elif wire.get('type')=='message_delta' and active_request:
                        request_usage.setdefault(active_request,{}).update(wire.get('usage',{}))
                    elif wire.get('type')=='message_stop' and active_request:closed_requests.add(active_request)
                    if delta.get('type')=='text_delta':text+=delta.get('text','');on_text(text)
                elif kind=='result':
                    result=event;usage=event.get('usage',{});text=event.get('result') or text;on_text(text)
                elif kind=='assistant' and isinstance(event.get('message'),dict):
                    message=event['message']
                    if message.get('usage'):
                        ref=message.get('id') or str(len(request_usage));request_usage.setdefault(ref,{}).update(message['usage'])
                        if message.get('stop_reason'):closed_requests.add(ref)
                elif kind=='system' and event.get('subtype')=='compact_boundary':
                    turn.state['compacted']=turn.state['delivered'].copy();turn.state['needs_compaction']=False;turn.save()
                    turn.store.event(turn.rid,{'type':'activity','provider':turn.identity,'text':'Claude 原生上下文已压缩'})
            await proc.wait()
        try:
            await asyncio.wait_for(consume(),900)
            if proc.returncode or result is None or result.get('is_error'):
                raise RuntimeError('Claude 会话调用未完成，请检查登录、模型权限与会话状态。')
            if not text.strip():raise RuntimeError('Claude 未返回可见答复。')
            native['usage']=usage;turn.save();return text,usage
        finally:
            status='complete' if result and not result.get('is_error') else 'interrupted' if asyncio.current_task().cancelling() else 'failed'
            if request_usage:
                model_usage=(result or {}).get('modelUsage',{})
                reported=model_usage.get(turn.model,next(iter(model_usage.values()),{}) if len(model_usage)==1 else {})
                for ref,raw in request_usage.items():record_usage(turn,'claude',{**raw,'context_window':reported.get('contextWindow')},'complete' if ref in closed_requests else status,request_ref=sid+':'+ref)
            else:record_usage(turn,'claude',usage,status)
            await kill_tree(proc)
            await asyncio.gather(stderr_reader,return_exceptions=True)


async def consume_stderr(proc,turn):
    stream=getattr(proc,'stderr',None)
    if stream is None:return
    while chunk:=await stream.read(16384):
        import base64
        emit(turn,'cli_stderr',{'base64':base64.b64encode(chunk).decode('ascii')})


async def reply(turn,prompt,root,effort,source,publish=True):
    from engines import preferences
    from model_catalog import effective_effort
    effort=await effective_effort(source,turn.model,effort)
    from budget_control import ContextBudgetExceeded
    try:content=turn.compose(prompt)
    except ContextBudgetExceeded as exceeded:
        gate=getattr(turn.store,'budget_gate',None)
        if gate is None:raise
        await gate.wait(turn,exceeded)
        content=turn.compose(prompt)
    mid=turn.store.message(turn.rid,turn.identity,model=turn.model,phase=turn.phase,status='streaming') if publish else None
    audit=call_for(turn,source['backend'])
    emit(turn,'host_input',{'prompt':content,'instructions':turn.instructions(),'tools':turn.schemas(),'published':publish,'message_id':mid})
    current='';last=0;published=False
    def output(text):
        nonlocal current,last
        current=text
        if publish and time.monotonic()-last>=0.12:
            turn.store.update(mid,text);last=time.monotonic()
    try:
        backend=source['backend']
        if backend=='chatgpt':answer,usage=await codex_conversation(turn,content,root,effort,output)
        elif backend=='claude':answer,usage=await claude_conversation(turn,content,root,effort,output)
        else:
            from google_billing import credential
            key=await credential(source)
            if not key:raise ValueError(source['name']+' 尚未配置密钥。')
            answer,usage=await api_conversation(turn,content,key,source['base_url'],output,effort)
        if publish:
            from member_summary import extract
            summary=extract(answer,turn.settings) if turn.role=='discussion' else None
            turn.store.update(mid,answer,'complete',summary=summary)
        else:turn.store.archive_raw(turn.rid,turn.identity,answer)
        public_answer=answer
        if not publish and turn.role=='supervisor':
            from workflows import json_reply
            try:public_answer=json_reply(answer).get('public_message','')
            except ValueError:public_answer=''
        published=True;turn.complete(public_answer,usage)
        turn.store.event(turn.rid,{'type':'usage','provider':turn.identity,'usage':usage})
        if audit:audit.finish('complete',answer,mid)
        return answer
    except BaseException as e:
        if not published:
            status='interrupted' if isinstance(e,asyncio.CancelledError) else 'error'
            if publish:turn.store.update(mid,current or '本轮未完成；会话状态与原文已保留。',status)
            else:turn.store.archive_raw(turn.rid,turn.identity,current,status)
        turn.record('interrupted' if isinstance(e,asyncio.CancelledError) else 'failed')
        if audit:audit.finish('interrupted' if isinstance(e,asyncio.CancelledError) else 'failed',current,mid)
        if isinstance(e,asyncio.CancelledError):raise
        # Never propagate HTTP request objects, headers or raw credential-bearing errors.
        if isinstance(e,(RuntimeError,ValueError,TimeoutError)):raise
        raise RuntimeError(source['name']+' 连接失败，请检查登录、密钥、模型与额度。') from None
