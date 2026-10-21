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
from context import SYSTEM,EXECUTOR_SYSTEM,TOOL_SCHEMAS


def tools_for(turn):
    return [{'type':'function','function':t} for t in TOOL_SCHEMAS] if turn.tools else []


async def api_conversation(turn,prompt,key,base_url,on_text,effort=None):
    native=turn.state['native'];messages=native.setdefault('messages',[
        {'role':'system','content':EXECUTOR_SYSTEM if turn.role=='executor' else SYSTEM}])
    messages.append({'role':'user','content':prompt});turn.save()
    tools=tools_for(turn);usage={}
    async with AsyncOpenAI(api_key=key,base_url=base_url,timeout=180,max_retries=0) as client, httpx.AsyncClient(timeout=180) as http:
        for _ in range(16):
            from context import estimate
            if estimate({'messages':messages,'tools':tools})>turn.input_budget:
                raise RuntimeError('本轮上下文达到保守预算，原文与会话已保留；请缩小资料范围或开始新一轮。')
            if is_gemini(base_url):
                # Keep every model-generated step, including opaque signatures, unchanged.
                response=await http.post(GEMINI_URL,headers={'x-goog-api-key':key},
                    json=gemini_request(turn.model,messages,tools,turn.budget['output']))
                if response.is_error:raise RuntimeError('Gemini 原生 API 请求失败（HTTP '+str(response.status_code)+'）。')
                raw=gemini_response(response.json());message=raw['choices'][0]['message'];usage=raw.get('usage',{})
                on_text(message.get('content',''))
            else:
                text='';reasoning='';calls={};finish=None
                kwargs={'model':turn.model,'messages':messages,'stream':True,'max_tokens':turn.budget['output'],
                    'stream_options':{'include_usage':True}}
                if tools:kwargs['tools']=tools
                if effort is not None:kwargs['reasoning_effort']=effort
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
                    turn.store.event(turn.rid,{'type':'activity','provider':turn.identity,'text':'读取群原文 · '+c['function']['name']})
                except (ValueError,TypeError,KeyError) as e:result={'error':str(e)}
                messages.append({'role':'tool','tool_call_id':c['id'],'content':json.dumps(result,ensure_ascii=False)})
            turn.save()
    raise RuntimeError('已达到本轮群消息读取上限，未自动宣称完成。')


class CodexRPC:
    def __init__(self,proc,turn,on_text):
        self.proc,self.turn,self.on_text=proc,turn,on_text;self.identifier=0;self.text='';self.done=None;self.usage={};self.futures={};self.compacted=asyncio.Event()

    async def send(self,data):
        self.proc.stdin.write((json.dumps(data,ensure_ascii=False)+'\n').encode());await self.proc.stdin.drain()

    async def request(self,method,params=None):
        self.identifier+=1;identifier=self.identifier
        future=asyncio.get_running_loop().create_future();self.futures[identifier]=future
        await self.send({'id':identifier,'method':method,'params':params})
        return await asyncio.wait_for(future,60)

    async def consume(self):
        try:
            while line:=await self.proc.stdout.readline():
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
                elif method=='turn/completed':
                    self.done=p.get('turn',{})
            if self.done is None:raise RuntimeError('ChatGPT 会话服务提前退出。')
        except BaseException as e:
            for f in self.futures.values():
                if not f.done():f.set_exception(e if isinstance(e,Exception) else RuntimeError('会话已停止'))
            raise


async def codex_conversation(turn,prompt,root,effort,on_text):
    from engines import find_cli,environment,HIDDEN,kill_tree,member_git_environment
    binary=find_cli('codex')
    if not binary:raise RuntimeError('未安装 ChatGPT CLI。')
    trust=member_git_environment(root)
    proc=await asyncio.create_subprocess_exec(binary,'app-server','--listen','stdio://',
        '-c','mcp_servers={}',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,env={**environment(),**trust},cwd=root,creationflags=HIDDEN,limit=8_000_000)
    rpc=CodexRPC(proc,turn,on_text);reader=asyncio.create_task(rpc.consume())
    try:
        await rpc.request('initialize',{'clientInfo':{'name':'agent_group_room','version':'0.10.0'},'capabilities':{'experimentalApi':True}})
        await rpc.send({'method':'initialized'})
        config={'mcp_servers':{},
            'model_auto_compact_token_limit':max(16000,int(turn.input_budget*0.8))}
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
            specs=[{'type':'function','name':t['name'],'description':t['description'],'inputSchema':t['parameters']} for t in TOOL_SCHEMAS] if turn.tools else []
            if turn.project_tools:specs+=turn.project_tools.specs()
            data=await rpc.request('thread/start',{**params,'ephemeral':False,'dynamicTools':specs,
                'selectedCapabilityRoots':[],
                'developerInstructions':(EXECUTOR_SYSTEM if turn.role=='executor' else SYSTEM)+
                    (' 编码必须使用 project_Read/Glob/Grep/Write/Edit；宿主逐次核验契约允许路径；查看仓库状态、差异和历史用只读 project_Git。不要用内置 apply_patch、shell 或 exec 写文件。' if turn.project_tools else '')})
            native['thread_id']=data['thread']['id'];turn.save()
        if turn.state.get('needs_compaction'):
            await rpc.request('thread/compact/start',{'threadId':native['thread_id']})
            await asyncio.wait_for(rpc.compacted.wait(),120)
            rpc.done=None;turn.state['needs_compaction']=False;turn.save()
        # Original peer text is user-supplied group data, never forged assistant history.
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
        await kill_tree(proc);reader.cancel()
        await asyncio.gather(reader,return_exceptions=True)


async def claude_conversation(turn,prompt,root,effort,on_text):
    from engines import find_cli,environment,HIDDEN,kill_tree,member_git_environment
    binary=find_cli('claude')
    if not binary:raise RuntimeError('未安装 Claude CLI。')
    native=turn.state['native'];existing=native.get('session_id');sid=existing or str(uuid.uuid4())
    native['session_id']=sid;turn.save()
    with tempfile.TemporaryDirectory(prefix='agent-room-session-') as folder:
        mcp={}
        if turn.tools:
            database=str(Path(turn.store.db.execute('PRAGMA database_list').fetchone()[2]).resolve())
            mcp={'room':{'command':sys.executable,'args':[str(Path(__file__).with_name('room_tools.py'))],
                'env':{'ROOM_DATABASE':database,'ROOM_ID':turn.rid,'ROOM_SESSION':turn.key}}}
        path=Path(folder)/'mcp.json';path.write_text(json.dumps({'mcpServers':mcp}),encoding='utf-8')
        args=[binary,'-p','--model',turn.model,'--restricted',
            '--permission-mode','acceptEdits' if turn.coding else 'plan','--permission-prompts','none',
            '--tools','Read,Glob,Grep,Write,Edit' if turn.coding else '',
            '--strict-mcp-config','--mcp-config',str(path),'--output-format','stream-json','--verbose','--include-partial-messages',
            '--append-system-prompt',EXECUTOR_SYSTEM if turn.role=='executor' else SYSTEM]
        if effort is not None:args+=['--effort',effort]
        if turn.tools:args+=['--allowedTools',','.join('mcp__room__'+t['name'] for t in TOOL_SCHEMAS)]
        args+=['--resume' if existing else '--session-id',sid]
        env={**environment(),**member_git_environment(root)};env['CLAUDE_CODE_AUTO_COMPACT_WINDOW']=str(max(100000,turn.budget['soft']))
        env['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE']='80'
        proc=await asyncio.create_subprocess_exec(*args,cwd=root,env=env,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,creationflags=HIDDEN,limit=8_000_000)
        text='';usage={};result=None
        async def consume():
            nonlocal text,usage,result
            proc.stdin.write(prompt.encode());await proc.stdin.drain();proc.stdin.close()
            while line:=await proc.stdout.readline():
                try:event=json.loads(line)
                except ValueError:continue
                kind=event.get('type')
                if kind=='stream_event':
                    delta=event.get('event',{}).get('delta',{})
                    if delta.get('type')=='text_delta':text+=delta.get('text','');on_text(text)
                elif kind=='result':
                    result=event;usage=event.get('usage',{});text=event.get('result') or text;on_text(text)
                elif kind=='system' and event.get('subtype')=='compact_boundary':
                    turn.store.event(turn.rid,{'type':'activity','provider':turn.identity,'text':'Claude 原生上下文已压缩'})
            await proc.wait()
        try:
            await asyncio.wait_for(consume(),900)
            if proc.returncode or result is None or result.get('is_error'):
                raise RuntimeError('Claude 会话调用未完成，请检查登录、模型权限与会话状态。')
            if not text.strip():raise RuntimeError('Claude 未返回可见答复。')
            native['usage']=usage;turn.save();return text,usage
        finally:await kill_tree(proc)


async def reply(turn,prompt,root,effort,source):
    from engines import preferences
    from model_catalog import effective_effort
    effort=await effective_effort(source,turn.model,effort)
    content=turn.compose(prompt)
    mid=turn.store.message(turn.rid,turn.identity,model=turn.model,phase=turn.phase,status='streaming')
    current='';last=0;published=False
    def output(text):
        nonlocal current,last
        current=text
        if time.monotonic()-last>=0.12:
            turn.store.update(mid,text);last=time.monotonic()
    try:
        backend=source['backend']
        if backend=='chatgpt':answer,usage=await codex_conversation(turn,content,root,effort,output)
        elif backend=='claude':answer,usage=await claude_conversation(turn,content,root,effort,output)
        else:
            key=preferences.get_member_key(source) or ('local-no-auth' if source.get('no_auth') else '')
            if not key:raise ValueError(source['name']+' 尚未配置密钥。')
            answer,usage=await api_conversation(turn,content,key,source['base_url'],output,effort)
        turn.store.update(mid,answer,'complete');published=True;turn.complete(answer,usage)
        turn.store.event(turn.rid,{'type':'usage','provider':turn.identity,'usage':usage})
        return answer
    except BaseException as e:
        if not published:turn.store.update(mid,current or '本轮未完成；会话状态与原文已保留。','interrupted' if isinstance(e,asyncio.CancelledError) else 'error')
        turn.record('interrupted' if isinstance(e,asyncio.CancelledError) else 'failed')
        if isinstance(e,asyncio.CancelledError):raise
        # Never propagate HTTP request objects, headers or raw credential-bearing errors.
        if isinstance(e,(RuntimeError,ValueError,TimeoutError)):raise
        raise RuntimeError(source['name']+' 连接失败，请检查登录、密钥、模型与额度。') from None
